"""Integration pin: pipeline API exposes ``bottleneck_stage`` +
``bottleneck_reason`` from run_report.json.

Companion to PR #504 + PR #508 (2026-06-24). The genlab-core RunReport
stage writes ``bottleneck_stage`` to run_report.json when
``blueprints_pushed == 0`` — the dashboard API's ``_normalize_run``
MUST include the field in the returned PipelineRun dict so the React
``RunHistoryTable`` can render the "blocked at X" badge.

If this test fails: the API normalizer was edited and silently dropped
the field, OR the run_report.json schema was changed without updating
the normalizer. Either way the badge disappears from the operator UI
and the operator goes back to parsing slo_violations strings.
"""

from __future__ import annotations

from server.api.pipeline import _normalize_run


def test_bottleneck_fields_propagate_when_present():
    """A run_report with bottleneck_stage + bottleneck_reason set must
    surface both as top-level keys in the API response."""
    data = {
        "status": "failed",
        "bottleneck_stage": "video_gate",
        "bottleneck_reason": "all 5/5 stories dropped for missing clips (VideoSourcer tier failure)",
        "metrics": {"blueprints_count": 0, "stories_count": 0},
    }
    out = _normalize_run(data, run_id="sports_20260623_100002")
    assert out["bottleneck_stage"] == "video_gate"
    assert (
        out["bottleneck_reason"]
        == "all 5/5 stories dropped for missing clips (VideoSourcer tier failure)"
    )
    assert out["status"] == "failed"


def test_bottleneck_fields_default_null_when_missing():
    """Older run reports without these fields must default to None so
    the frontend's truthy check (`if (run.bottleneck_stage)`) works
    without a TypeError or a spurious badge.

    Default None (not "") is intentional — empty string would still be
    falsy in JS but creates an ambiguity gap that's hard to remove later.
    """
    data = {
        "status": "success",
        "metrics": {"blueprints_count": 3, "stories_count": 3},
    }
    out = _normalize_run(data, run_id="ai_creators_20260101_000000")
    assert out["bottleneck_stage"] is None
    assert out["bottleneck_reason"] is None


def test_bottleneck_independent_of_status():
    """``bottleneck_stage`` lives independently of the run status. A
    "failed" run can carry a bottleneck (the normal case); a "success"
    run carries None for both bottleneck fields.

    Pin both shapes so a refactor that conflates them trips here.
    """
    # failed + bottleneck (normal zero-blueprint shape)
    failed_data = {
        "status": "failed",
        "bottleneck_stage": "pre_download_dedup",
        "bottleneck_reason": "all 5 stories dropped by URL/video-id dedup",
        "metrics": {"blueprints_count": 0, "stories_count": 0},
    }
    failed = _normalize_run(failed_data, run_id="gaming_20260623_152725")
    assert failed["bottleneck_stage"] == "pre_download_dedup"

    # success + no bottleneck (normal happy-path shape)
    success_data = {
        "status": "success",
        "bottleneck_stage": None,
        "bottleneck_reason": None,
        "metrics": {"blueprints_count": 5, "stories_count": 5},
    }
    success = _normalize_run(success_data, run_id="anime_20260623_182807")
    assert success["bottleneck_stage"] is None
    assert success["status"] == "success"


def test_bottleneck_stage_covers_all_canonical_values():
    """Pin the contract: every documented bottleneck_stage enum value
    propagates through the normalizer unchanged. Catches a silent enum
    rename in genlab-core or a normalizer that whitelists only some.
    """
    canonical_values = (
        "fetch",
        "pre_download_dedup",
        "video_gate",
        "video_validation",
        "push_to_backlog",
    )
    for stage_name in canonical_values:
        data = {
            "status": "failed",
            "bottleneck_stage": stage_name,
            "bottleneck_reason": f"blocked at {stage_name}",
            "metrics": {"blueprints_count": 0, "stories_count": 0},
        }
        out = _normalize_run(data, run_id=f"x_{stage_name}")
        assert out["bottleneck_stage"] == stage_name, (
            f"normalizer must round-trip canonical value '{stage_name}' "
            "unchanged — see genlab_core/pipeline/stages/run_report.py for "
            "the enum producer."
        )
