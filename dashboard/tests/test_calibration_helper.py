"""Pin the 2026-06-15 S2 fix.

`server.core.calibration_helper.log_calibration_for_action` exists
so the 4 bulk-review / approve-and-schedule / batch-approve-schedule
/ archive paths all log calibration data through a single shape — no
duplicated wrapper-build + gate-eval + logger glue.

These pins prove:

1. The helper actually calls calibration_logger (catches a future
   refactor that silently no-ops).
2. The dashboard's `archived` action maps to `rejected` for the
   calibration matrix (calibration_logger's VALID_OPERATOR_ACTIONS
   doesn't include "archived" — without the alias it would skip the
   row entirely).
3. The helper swallows ALL exceptions — calibration MUST NEVER block
   an operator-action endpoint.
4. The gate's `extra` wrapper is built when the blueprint dict
   doesn't already have one (mirrors PR #221's auto_approver fix
   + the dashboard preview endpoint).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _mock_client_with_blueprint(extra=None, niche_id="gaming", **fields):
    """Build a mock BacklogClient whose .blueprints.get returns the
    given blueprint shape."""
    client = MagicMock()
    bp = {
        "id": "bp1",
        "fields": {
            "niche_id": niche_id,
            "hook_text": "h",
            "composite_score": 0.7,
            "virality_score": 0.1,
            "visual_paths": ["/v.mp4"],
            "validation_status": {"all_passed": True},
            "action_taken_source": "",
            **fields,
        },
    }
    if extra is not None:
        bp["fields"]["extra"] = extra
    client.blueprints.get.return_value = bp
    return client


class TestLogCalibrationForAction:
    def test_calls_calibration_logger_with_operator_action(self):
        """The helper must actually call calibration_logger.log with
        the operator's action — the whole reason it exists is to
        unblock S2."""
        from server.core.calibration_helper import log_calibration_for_action

        client = _mock_client_with_blueprint()
        with patch(
            "genlab_core.scheduling.calibration_logger.log",
        ) as mock_log:
            log_calibration_for_action(client=client, record_id="bp1", action="approved")
        mock_log.assert_called_once()
        kwargs = mock_log.call_args.kwargs
        assert kwargs["blueprint_id"] == "bp1"
        assert kwargs["niche_id"] == "gaming"
        assert kwargs["operator_action"] == "approved"

    def test_archived_action_aliased_to_rejected(self):
        """``archived`` is NOT in calibration_logger.VALID_OPERATOR_ACTIONS
        — without the alias, archive would skip the row. The helper
        maps archived → rejected so the confusion-matrix denominator
        counts operator "don't publish" decisions correctly."""
        from server.core.calibration_helper import log_calibration_for_action

        client = _mock_client_with_blueprint()
        with patch(
            "genlab_core.scheduling.calibration_logger.log",
        ) as mock_log:
            log_calibration_for_action(client=client, record_id="bp1", action="archived")
        kwargs = mock_log.call_args.kwargs
        assert kwargs["operator_action"] == "rejected", (
            "archived must map to rejected — otherwise it gets filtered "
            "by calibration_logger's VALID_OPERATOR_ACTIONS"
        )

    def test_swallows_calibration_logger_exception(self):
        """If calibration_logger.log raises, the helper MUST NOT
        propagate — calibration is best-effort and the operator-action
        endpoint must keep working."""
        from server.core.calibration_helper import log_calibration_for_action

        client = _mock_client_with_blueprint()
        with patch(
            "genlab_core.scheduling.calibration_logger.log",
            side_effect=RuntimeError("simulated DB failure"),
        ):
            # MUST NOT raise
            log_calibration_for_action(client=client, record_id="bp1", action="approved")

    def test_swallows_blueprint_fetch_exception(self):
        """If client.blueprints.get raises (e.g. record archived
        between operator click and re-fetch), the helper MUST NOT
        propagate."""
        from server.core.calibration_helper import log_calibration_for_action

        client = MagicMock()
        client.blueprints.get.side_effect = RuntimeError("not found")
        # MUST NOT raise
        log_calibration_for_action(client=client, record_id="bp1", action="approved")

    def test_builds_extra_wrapper_when_missing(self):
        """When blueprint fields don't include an `extra` dict, the
        helper builds one from top-level fields so the gate's
        evaluate() sees real scores instead of None. Same fix as PR
        #221 for the auto_approver path."""
        from server.core.calibration_helper import log_calibration_for_action

        # No `extra` key in the blueprint — scores live at top level
        client = _mock_client_with_blueprint(extra=None)
        with patch("genlab_core.scheduling.auto_approval_gate.evaluate") as mock_eval:
            # Use real calibration_logger.log mock so the helper runs
            # to completion; we're only checking what was passed to
            # evaluate.
            with patch("genlab_core.scheduling.calibration_logger.log"):
                log_calibration_for_action(client=client, record_id="bp1", action="approved")
        mock_eval.assert_called_once()
        # The blueprint dict passed to evaluate must have an `extra`
        # dict carrying the score fields.
        passed_bp = mock_eval.call_args.args[0]
        assert isinstance(passed_bp.get("extra"), dict)
        assert passed_bp["extra"]["composite_score"] == 0.7
        assert passed_bp["extra"]["virality_score"] == 0.1

    def test_preserves_existing_extra_dict(self):
        """When blueprint already has an `extra` dict, the helper
        MUST NOT overwrite it (SharePoint path regression pin —
        mirrors PR #221's same-name pin in test_auto_approver)."""
        from server.core.calibration_helper import log_calibration_for_action

        existing_extra = {
            "composite_score": 0.99,
            "virality_score": 0.99,
            "custom_field": "do not delete me",
        }
        client = _mock_client_with_blueprint(extra=existing_extra)
        with patch("genlab_core.scheduling.auto_approval_gate.evaluate") as mock_eval:
            with patch("genlab_core.scheduling.calibration_logger.log"):
                log_calibration_for_action(client=client, record_id="bp1", action="approved")
        passed_bp = mock_eval.call_args.args[0]
        assert passed_bp["extra"] is existing_extra
        assert passed_bp["extra"]["custom_field"] == "do not delete me"


