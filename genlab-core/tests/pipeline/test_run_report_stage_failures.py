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


class TestZeroBlueprintsSLOMessageAccuracy:
    """Pin the 2026-06-20 SLO-message-accuracy fix.

    Pre-fix, when a pipeline produced 0 blueprints AND ``len(stories)``
    at run_report time was 0, the SLO message hardcoded "no stories
    fetched (total fetch wipeout: WARP/quota/relevance gate?)".

    But ``stories`` here is the POST-VideoGate filtered list (see
    ``video_gate.py:228`` filter). A sports run that fetched 10
    stories, scored down to 5, dropped 3 via URL dedup, and lost the
    remaining 2 at VideoGate would arrive at run_report with
    ``len(stories) == 0`` — emitting the misleading "fetch wipeout"
    message and sending operators chasing WARP/quota/relevance when
    the REAL cause was VideoSourcer failure (Reddit IP block,
    YouTube search not matching titles, etc.).

    The fix distinguishes by reading ``run_stats["video_gate"]
    .skipped`` — when >0, stories DID reach VideoGate but their
    clips weren't available; emit a message that points the operator
    at VideoSourcer instead.
    """

    def test_true_fetch_wipeout_keeps_original_message(self):
        """No stories at all + no video_gate stats → 'fetch wipeout'
        message stays. Pin backward compat for the pre-existing case
        where the original SLO message IS accurate."""
        report = _run(
            _ctx(
                blueprints_pushed=0,
            )
        )
        slo_v = report.get("slo_violations", [])
        assert any("no stories fetched" in v and "total fetch wipeout" in v for v in slo_v), (
            f"True fetch wipeout (no stories, no video_gate) MUST keep the original "
            f"message. Got: {slo_v}"
        )

    def test_video_gate_drops_all_emits_sourcing_message(self):
        """The exact prod symptom: stories fetched + scored + reached
        VideoGate, but ALL got dropped for missing clips. The SLO
        message must point at video sourcing, NOT fetch wipeout.
        """
        report = _run(
            _ctx(
                blueprints_pushed=0,
                video_gate={"passed": 0, "skipped": 2},
            )
        )
        slo_v = report.get("slo_violations", [])
        # Must NOT say "fetch wipeout" — that's the bug
        assert not any("fetch wipeout" in v for v in slo_v), (
            f"With video_gate.skipped>0, the SLO message must NOT use the "
            f"'fetch wipeout' wording — fetch DID succeed. Got: {slo_v}"
        )
        # Must mention VideoGate / video sourcing
        sourcing_msg = [v for v in slo_v if "VideoGate" in v or "sourcing" in v]
        assert sourcing_msg, f"Must emit a video-sourcing-specific SLO message. Got: {slo_v}"
        # The count must be in the message (operator wants to see "2 stories")
        assert "2/2" in sourcing_msg[0]

    def test_partial_video_gate_skip_still_uses_sourcing_message(self):
        """Mixed case: video_gate passed=1 + skipped=3, but blueprints=0
        anyway (downstream failure). The dominant cause was VideoGate
        dropping 3/4, so the sourcing message is still the right pointer.
        """
        report = _run(
            _ctx(
                blueprints_pushed=0,
                video_gate={"passed": 1, "skipped": 3},
            )
        )
        slo_v = report.get("slo_violations", [])
        sourcing_msg = [v for v in slo_v if "VideoGate" in v or "sourcing" in v]
        assert sourcing_msg
        # The "3/4" shows BOTH the skipped count and the total at VideoGate
        assert "3/4" in sourcing_msg[0]

    def test_post_videogate_stories_keeps_existing_message(self):
        """If ``len(stories) > 0`` at run_report time (some made it
        past VideoGate), the existing 'from N stories' message wins
        — that's already informative. Pin the precedence."""
        report = _run(
            _ctx(
                blueprints_pushed=0,
                video_gate={"passed": 1, "skipped": 2},
            )
        )
        # _ctx with blueprints_pushed=0 sets stories=[] by default
        # Override here to test the "stories survived" branch
        ctx_with_survivors = _ctx(blueprints_pushed=0, video_gate={"passed": 1, "skipped": 2})
        ctx_with_survivors["stories"] = [{"story_id": "a"}]
        report = _run(ctx_with_survivors)
        slo_v = report.get("slo_violations", [])
        # Old "from N stories" message takes precedence
        assert any("from 1 stories" in v for v in slo_v), (
            f"When len(stories)>0 at run_report, the existing message wins. Got: {slo_v}"
        )


