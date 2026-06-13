"""Regression pin: per-stage failures promote run status from
'success' to 'partial' and surface in `stage_failures` field.

RENDER #2 fix (2026-06-13). Pre-fix, sub-stage failures were invisible
to the run-status determination — a run with 4 blueprints pushed and 2
of them carrying corrupt _captioned.mp4 (from whisper-caption ffmpeg
timeout) still reported `status: "success"`. The dashboard had no
signal that anything was wrong; the operator had to grep journalctl.

After fix: any non-zero `failed` or `errors` count in tracked sub-stages
(video_validation, audio, text_overlays, whisper_captions, publishing, qc)
promotes the status. The specific failure breakdown is exposed via the
new `stage_failures` top-level dict so the dashboard can attribute
correctly.
"""

from __future__ import annotations

from genlab_core.pipeline.stages.run_report import RunReport


def _ctx(blueprints_pushed: int = 1, **stage_stats) -> dict:
    """Build a minimal context that exercises RunReport's status logic.

    blueprints_pushed > 0 means we'd normally be "success"; passing
    failure stats lets the test verify the promotion to "partial".
    """
    base = {
        "niche_id": "ai_creators",
        "niche_config": {},
        "stories": [{"story_id": "x"}] * (1 if blueprints_pushed else 0),
        "run_stats": {
            "backlog_push": {"blueprints_pushed": blueprints_pushed},
            "errors": [],
            "_stage_timings": {"_": 1.0},
            **stage_stats,
        },
    }
    return base


def _run(ctx: dict) -> dict:
    """Run RunReport and return the report dict (via run_stats['report'])."""
    out = RunReport().execute(ctx)
    return out["run_stats"]["report"]


class TestStageFailuresPromoteStatus:
    def test_no_failures_keeps_success(self):
        """Clean run with blueprints pushed → status: success."""
        report = _run(_ctx(blueprints_pushed=1))
        assert report["status"] == "success"
        assert report["stage_failures"] == {}

    def test_whisper_captions_failed_promotes_to_partial(self):
        """The exact pre-fix bug: 4 captioned mp4s produced, 2 corrupt
        (failed) → status was 'success' pre-fix. Now: 'partial'."""
        report = _run(
            _ctx(
                blueprints_pushed=4,
                whisper_captions={
                    "synced": 2,
                    "wpm_fallback": 0,
                    "skipped": 0,
                    "failed": 2,
                },
            )
        )
        assert report["status"] == "partial"
        assert report["stage_failures"]["whisper_captions"] == 2

    def test_audio_errors_promote_to_partial(self):
        report = _run(_ctx(blueprints_pushed=2, audio={"errors": 3, "skipped": 0}))
        assert report["status"] == "partial"
        assert report["stage_failures"]["audio"] == 3

    def test_video_validation_failed_promotes_to_partial(self):
        report = _run(
            _ctx(
                blueprints_pushed=3,
                video_validation={"passed": 1, "failed": 2, "skipped": 0},
            )
        )
        assert report["status"] == "partial"
        assert report["stage_failures"]["video_validation"] == 2

    def test_publishing_errors_promote_to_partial(self):
        report = _run(
            _ctx(
                blueprints_pushed=1,
                publishing={"published": 1, "failed": 1, "errors": 0},
            )
        )
        assert report["status"] == "partial"
        assert report["stage_failures"]["publishing"] == 1

    def test_multiple_stages_failed_all_surface_in_dict(self):
        report = _run(
            _ctx(
                blueprints_pushed=4,
                whisper_captions={"synced": 2, "failed": 2},
                audio={"errors": 1},
                video_validation={"passed": 2, "failed": 2},
            )
        )
        assert report["status"] == "partial"
        assert report["stage_failures"]["whisper_captions"] == 2
        assert report["stage_failures"]["audio"] == 1
        assert report["stage_failures"]["video_validation"] == 2

    def test_zero_blueprints_stays_failed_even_with_stage_failures(self):
        """The R-65 dark-day rule trumps stage failures: 0 blueprints is
        always 'failed', never 'partial'."""
        report = _run(
            _ctx(
                blueprints_pushed=0,
                whisper_captions={"failed": 1},
            )
        )
        assert report["status"] == "failed"
        # stage_failures should still be populated for attribution
        assert report["stage_failures"].get("whisper_captions") == 1

    def test_qc_failures_surface_in_stage_failures(self):
        """RENDER #6's sports run produced QC failures from clipless
        stories that VideoGate now filters out. With the new
        propagation, even pre-filter runs would surface these as
        stage_failures.qc."""
        report = _run(
            _ctx(
                blueprints_pushed=3,
                qc={"passed": 3, "failed": 2, "total": 5, "pass_rate": "60.0%"},
            )
        )
        assert report["status"] == "partial"
        assert report["stage_failures"]["qc"] == 2

    def test_stage_with_zero_failures_not_in_dict(self):
        """Only stages with NON-ZERO failures should appear in
        stage_failures (keeps the field readable on the dashboard)."""
        report = _run(
            _ctx(
                blueprints_pushed=2,
                whisper_captions={"synced": 2, "failed": 0},
                audio={"generated": 2, "errors": 0, "skipped": 0},
            )
        )
        assert report["status"] == "success"
        assert report["stage_failures"] == {}

    def test_top_level_errors_still_promote_to_partial(self):
        """Backward-compat: the pre-existing has_errors path still works.
        Stage_failures and top-level errors are independent signals."""
        report = _run(
            _ctx(
                blueprints_pushed=1,
                errors=["something blew up"],
            )
        )
        # Either errors or stage_failures triggers "partial"
        assert report["status"] == "partial"