class TestVisualPathsJsonDecode:
    """Pin the 2026-06-15 audit fix: visual_paths must be JSON-decoded.

    Bug: Postgres path stores visual_paths as a JSON string. The
    previous code passed the raw string through, and the gate's
    has_video check (`bool(extra.get("visual_paths"))`) returned True
    for the string `"[]"` — meaning calibration verdict disagreed
    with what the worker would compute for the same blueprint.
    """

    def _client_with_visual_paths(self, raw_value):
        """Build a mock client whose blueprint has the given raw
        visual_paths value (string or list)."""
        from unittest.mock import MagicMock

        client = MagicMock()
        client.blueprints.get.return_value = {
            "id": "bp1",
            "fields": {
                "niche_id": "gaming",
                "hook_text": "h",
                "composite_score": 0.7,
                "virality_score": 0.1,
                "visual_paths": raw_value,
                "validation_status": {"all_passed": True},
                "action_taken_source": "",
            },
        }
        return client

    def test_empty_list_string_decoded_to_empty_list(self):
        """The bug case: `"[]"` string must decode to `[]` so the gate
        sees has_video=False (the post truly has no video)."""
        from unittest.mock import patch

        from server.core.calibration_helper import log_calibration_for_action

        client = self._client_with_visual_paths("[]")
        with patch("genlab_core.scheduling.auto_approval_gate.evaluate") as mock_eval:
            with patch("genlab_core.scheduling.calibration_logger.log"):
                log_calibration_for_action(client=client, record_id="bp1", action="approved")

        passed_bp = mock_eval.call_args.args[0]
        # visual_paths in the wrapper must be a list (decoded), not a string
        assert passed_bp["extra"]["visual_paths"] == [], (
            "'[]' string must decode to [] — bool(string) is True even when "
            "string represents empty list. Gate would report has_video=True "
            "for a video-less blueprint."
        )

    def test_non_empty_list_string_decoded_correctly(self):
        """`'["/a.mp4"]'` decodes to `["/a.mp4"]` — gate sees has_video=True."""
        from unittest.mock import patch

        from server.core.calibration_helper import log_calibration_for_action

        client = self._client_with_visual_paths('["/path/a.mp4"]')
        with patch("genlab_core.scheduling.auto_approval_gate.evaluate") as mock_eval:
            with patch("genlab_core.scheduling.calibration_logger.log"):
                log_calibration_for_action(client=client, record_id="bp1", action="approved")

        passed_bp = mock_eval.call_args.args[0]
        assert passed_bp["extra"]["visual_paths"] == ["/path/a.mp4"]

    def test_malformed_json_string_falls_back_to_empty_list(self):
        """Garbled string mustn't raise — fall back to []."""
        from unittest.mock import patch

        from server.core.calibration_helper import log_calibration_for_action

        client = self._client_with_visual_paths("not-valid-json!!!")
        with patch("genlab_core.scheduling.auto_approval_gate.evaluate") as mock_eval:
            with patch("genlab_core.scheduling.calibration_logger.log"):
                log_calibration_for_action(client=client, record_id="bp1", action="approved")
        passed_bp = mock_eval.call_args.args[0]
        assert passed_bp["extra"]["visual_paths"] == []

    def test_list_value_preserved(self):
        """When the field is already a list (SharePoint path), no decode needed."""
        from unittest.mock import patch

        from server.core.calibration_helper import log_calibration_for_action

        client = self._client_with_visual_paths(["/b.mp4", "/c.mp4"])
        with patch("genlab_core.scheduling.auto_approval_gate.evaluate") as mock_eval:
            with patch("genlab_core.scheduling.calibration_logger.log"):
                log_calibration_for_action(client=client, record_id="bp1", action="approved")
        passed_bp = mock_eval.call_args.args[0]
        assert passed_bp["extra"]["visual_paths"] == ["/b.mp4", "/c.mp4"]


