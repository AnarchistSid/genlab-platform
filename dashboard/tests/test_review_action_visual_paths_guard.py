"""Pin: ``_execute_review_action`` must refuse to approve a blueprint
that has no rendered media (task #575).

## 2026-07-08 live-fire

Tomorrow's queue check surfaced 3 blueprints in
``status='VISUAL_READY' + action_taken='approved' + scheduled_for=<future>``
with ``extra.visual_paths`` MISSING from the JSONB blob:

  * Sports ``1a9c169c`` → scheduled 2026-07-09 (tomorrow)
  * Movies ``284ac2fe`` → scheduled 2026-07-28
  * Movies ``767c6112`` → scheduled 2026-07-29

All 3 created ``2026-06-29 16:31:25`` UTC — same batch.

Publisher would have failed each at fire time with ``No valid media
files for video publish`` (payload_builder.py:218-222). This guard
now refuses the review action at the dashboard/API layer so the
class of bug can't recur.

Related task #576 investigates HOW they got to
``VISUAL_READY + no visual_paths`` in the first place.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from server.review_server import _execute_review_action


def _bp(fields: dict | None = None) -> dict:
    """Build a minimal blueprint payload matching the client.blueprints.get
    return shape (``{id, fields}``)."""
    return {
        "id": "test-blueprint-uuid",
        "fields": {
            "niche_id": "sports",
            "format": "reel",
            **(fields or {}),
        },
    }


class TestApproveGuardVisualPaths:
    """The approve branch of _execute_review_action must reject when
    the blueprint has no visual_paths in extra. This is the load-
    bearing invariant for task #575."""

    def test_approve_refused_when_extra_missing_visual_paths(self):
        """A blueprint whose ``extra`` dict doesn't contain
        ``visual_paths`` must raise. Sports 1a9c169c on 2026-07-08 had
        this exact shape and was queued to fail publisher."""
        client = MagicMock()
        client.blueprints.get.return_value = _bp(
            {"extra": {"angle": "test", "score": 0.5}}  # no visual_paths key
        )
        with (
            patch("server.core.graph_sync.get_sync_client", return_value=client),
            pytest.raises(ValueError, match="visual_paths missing"),
        ):
            _execute_review_action("test-blueprint-uuid", "approved")
        # Update must NOT have fired.
        client.blueprints.update.assert_not_called()

    def test_approve_refused_when_visual_paths_empty_list(self):
        """``extra.visual_paths = []`` is present but empty — same
        publisher outcome. Guard must reject."""
        client = MagicMock()
        client.blueprints.get.return_value = _bp({"extra": {"visual_paths": []}})
        with (
            patch("server.core.graph_sync.get_sync_client", return_value=client),
            pytest.raises(ValueError, match="visual_paths missing"),
        ):
            _execute_review_action("test-blueprint-uuid", "approved")
        client.blueprints.update.assert_not_called()

    def test_approve_refused_when_visual_paths_string_placeholder(self):
        """``extra.visual_paths = "[]"`` (JSON-serialized empty list) is
        the shape some fetch paths return. Guard must still reject."""
        client = MagicMock()
        client.blueprints.get.return_value = _bp({"extra": {"visual_paths": "[]"}})
        with (
            patch("server.core.graph_sync.get_sync_client", return_value=client),
            pytest.raises(ValueError, match="visual_paths missing"),
        ):
            _execute_review_action("test-blueprint-uuid", "approved")

    def test_approve_allowed_when_visual_paths_populated(self):
        """The happy path — a rendered blueprint with a real path list
        must approve normally. Otherwise the guard breaks the whole
        publisher pipeline."""
        client = MagicMock()
        client.blueprints.get.return_value = _bp(
            {"extra": {"visual_paths": ["/opt/genlab/.tmp/runs/x/reel.mp4"]}}
        )
        with (
            patch("server.core.graph_sync.get_sync_client", return_value=client),
            patch(
                "server.core.publishing_queue._next_available_slot",
                return_value="2026-07-10T06:00:00+00:00",
            ),
            # Calibration write is best-effort; stub to avoid its DB call.
            patch("genlab_core.scheduling.calibration_logger.log"),
        ):
            result = _execute_review_action("test-blueprint-uuid", "approved")
        assert result["action_taken"] == "approved"
        assert "scheduled_for" in result
        client.blueprints.update.assert_called_once()

    def test_approve_allowed_when_extra_is_json_string(self):
        """Some client paths still store ``extra`` as a JSON string
        rather than a dict. Guard must decode + evaluate correctly."""
        client = MagicMock()
        client.blueprints.get.return_value = _bp(
            {"extra": '{"visual_paths": ["/opt/genlab/.tmp/runs/y/reel.mp4"]}'}
        )
        with (
            patch("server.core.graph_sync.get_sync_client", return_value=client),
            patch(
                "server.core.publishing_queue._next_available_slot",
                return_value="2026-07-10T06:00:00+00:00",
            ),
            patch("genlab_core.scheduling.calibration_logger.log"),
        ):
            result = _execute_review_action("test-blueprint-uuid", "approved")
        assert result["action_taken"] == "approved"

    def test_reject_does_not_hit_visual_paths_guard(self):
        """The guard is scoped to ``action == 'approved'`` — rejecting
        an unrendered blueprint must work fine (it's ARCHIVED,
        publisher never sees it)."""
        client = MagicMock()
        client.blueprints.get.return_value = _bp({"extra": {"angle": "test"}})
        with (
            patch("server.core.graph_sync.get_sync_client", return_value=client),
            patch("genlab_core.scheduling.calibration_logger.log"),
        ):
            result = _execute_review_action("test-blueprint-uuid", "rejected", feedback_issue="bad")
        assert result["status"] == "ARCHIVED"
        assert result["scheduled_for"] is None
        client.blueprints.update.assert_called_once()

    def test_approve_refused_error_message_names_task_575(self):
        """The ValueError message must be operator-actionable. Names the
        remedy (re-render or reject) so the dashboard can surface it
        clearly."""
        client = MagicMock()
        client.blueprints.get.return_value = _bp({"extra": {"visual_paths": []}})
        with (
            patch("server.core.graph_sync.get_sync_client", return_value=client),
            pytest.raises(ValueError) as exc_info,
        ):
            _execute_review_action("test-blueprint-uuid", "approved")

        msg = str(exc_info.value).lower()
        assert "visual_paths missing" in msg
        assert "re-run render" in msg or "reject" in msg
