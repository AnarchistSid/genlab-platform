"""Pin the PR #510 split of the historical ``pending_records`` bucket
into ``render_retry`` (DRAFTED) + ``operator_review`` (VISUAL_READY
with empty action_taken).

Pre-PR: a single ``total_pending_review`` counted both. The Mission
Control banner "X posts waiting for review" surfaced X but X of those
were actually DRAFTED render-retries (NOT an operator action — needs
pipeline re-run). Operator clicked through, saw unactionable rows,
lost trust in the badge.

Post-PR: two distinct counts surface in the API; the banner uses
``total_operator_review`` so the X reflects actual click-needed work.
Backward-compat: ``total_pending_review`` stays as the union.

The pin matters because the conflation is silent — the wrong number
just looks like a slightly-off count, not an obvious bug. A refactor
that drops the split without updating the banner re-introduces the
trust gap invisibly.
"""

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch


class TestOverviewReviewQueueSplit(unittest.TestCase):
    @patch("server.api.overview._get_client")
    @patch("server.api.overview._load_registry")
    @patch("server.api.overview._platform_health_from_reports")
    @patch("server.api.pipeline._merge_prefect_status", side_effect=lambda x: x)
    @patch("server.api.pipeline._prefect_healthy", return_value=False)
    def test_split_produces_distinct_counts(self, _ph, _mp, _phr, mock_registry, mock_client_fn):
        from server.api.overview import _build_overview

        mock_registry.return_value = [
            {"id": "movies", "status": "active", "display_name": "SR"},
        ]
        _phr.return_value = {}

        now = datetime.now(UTC)
        now_iso = now.isoformat()
        # Stale created_at for the "oldest_age" pin
        three_days_ago = (now - timedelta(days=3)).isoformat()

        mock_client = MagicMock()
        mock_client.blueprints.all.return_value = [
            # 2 DRAFTED with empty action → render_retry bucket
            {
                "id": "d1",
                "fields": {
                    "status": "DRAFTED",
                    "action_taken": "",
                    "niche_id": "movies",
                    "created_at": now_iso,
                },
            },
            {
                "id": "d2",
                "fields": {
                    "status": "DRAFTED",
                    "action_taken": None,
                    "niche_id": "movies",
                    "created_at": now_iso,
                },
            },
            # 3 VISUAL_READY with empty action → operator_review bucket;
            # one is 3 days old to exercise the oldest-age pin.
            {
                "id": "v1",
                "fields": {
                    "status": "VISUAL_READY",
                    "action_taken": "",
                    "niche_id": "movies",
                    "created_at": three_days_ago,
                },
            },
            {
                "id": "v2",
                "fields": {
                    "status": "VISUAL_READY",
                    "action_taken": None,
                    "niche_id": "movies",
                    "created_at": now_iso,
                },
            },
            {
                "id": "v3",
                "fields": {
                    "status": "VISUAL_READY",
                    "action_taken": "",
                    "niche_id": "movies",
                    "created_at": now_iso,
                },
            },
            # 1 VISUAL_READY with action_taken set → already approved,
            # NOT pending. Must NOT count in either bucket.
            {
                "id": "v4",
                "fields": {
                    "status": "VISUAL_READY",
                    "action_taken": "approved",
                    "scheduled_for": now_iso,
                    "niche_id": "movies",
                    "created_at": now_iso,
                },
            },
        ]
        mock_client_fn.return_value = mock_client
        mock_client.publishing_analytics.all.return_value = []

        result = _build_overview()
        g = result["global"]

        # Backward-compat: the union still equals the historical count.
        assert g["total_pending_review"] == 5, (
            f"union must include 2 DRAFTED + 3 VISUAL_READY = 5, got {g['total_pending_review']}"
        )
        # New structured fields
        assert g["total_render_retry"] == 2, (
            f"DRAFTED count must split to render_retry, got {g['total_render_retry']}"
        )
        assert g["total_operator_review"] == 3, (
            f"VISUAL_READY-with-empty-action count must split to "
            f"operator_review, got {g['total_operator_review']}"
        )
        # Sanity: union == sum of split
        assert g["total_render_retry"] + g["total_operator_review"] == g["total_pending_review"]

    @patch("server.api.overview._get_client")
    @patch("server.api.overview._load_registry")
    @patch("server.api.overview._platform_health_from_reports")
    @patch("server.api.pipeline._merge_prefect_status", side_effect=lambda x: x)
    @patch("server.api.pipeline._prefect_healthy", return_value=False)
    def test_oldest_operator_review_age_hours_reflects_oldest_visual_ready(
        self, _ph, _mp, _phr, mock_registry, mock_client_fn
    ):
        from server.api.overview import _build_overview

        mock_registry.return_value = [{"id": "movies", "status": "active", "display_name": "SR"}]
        _phr.return_value = {}

        now = datetime.now(UTC)
        # 5 VISUAL_READY items at different ages — oldest is 72h.
        ages_hours = [1, 12, 72, 24, 48]
        records = [
            {
                "id": f"v{i}",
                "fields": {
                    "status": "VISUAL_READY",
                    "action_taken": "",
                    "niche_id": "movies",
                    "created_at": (now - timedelta(hours=h)).isoformat(),
                },
            }
            for i, h in enumerate(ages_hours)
        ]
        mock_client = MagicMock()
        mock_client.blueprints.all.return_value = records
        mock_client_fn.return_value = mock_client
        mock_client.publishing_analytics.all.return_value = []

        result = _build_overview()
        age = result["global"]["oldest_operator_review_age_hours"]
        # Allow ±1h slop for clock drift between the now snapshot above
        # and the now snapshot inside _build_overview.
        assert age is not None
        assert 71 <= age <= 73, f"oldest age must be ~72h (the max of {ages_hours}), got {age}"

    @patch("server.api.overview._get_client")
    @patch("server.api.overview._load_registry")
    @patch("server.api.overview._platform_health_from_reports")
    @patch("server.api.pipeline._merge_prefect_status", side_effect=lambda x: x)
    @patch("server.api.pipeline._prefect_healthy", return_value=False)
    def test_oldest_age_is_none_when_no_operator_review_items(
        self, _ph, _mp, _phr, mock_registry, mock_client_fn
    ):
        """When the operator-review queue is empty, age must be None
        (not 0) so the frontend banner condition (`age >= 24` → show
        suffix) doesn't accidentally render '(oldest: 0d)'."""
        from server.api.overview import _build_overview

        mock_registry.return_value = [{"id": "movies", "status": "active", "display_name": "SR"}]
        _phr.return_value = {}
        mock_client = MagicMock()
        mock_client.blueprints.all.return_value = []  # no pending items
        mock_client_fn.return_value = mock_client
        mock_client.publishing_analytics.all.return_value = []

        result = _build_overview()
        g = result["global"]
        assert g["total_operator_review"] == 0
        assert g["oldest_operator_review_age_hours"] is None

    @patch("server.api.overview._get_client")
    @patch("server.api.overview._load_registry")
    @patch("server.api.overview._platform_health_from_reports")
    @patch("server.api.pipeline._merge_prefect_status", side_effect=lambda x: x)
    @patch("server.api.pipeline._prefect_healthy", return_value=False)
    def test_per_niche_split_counts_appear_on_niche_dict(
        self, _ph, _mp, _phr, mock_registry, mock_client_fn
    ):
        """Per-niche dict must carry render_retry_count +
        operator_review_count alongside the legacy pending_review."""
        from server.api.overview import _build_overview

        mock_registry.return_value = [
            {"id": "gaming", "status": "active", "display_name": "CR"},
            {"id": "movies", "status": "active", "display_name": "SR"},
        ]
        _phr.return_value = {}

        now_iso = datetime.now(UTC).isoformat()
        records = [
            # gaming: 2 DRAFTED render-retry, 0 operator-review
            {
                "id": "g1",
                "fields": {
                    "status": "DRAFTED",
                    "action_taken": "",
                    "niche_id": "gaming",
                    "created_at": now_iso,
                },
            },
            {
                "id": "g2",
                "fields": {
                    "status": "DRAFTED",
                    "action_taken": "",
                    "niche_id": "gaming",
                    "created_at": now_iso,
                },
            },
            # movies: 0 DRAFTED, 3 operator-review
            *[
                {
                    "id": f"m{i}",
                    "fields": {
                        "status": "VISUAL_READY",
                        "action_taken": "",
                        "niche_id": "movies",
                        "created_at": now_iso,
                    },
                }
                for i in range(3)
            ],
        ]
        mock_client = MagicMock()
        mock_client.blueprints.all.return_value = records
        mock_client_fn.return_value = mock_client
        mock_client.publishing_analytics.all.return_value = []

        result = _build_overview()
        niches_by_id = {n["id"]: n for n in result["niches"]}

        assert niches_by_id["gaming"]["render_retry_count"] == 2
        assert niches_by_id["gaming"]["operator_review_count"] == 0
        # Legacy: pending_review on gaming still equals the union (2)
        assert niches_by_id["gaming"]["pending_review"] == 2

        assert niches_by_id["movies"]["render_retry_count"] == 0
        assert niches_by_id["movies"]["operator_review_count"] == 3
        assert niches_by_id["movies"]["pending_review"] == 3


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