# ---------------------------------------------------------------------------
# PR #504 (2026-06-23) — extended stage_failures tracking + bottleneck_stage.
#
# The pre-PR loop only tracked 6 stages (video_validation, audio, text_
# overlays, whisper_captions, publishing, qc). PR #504 extends to 4 more
# (video_gate, relevance_gate, pre_download_dedup, push_to_backlog) AND
# adds a structured ``bottleneck_stage`` field that names which filter
# was the funnel killer on zero-blueprint runs.
# ---------------------------------------------------------------------------


class TestExtendedStageFailuresLoop:
    """Pin that the loop covers all filter/gate stages, not just the
    post-render handful. Today these stages don't emit failed/errors
    keys (they fail-open or pass-through), so the assertions use the
    forward-visibility shape: if a stage DOES report failed/errors,
    it surfaces in ``stage_failures``.
    """

    def test_video_gate_failures_surface(self):
        report = _run(_ctx(blueprints_pushed=2, video_gate={"failed": 1, "errors": 0}))
        assert report["stage_failures"].get("video_gate") == 1

    def test_relevance_gate_failures_surface(self):
        report = _run(_ctx(blueprints_pushed=2, relevance_gate={"errors": 3}))
        assert report["stage_failures"].get("relevance_gate") == 3

    def test_pre_download_dedup_failures_surface(self):
        report = _run(_ctx(blueprints_pushed=2, pre_download_dedup={"failed": 2, "errors": 1}))
        assert report["stage_failures"].get("pre_download_dedup") == 3

    def test_push_to_backlog_failures_surface(self):
        report = _run(_ctx(blueprints_pushed=1, push_to_backlog={"errors": 2}))
        assert report["stage_failures"].get("push_to_backlog") == 2

    def test_new_stages_with_zero_failures_stay_absent(self):
        """The loop should only add stages with non-zero failures —
        keeps stage_failures dict readable on dashboard."""
        report = _run(
            _ctx(
                blueprints_pushed=2,
                video_gate={"passed": 2, "skipped": 0},  # normal-success shape
                pre_download_dedup={"input_count": 2, "kept_count": 2, "dropped_url": 0},
            )
        )
        assert "video_gate" not in report["stage_failures"]
        assert "pre_download_dedup" not in report["stage_failures"]


class TestBottleneckStageField:
    """Pin the new structured ``bottleneck_stage`` + ``bottleneck_reason``
    fields that surface WHICH stage was the funnel killer on zero-
    blueprint runs. Dashboard reads these for a clean "blocked at X"
    badge instead of parsing slo_violations strings.
    """

    def test_clean_run_has_no_bottleneck(self):
        """Successful run with blueprints pushed → no bottleneck."""
        report = _run(_ctx(blueprints_pushed=3))
        assert report["bottleneck_stage"] is None
        assert report["bottleneck_reason"] is None

    def test_fetch_wipeout_detected(self):
        """0 stories + no video_gate stats → fetch bottleneck."""
        report = _run(_ctx(blueprints_pushed=0))
        assert report["bottleneck_stage"] == "fetch"
        assert "no stories fetched" in report["bottleneck_reason"]

    def test_pre_download_dedup_bottleneck_detected(self):
        """All stories dropped by URL dedup → pre_download_dedup bottleneck."""
        ctx = _ctx(
            blueprints_pushed=0,
            pre_download_dedup={
                "input_count": 5,
                "kept_count": 0,
                "dropped_url": 5,
                "dropped_video_id": 0,
            },
        )
        ctx["stories"] = []
        report = _run(ctx)
        assert report["bottleneck_stage"] == "pre_download_dedup"
        assert "5 stories" in report["bottleneck_reason"]
        assert "url_dedup_ttl_days" in report["bottleneck_reason"]

    def test_video_gate_bottleneck_detected(self):
        """All stories clipless → video_gate bottleneck.
        Replicates today's pre-fix sports outage shape (PR #501)."""
        ctx = _ctx(
            blueprints_pushed=0,
            video_gate={"passed": 0, "skipped": 2},
        )
        ctx["stories"] = []
        report = _run(ctx)
        assert report["bottleneck_stage"] == "video_gate"
        assert "2/2 stories" in report["bottleneck_reason"]
        assert "VideoSourcer" in report["bottleneck_reason"]

    def test_first_match_wins_pre_download_over_video_gate(self):
        """When multiple filters could be the bottleneck, the EARLIEST
        pipeline-position one wins. The earliest drop is the most
        actionable signal — fixing it unblocks downstream stages."""
        ctx = _ctx(
            blueprints_pushed=0,
            pre_download_dedup={"input_count": 5, "kept_count": 0, "dropped_url": 5},
            video_gate={"passed": 0, "skipped": 2},
        )
        ctx["stories"] = []
        report = _run(ctx)
        # pre_download_dedup runs BEFORE video_gate — it wins
        assert report["bottleneck_stage"] == "pre_download_dedup"