class TestQueueActionAliases:
    """Pin the 2026-06-15 audit fix: queue verbs map to calibration verbs.

    Publishing Queue endpoints use ``held`` and ``released`` — neither
    is in calibration_logger.VALID_OPERATOR_ACTIONS. Without the
    alias, every queue-action calibration call would be silently
    skipped. After the fix:
      - held → rejected  (operator says don't publish, at least for now)
      - released → skipped (operator removes from queue, no content verdict)
    """

    def _setup(self, monkeypatch):
        """Common setup: env + mock blueprint."""
        from unittest.mock import MagicMock

        monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@127.0.0.1:1/d")
        client = MagicMock()
        client.blueprints.get.return_value = {
            "id": "bp1",
            "fields": {
                "niche_id": "gaming",
                "hook_text": "h",
                "composite_score": 0.7,
                "virality_score": 0.1,
                "visual_paths": ["/v.mp4"],
                "validation_status": {"all_passed": True},
                "action_taken_source": "",
            },
        }
        return client

    def test_held_mapped_to_rejected(self, monkeypatch):
        from unittest.mock import patch

        from server.core.calibration_helper import log_calibration_for_action

        client = self._setup(monkeypatch)
        with patch(
            "genlab_core.scheduling.calibration_logger.log",
        ) as mock_log:
            log_calibration_for_action(client=client, record_id="bp1", action="held")
        kwargs = mock_log.call_args.kwargs
        assert kwargs["operator_action"] == "rejected"

    def test_released_mapped_to_skipped(self, monkeypatch):
        from unittest.mock import patch

        from server.core.calibration_helper import log_calibration_for_action

        client = self._setup(monkeypatch)
        with patch(
            "genlab_core.scheduling.calibration_logger.log",
        ) as mock_log:
            log_calibration_for_action(client=client, record_id="bp1", action="released")
        kwargs = mock_log.call_args.kwargs
        assert kwargs["operator_action"] == "skipped"
