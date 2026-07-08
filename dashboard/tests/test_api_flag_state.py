"""Integration pin: /api/v1/flags/state.

L11 audit item (2026-07-08). Endpoint aggregates the state of every
catalogued GENLAB_* env flag + surfaces case-sensitivity warnings.

Pins:
  1. Endpoint returns 200 + JSON success shape
  2. Every catalog entry appears in the response (no silent drops)
  3. Unset env var → status="dormant"
  4. Truthy value ("1", "true", "yes", "on") → status="active"
  5. Falsy value ("0", "false", "") → status="dormant"
  6. Mis-set value (e.g. "TRUE" if reader only accepts "true", or
     random junk) → status="mis_set" + warning attached
  7. Disable-polarity flag with truthy value → status="disabled_by_flag"
     (renamed from "active" so frontend can show a red badge)
  8. Summary counters match category totals
  9. Payload never leaks raw values >32 chars (defensive redaction)
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
import server.review_server as review_server_module
from server.api.flag_state import _FLAG_CATALOG
from server.review_server import app


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", False)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Every test starts with all catalogued flags UNSET.

    Otherwise, the .env values leaking through from the dev host
    would make assertion behavior depend on the developer's shell.
    """
    for spec in _FLAG_CATALOG:
        monkeypatch.delenv(spec["name"], raising=False)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class TestGetState:
    def test_endpoint_returns_success_shape(self, client):
        resp = client.get("/api/v1/flags/state")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "success"
        assert "flags" in body["data"]
        assert "summary" in body["data"]

    def test_every_catalog_entry_is_returned(self, client):
        """Prevents a silent drop from `_summarize_*` in the endpoint —
        every catalog entry must round-trip to the client, or the card
        would show fewer flags than the catalog defines."""
        resp_names = {
            f["name"] for f in client.get("/api/v1/flags/state").get_json()["data"]["flags"]
        }
        catalog_names = {spec["name"] for spec in _FLAG_CATALOG}
        assert resp_names == catalog_names

    def test_unset_flag_is_dormant(self, client):
        # All flags start unset via the autouse _clean_env fixture.
        data = client.get("/api/v1/flags/state").get_json()["data"]
        first = data["flags"][0]
        assert first["status"] == "dormant"
        assert first["raw_value"] is None
        assert first["warning"] is None

    @pytest.mark.parametrize("truthy", ["1", "true", "yes", "on", "TRUE", "Yes"])
    def test_truthy_values_are_active(self, client, monkeypatch, truthy):
        """Case-insensitive matching for truthy values is deliberate:
        even though some individual readers ARE strict-'1' only, the
        endpoint reports 'active' on any conventional truthy and lets
        the mis-set check flag the strict-reader case separately."""
        monkeypatch.setenv("GENLAB_BAYESIAN_GATE_ENABLED", truthy)
        data = client.get("/api/v1/flags/state").get_json()["data"]
        target = next(
            f for f in data["flags"] if f["name"] == "GENLAB_BAYESIAN_GATE_ENABLED"
        )
        assert target["status"] == "active"

    @pytest.mark.parametrize("falsy", ["0", "false", "no", "off", ""])
    def test_falsy_values_are_dormant(self, client, monkeypatch, falsy):
        monkeypatch.setenv("GENLAB_BAYESIAN_GATE_ENABLED", falsy)
        data = client.get("/api/v1/flags/state").get_json()["data"]
        target = next(
            f for f in data["flags"] if f["name"] == "GENLAB_BAYESIAN_GATE_ENABLED"
        )
        assert target["status"] == "dormant"

    def test_junk_value_is_mis_set_with_warning(self, client, monkeypatch):
        """The H2 audit's motivation: an env value that neither
        conventional truthy nor falsy pattern-matches must surface
        as a warning so operator sees the silent-no-op class."""
        monkeypatch.setenv("GENLAB_BAYESIAN_GATE_ENABLED", "enable-this-plz")
        data = client.get("/api/v1/flags/state").get_json()["data"]
        target = next(
            f for f in data["flags"] if f["name"] == "GENLAB_BAYESIAN_GATE_ENABLED"
        )
        assert target["status"] == "mis_set"
        assert target["warning"] is not None
        assert "enable-this-plz" in target["warning"]

    def test_disable_polarity_flag_surfaces_disabled_by_flag(
        self, client, monkeypatch
    ):
        """The AUTO_APPROVE kill switch is polarity=disable — a truthy
        value means the feature is TURNED OFF. Locking in the semantic
        rename so a red badge appears instead of a green one."""
        monkeypatch.setenv("GENLAB_AUTO_APPROVE_DISABLED", "1")
        data = client.get("/api/v1/flags/state").get_json()["data"]
        target = next(
            f for f in data["flags"] if f["name"] == "GENLAB_AUTO_APPROVE_DISABLED"
        )
        assert target["status"] == "disabled_by_flag"
        assert target["polarity"] == "disable"

    def test_summary_counters_match_totals(self, client, monkeypatch):
        monkeypatch.setenv("GENLAB_BAYESIAN_GATE_ENABLED", "1")
        monkeypatch.setenv("GENLAB_CROSS_NICHE_TRANSFER_ENABLED", "1")
        monkeypatch.setenv("GENLAB_HOOK_CRITIC_ENABLED", "typo-value")
        summary = client.get("/api/v1/flags/state").get_json()["data"]["summary"]
        assert summary["total"] == len(_FLAG_CATALOG)
        assert summary["active"] == 2
        assert summary["mis_set"] == 1
        # dormant + active + mis_set = total (no double-count).
        assert (
            summary["active"] + summary["dormant"] + summary["mis_set"]
        ) == summary["total"]

    def test_never_leaks_long_raw_value(self, client, monkeypatch):
        """Defensive redaction — flags NEVER legitimately hold long
        strings, but if someone accidentally set an env var to a
        secret token the endpoint MUST NOT leak it. Locking in the
        32-char cutoff."""
        long_secret = "sk_live_" + "x" * 64  # 72 chars
        monkeypatch.setenv("GENLAB_BAYESIAN_GATE_ENABLED", long_secret)
        data = client.get("/api/v1/flags/state").get_json()["data"]
        target = next(
            f for f in data["flags"] if f["name"] == "GENLAB_BAYESIAN_GATE_ENABLED"
        )
        assert target["raw_value"] == "…"
        assert long_secret not in str(data)

    def test_group_field_present_and_expected_set(self, client):
        """Each entry declares a group so the frontend can render
        categorized rows. Assert the currently-defined groups don't
        drift silently."""
        groups = {
            f["group"]
            for f in client.get("/api/v1/flags/state").get_json()["data"]["flags"]
        }
        expected = {
            "learning",
            "intelligence",
            "content_quality",
            "monetization",
            "experimentation",
            "infrastructure",
        }
        assert groups == expected
