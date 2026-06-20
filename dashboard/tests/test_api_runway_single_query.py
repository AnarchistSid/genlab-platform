"""Regression test for PR #396 — /runway collapsed 5 per-niche
blueprints.all() calls into 1 all-niches fetch + Python group-by.

Pre-fix: ``for niche_id in NICHE_IDS: client.blueprints.all(formula=AND(...niche_id=X))``
ran one BacklogClient round-trip per niche (5 calls per /runway request).

Post-fix: ONE ``blueprints.all(formula='{status}=VISUAL_READY')`` call
returns every VISUAL_READY blueprint across all niches; the handler
groups by niche_id in Python.

Pinned invariants:
  * Response shape (`runway_days`, `total_visual_ready`, `approved`,
    `status` per niche)
  * Status classification (critical / low / ok thresholds preserved)
  * Algorithmic invariant: exactly 1 `blueprints.all()` call per
    /runway request (mocked call counter)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

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


def _record(niche_id: str, approved: bool = False) -> dict:
    """Build a fake BacklogClient blueprint record matching the prod schema."""
    return {
        "id": f"bp-{niche_id}-{hash((niche_id, approved)) & 0xFFFF}",
        "fields": {
            "status": "VISUAL_READY",
            "niche_id": niche_id,
            "action_taken": "approved" if approved else "",
        },
    }


def _patch_client(records: list[dict]):
    """Mock the backlog client so blueprints.all() returns ``records``.

    Critical: the mock's `.call_count` lets the test count the number
    of `blueprints.all()` invocations per /runway request (the
    algorithmic invariant we're pinning).
    """
    mock_client = MagicMock()
    mock_client.blueprints.all.return_value = records
    return patch("server.api.runway._get_client", return_value=mock_client), mock_client


class TestRunwaySingleQuery:
    def test_runway_calls_blueprints_all_exactly_once(self, client):
        """Pin the algorithmic invariant: 1 BacklogClient call per /runway
        request, NOT 5 (one per niche) as the pre-fix code did.
        """
        records = [
            _record("ai_creators", approved=True),
            _record("gaming", approved=True),
            _record("gaming", approved=False),
            _record("sports", approved=True),
        ]
        cm, mock_client = _patch_client(records)
        with cm:
            resp = client.get("/api/v1/runway")
        assert resp.status_code == 200
        assert mock_client.blueprints.all.call_count == 1, (
            f"Expected exactly 1 blueprints.all() call (post-PR #396), "
            f"got {mock_client.blueprints.all.call_count} — per-niche-loop regression?"
        )

    def test_runway_groups_by_niche_correctly(self, client):
        """Verify the Python group-by produces correct per-niche counts."""
        records = [
            _record("ai_creators", approved=True),
            _record("ai_creators", approved=True),
            _record("ai_creators", approved=False),  # unapproved
            _record("gaming", approved=True),
            _record("sports", approved=True),
            _record("sports", approved=True),
            _record("sports", approved=True),
            _record("sports", approved=True),  # 4 approved → status ok (>3)
        ]
        cm, _ = _patch_client(records)
        with cm:
            resp = client.get("/api/v1/runway")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        # Always all 5 niches in response, even with zero records
        assert set(data.keys()) == {"ai_creators", "gaming", "sports", "movies", "anime"}
        assert data["ai_creators"]["total_visual_ready"] == 3
        assert data["ai_creators"]["approved"] == 2
        assert data["ai_creators"]["runway_days"] == 2
        assert data["ai_creators"]["status"] == "critical"  # ≤3 days
        assert data["gaming"]["approved"] == 1
        assert data["gaming"]["status"] == "critical"
        assert data["sports"]["approved"] == 4
        # 4 ≤ 7 (RUNWAY_WARN_DAYS), so status = "low"
        assert data["sports"]["status"] == "low"
        # Zero-record niches get the empty default
        assert data["movies"]["runway_days"] == 0
        assert data["movies"]["status"] == "critical"
        assert data["anime"]["runway_days"] == 0
        assert data["anime"]["status"] == "critical"

    def test_runway_stray_niche_id_is_ignored(self, client):
        """Records with a niche_id NOT in NICHE_IDS are silently skipped
        (defensive against historical rows with deprecated niche names).
        """
        records = [
            _record("ai_news", approved=True),  # legacy alias — should be ignored
            _record("gaming", approved=True),
        ]
        cm, _ = _patch_client(records)
        with cm:
            resp = client.get("/api/v1/runway")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        # ai_news isn't in NICHE_IDS so it's silently dropped
        assert "ai_news" not in data
        assert data["gaming"]["approved"] == 1
        assert data["ai_creators"]["approved"] == 0  # canonical name has zero

    def test_runway_fetch_failure_returns_502(self, client):
        """If the single fetch fails, return 502 — not a per-niche partial."""
        mock_client = MagicMock()
        mock_client.blueprints.all.side_effect = RuntimeError("connection lost")
        with patch("server.api.runway._get_client", return_value=mock_client):
            resp = client.get("/api/v1/runway")
        assert resp.status_code == 502
        body = resp.get_json()
        assert "runway_fetch_failed" in (body.get("error") or "")
