"""Tests for PR II — tier-transition detection (backend Phase 1).

Closes the last gap in the operator-leverage loop. The readiness
endpoint computes current tier per niche on every read; this
module compares to last-known tier from tier_history table,
records new transitions, and exposes them via
/api/v1/sponsorship/recent-transitions.

Pins:
  Detection logic (5):
    - first-ever observation creates transition with prev_tier=None
    - same tier as last known → no transition recorded
    - different tier → transition recorded with prev_tier set
    - no DSN → no-op (returns [])
    - empty current_per_niche → no-op (returns [])

  Read endpoint (4):
    - window_hours default 24 when missing
    - window_hours clamped to [1, 168]
    - response shape: {window_hours, transitions: [...]}
    - cold-start (empty table) → 200 + empty transitions list

  Concurrency tolerance (1):
    - Idempotency note: multiple concurrent writes create duplicate
      rows by design (no FOR UPDATE locking) — tested via pin
      that asserts the insert is unconditional INSERT (not
      INSERT ... ON CONFLICT)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import server.review_server as review_server_module
from server.review_server import app


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", False)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class TestDetectionLogic:
    """Pin record_tier_transitions: compute deltas from last-known to
    current, INSERT new transitions only."""

    def test_first_ever_observation_creates_transition_with_none_prev(self, monkeypatch):
        """Pin: niche with no prior tier_history row → transition
        recorded with prev_tier=None. The very-first transition.
        """
        monkeypatch.setenv("DATABASE_URL", "dbname=stub")
        # Last-known returns empty → niche has no history
        monkeypatch.setattr(
            "server.core.tier_transition_detector._read_last_known_tiers",
            lambda dsn, niche_ids: {},
        )
        inserted = []
        monkeypatch.setattr(
            "server.core.tier_transition_detector._insert_transitions",
            lambda dsn, ts: inserted.extend(ts),
        )

        from server.core.tier_transition_detector import record_tier_transitions

        result = record_tier_transitions(
            {"gaming": {"tier": "eligible_now", "nearest_threshold_days": 0}}
        )
        assert len(result) == 1
        assert result[0]["niche_id"] == "gaming"
        assert result[0]["tier"] == "eligible_now"
        assert result[0]["prev_tier"] is None  # First-ever
        assert inserted == result

    def test_same_tier_no_transition(self, monkeypatch):
        """Pin: when current tier matches last-known tier, NO row
        inserted. Detection is delta-only."""
        monkeypatch.setenv("DATABASE_URL", "dbname=stub")
        monkeypatch.setattr(
            "server.core.tier_transition_detector._read_last_known_tiers",
            lambda dsn, niche_ids: {"gaming": "within_2_months"},
        )
        inserted = []
        monkeypatch.setattr(
            "server.core.tier_transition_detector._insert_transitions",
            lambda dsn, ts: inserted.extend(ts),
        )

        from server.core.tier_transition_detector import record_tier_transitions

        result = record_tier_transitions(
            {"gaming": {"tier": "within_2_months", "nearest_threshold_days": 21}}
        )
        assert result == []
        assert inserted == []

    def test_different_tier_inserts_with_prev_tier(self, monkeypatch):
        """Pin: tier change → INSERT with prev_tier set to the prior
        value. Enables future queries like "show me niches that
        crossed INTO eligible_now in the last 7 days."""
        monkeypatch.setenv("DATABASE_URL", "dbname=stub")
        monkeypatch.setattr(
            "server.core.tier_transition_detector._read_last_known_tiers",
            lambda dsn, niche_ids: {"gaming": "within_2_months"},
        )
        inserted = []
        monkeypatch.setattr(
            "server.core.tier_transition_detector._insert_transitions",
            lambda dsn, ts: inserted.extend(ts),
        )

        from server.core.tier_transition_detector import record_tier_transitions

        result = record_tier_transitions(
            {"gaming": {"tier": "eligible_now", "nearest_threshold_days": 0}}
        )
        assert len(result) == 1
        assert result[0]["niche_id"] == "gaming"
        assert result[0]["tier"] == "eligible_now"
        assert result[0]["prev_tier"] == "within_2_months"

    def test_no_dsn_no_op(self, monkeypatch):
        """Pin: DATABASE_URL unset → record_tier_transitions returns
        empty list without raising. Cold-start safe — local dev
        without a Postgres connection still works."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        from server.core.tier_transition_detector import record_tier_transitions

        result = record_tier_transitions(
            {"gaming": {"tier": "eligible_now", "nearest_threshold_days": 0}}
        )
        assert result == []

    def test_empty_current_per_niche_no_op(self, monkeypatch):
        """Pin: empty current dict → returns [] immediately, no DB
        round-trip. Defends against calling with [] mid-startup."""
        monkeypatch.setenv("DATABASE_URL", "dbname=stub")
        from server.core.tier_transition_detector import record_tier_transitions

        result = record_tier_transitions({})
        assert result == []


