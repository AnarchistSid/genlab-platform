"""Integration pin: pipeline API exposes `stage_failures` from run_report.json.

Companion to RENDER #2 (commit 3a46eb9). The genlab-core RunReport stage
writes `stage_failures` to run_report.json; the dashboard API's
``_normalize_run`` MUST include the field in the returned
PipelineRun dict so the React `RunHistoryTable` can render the warning
badge.

If this test fails: the API normalizer was edited and silently dropped
the field, OR the run_report.json schema was changed without updating
the normalizer. Either way the badge disappears from the operator UI.
"""

from __future__ import annotations

from server.api.pipeline import _normalize_run


def test_stage_failures_included_when_present():
    """A run_report with stage_failures present must propagate."""
    data = {
        "status": "partial",
        "stage_failures": {"whisper_captions": 2, "audio": 1},
        "metrics": {"blueprints_count": 4, "stories_count": 4},
    }
    out = _normalize_run(data, run_id="anime_20260613_120000")
    assert out["stage_failures"] == {"whisper_captions": 2, "audio": 1}
    assert out["status"] == "partial"


def test_stage_failures_default_empty_when_missing():
    """Older run reports without the field must default to {} so the
    frontend's `Object.keys(...).length > 0` check doesn't TypeError
    on `undefined`."""
    data = {
        "status": "success",
        "metrics": {"blueprints_count": 1, "stories_count": 1},
    }
    out = _normalize_run(data, run_id="ai_creators_20260101_000000")
    assert out["stage_failures"] == {}


def test_stage_failures_independent_of_status():
    """`stage_failures` and `status` are independent fields in the output —
    a run flagged 'failed' (zero blueprints) can still carry stage_failures
    for attribution. Pin this so a refactor that conflates them tripts here."""
    data = {
        "status": "failed",
        "stage_failures": {"whisper_captions": 1},
        "metrics": {"blueprints_count": 0, "stories_count": 4},
    }
    out = _normalize_run(data, run_id="movies_20260613_120000")
    assert out["status"] == "failed"
    assert out["stage_failures"] == {"whisper_captions": 1}


def test_stage_failures_handles_non_dict_input_gracefully():
    """Defensive: if the upstream writer ever produced a non-dict value
    for stage_failures, the normalizer must not crash the API endpoint."""
    data = {
        "status": "partial",
        "stage_failures": None,  # null in JSON
        "metrics": {"blueprints_count": 1, "stories_count": 1},
    }
    # Per the normalizer's `data.get("stage_failures", {})` call, None
    # would propagate. The frontend's `Object.keys(undefined ?? {})`
    # handles this fine. Pin the contract.
    out = _normalize_run(data, run_id="x_20260101_000000")
    # API can return None OR {} — both render the same on frontend
    assert out["stage_failures"] in (None, {})
