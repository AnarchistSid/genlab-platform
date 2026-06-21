"""Pin: overview pub_analytics dedup uses O(1) set membership.

Perf fix (2026-06-21): the publishing_analytics supplementation loop in
``_build_overview`` had a nested ``for r in today_records`` linear scan
to test whether a niche was already represented. With 200 max
pub_analytics records and up to 500 today_records, that was 100k
iterations per overview call — and Mission Control polls this every 60s.

The fix builds a ``set[str]`` of niche_ids ONCE before the outer loop,
then tests membership in O(1) and mutates the set when synthetic
records are appended.

These pins lock in:

  1. The right COUNT of synthetic records is appended (dedup still
     correct — exactly one synthetic record per missing niche, not
     zero, not two).
  2. The set is mutated when a synthetic record is appended (so a later
     pub_analytics record for the same niche doesn't double-append).
  3. The dedup behavior is invariant to whether ``created_at`` is a
     ``datetime`` or an ISO string — both code branches share the same
     set, so cross-branch dedup also works.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch


def _run_build_overview(pub_analytics_records, today_blueprint_records):
    """Run ``_build_overview`` with controlled records.

    All ``_build_overview`` external dependencies are stubbed via context
    managers (we do NOT decorate the helper itself — that injects mock
    args into the helper's signature, which collides with our kwargs).
    """
    with (
        patch("server.api.overview._get_client") as mock_client_fn,
        patch("server.api.overview._load_registry") as mock_registry,
        patch("server.api.overview._platform_health_from_reports") as _phr,
        patch("server.api.pipeline._merge_prefect_status", side_effect=lambda x: x),
        patch("server.api.pipeline._prefect_healthy", return_value=False),
    ):
        from server.api.overview import _build_overview

        mock_registry.return_value = [
            {"id": "gaming", "status": "active", "display_name": "CR"},
            {"id": "sports", "status": "active", "display_name": "CW"},
            {"id": "movies", "status": "active", "display_name": "SR"},
        ]
        _phr.return_value = {}

        mock_client = MagicMock()
        # blueprints.all returns "today_blueprint_records" — these
        # populate today_records via the date-window filter in
        # ``_build_overview``.
        mock_client.blueprints.all.return_value = today_blueprint_records
        # publishing_analytics.all returns the records that exercise
        # the dedup path under test.
        mock_client.publishing_analytics.all.return_value = pub_analytics_records
        mock_client_fn.return_value = mock_client

        result = _build_overview()

    return result.get("niches", [])


class TestOverviewPubAnalyticsDedupSet(unittest.TestCase):
    def test_two_pub_analytics_same_niche_appends_only_one_synthetic(self):
        """Two pub_analytics rows for ``movies`` (niche absent from
        today_records) should add ONE synthetic record, not two — the
        set is mutated on first append so the second sees membership."""
        now = datetime.now(UTC)
        today_blueprints = [
            {
                "id": "100",
                "fields": {
                    "status": "PUBLISHED",
                    "published_at": now.isoformat(),
                    "niche_id": "sports",
                    "priority_score": "0.5",
                },
            },
        ]
        pub_analytics = [
            {
                "id": "p1",
                "fields": {
                    "status": "SUCCESS",
                    "created_at": now,  # datetime branch
                    "niche_id": "movies",
                },
            },
            {
                "id": "p2",
                "fields": {
                    "status": "SUCCESS",
                    "created_at": (now - timedelta(minutes=10)),
                    "niche_id": "movies",
                },
            },
        ]
        niches = _run_build_overview(
            pub_analytics_records=pub_analytics,
            today_blueprint_records=today_blueprints,
        )
        movies = next((n for n in niches if n["id"] == "movies"), None)
        assert movies is not None
        # Pin: exactly 1 published-today for movies, not 2. If the
        # ``niche_ids_today.add(n)`` line gets removed, this fires.
        assert movies["published_today"] == 1, (
            f"Expected 1 synthetic record for movies, got "
            f"{movies['published_today']}. Set mutation missing?"
        )

    def test_pub_analytics_for_niche_already_in_today_records_no_append(self):
        """If a niche already has a blueprint in today_records, the
        pub_analytics row must NOT create a duplicate synthetic record."""
        now = datetime.now(UTC)
        today_blueprints = [
            {
                "id": "200",
                "fields": {
                    "status": "PUBLISHED",
                    "published_at": now.isoformat(),
                    "niche_id": "gaming",
                    "priority_score": "0.6",
                },
            },
        ]
        pub_analytics = [
            {
                "id": "p1",
                "fields": {
                    "status": "SUCCESS",
                    "created_at": now,
                    "niche_id": "gaming",
                },
            },
        ]
        niches = _run_build_overview(
            pub_analytics_records=pub_analytics,
            today_blueprint_records=today_blueprints,
        )
        gaming = next((n for n in niches if n["id"] == "gaming"), None)
        assert gaming is not None
        # Pin: still 1, not 2 — the existing-niche check works.
        assert gaming["published_today"] == 1

    def test_cross_branch_dedup_string_then_datetime(self):
        """One pub_analytics row uses ISO string ``created_at``, another
        uses a ``datetime`` for the SAME niche. Both code branches share
        the niche_ids_today set, so dedup must work across the boundary."""
        now = datetime.now(UTC)
        today_blueprints: list[dict] = []
        pub_analytics = [
            {
                "id": "p1",
                "fields": {
                    "status": "SUCCESS",
                    "created_at": now.isoformat(),  # string branch
                    "niche_id": "sports",
                },
            },
            {
                "id": "p2",
                "fields": {
                    "status": "SUCCESS",
                    "created_at": now - timedelta(minutes=5),  # datetime branch
                    "niche_id": "sports",
                },
            },
        ]
        niches = _run_build_overview(
            pub_analytics_records=pub_analytics,
            today_blueprint_records=today_blueprints,
        )
        sports = next((n for n in niches if n["id"] == "sports"), None)
        assert sports is not None
        # Pin: exactly 1 synthetic record for sports despite TWO
        # pub_analytics rows hitting DIFFERENT code branches. If the
        # set is rebuilt per-branch or scoped wrong, this fires.
        assert sports["published_today"] == 1, (
            f"Expected cross-branch dedup → 1 sports record, got {sports['published_today']}."
        )


if __name__ == "__main__":
    unittest.main()