class TestReadEndpoint:
    """Pins for /api/v1/sponsorship/recent-transitions."""

    def test_default_window_hours_24(self, client, monkeypatch):
        """Pin: missing window_hours query param → default 24h."""
        captured_window = []

        def fake_fetch(**kw):
            captured_window.append(kw.get("window_hours"))
            return []

        monkeypatch.setattr(
            "server.core.tier_transition_detector.fetch_recent_transitions",
            fake_fetch,
        )
        resp = client.get("/api/v1/sponsorship/recent-transitions")
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data["window_hours"] == 24
        assert captured_window == [24]

    def test_window_hours_clamped_to_min_1(self, client, monkeypatch):
        """Pin: window_hours=0 (or negative) → clamped to 1."""
        captured = []
        monkeypatch.setattr(
            "server.core.tier_transition_detector.fetch_recent_transitions",
            lambda **kw: captured.append(kw.get("window_hours")) or [],
        )
        resp = client.get("/api/v1/sponsorship/recent-transitions?window_hours=0")
        data = json.loads(resp.data)["data"]
        assert data["window_hours"] == 1

    def test_window_hours_clamped_to_max_168(self, client, monkeypatch):
        """Pin: window_hours=1000 → clamped to 168 (7 days). Prevents
        unbounded scans on the tier_history table."""
        monkeypatch.setattr(
            "server.core.tier_transition_detector.fetch_recent_transitions",
            lambda **kw: [],
        )
        resp = client.get("/api/v1/sponsorship/recent-transitions?window_hours=1000")
        data = json.loads(resp.data)["data"]
        assert data["window_hours"] == 168

    def test_response_shape(self, client, monkeypatch):
        """Pin: response carries {window_hours, transitions} top-level
        keys. Frontend reads `transitions` array directly."""
        fixture = [
            {
                "niche_id": "gaming",
                "tier": "eligible_now",
                "prev_tier": "within_2_months",
                "nearest_threshold_days": 0,
                "observed_at": "2026-06-23T10:00:00+00:00",
            }
        ]
        monkeypatch.setattr(
            "server.core.tier_transition_detector.fetch_recent_transitions",
            lambda **kw: fixture,
        )
        resp = client.get("/api/v1/sponsorship/recent-transitions")
        data = json.loads(resp.data)["data"]
        assert "window_hours" in data
        assert "transitions" in data
        assert data["transitions"] == fixture

    def test_cold_start_empty_transitions(self, client, monkeypatch):
        """Pin: empty fetch result → 200 + empty transitions list (not
        404, not 500). The endpoint is a passive dashboard surface;
        always 200 keeps the frontend simple."""
        monkeypatch.setattr(
            "server.core.tier_transition_detector.fetch_recent_transitions",
            lambda **kw: [],
        )
        resp = client.get("/api/v1/sponsorship/recent-transitions")
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data["transitions"] == []

    def test_fetch_exception_returns_empty_not_500(self, client, monkeypatch):
        """Pin: fetch_recent_transitions raising → 200 + empty list.
        Passive dashboard surfaces fail-OPEN (vs the readiness
        endpoint which fail-LOUDly because it's the primary contract)."""

        def boom(**kw):
            raise RuntimeError("simulated pg outage")

        monkeypatch.setattr(
            "server.core.tier_transition_detector.fetch_recent_transitions",
            boom,
        )
        resp = client.get("/api/v1/sponsorship/recent-transitions")
        assert resp.status_code == 200
        data = json.loads(resp.data)["data"]
        assert data["transitions"] == []


class TestSourceWire:
    """Source-level pin: the insert path is unconditional INSERT (not
    INSERT ... ON CONFLICT). Multiple concurrent dashboard tabs may
    write duplicate transition rows during a race window — that's
    intentional + harmless (see module docstring). This pin guards
    against accidentally adding ON CONFLICT in a future refactor
    that would silently swallow legitimate transitions."""

    def test_insert_is_unconditional(self):
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[1] / "server" / "core" / "tier_transition_detector.py"
        ).read_text()
        assert "INSERT INTO tier_history" in src
        # No ON CONFLICT clause on the INSERT path
        assert "ON CONFLICT" not in src

    def test_read_uses_distinct_on(self):
        """Pin: last-known-tier read uses SELECT DISTINCT ON (niche_id)
        — Postgres idiom for "row with max(observed_at) per group."
        Without this the read would return all history rows per niche."""
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[1] / "server" / "core" / "tier_transition_detector.py"
        ).read_text()
        assert "SELECT DISTINCT ON (niche_id)" in src
