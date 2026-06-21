"""Pin: post-render video QC frame-ensemble judge (Lever G3).

Lever G2 (PR #432) judges ONE frame. Lever G3 extends that to the
full rendered video by extracting 3 frames at fixed positions
(start/middle/end), running each through G2's judge, and ensembling
the verdicts into a single RenderQCReport.

These pins lock in:

  Opt-in (2):
  1. Default off (env unset) → is_enabled False
  2. env=1 → is_enabled True

  aggregate_verdicts pure logic (5):
  3. Empty verdicts → frames_judged=0, recommendation="unknown"
  4. min_quality_score is the WORST frame (bottleneck)
  5. avg_quality_score is the mean
  6. unique_issues deduplicated across frames, order-preserved
  7. publishable threshold gates recommendation publish/regenerate

  qc_render orchestrator fail-open (4):
  8. env unset → None
  9. video missing → None (probe_video_duration returns None)
  10. extract_frame fails for all positions → None
  11. vision_judge returns None for all frames → None

  qc_render happy path (1):
  12. All 3 frames extract + judge → RenderQCReport with frames_judged=3
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class _FakeVerdict:
    """Stand-in for VisionVerdict in aggregate_verdicts tests — no
    need to import the real dataclass here."""

    def __init__(self, score: float, issues: list[str], confidence: float = 0.8):
        self.quality_score = score
        self.issues = issues
        self.confidence = confidence


class TestIsEnabled:
    def test_default_off(self, monkeypatch):
        from genlab_core.media.render_qc import is_enabled

        monkeypatch.delenv("GENLAB_RENDER_QC_ENABLED", raising=False)
        assert is_enabled() is False

    def test_env_one_is_on(self, monkeypatch):
        from genlab_core.media.render_qc import is_enabled

        monkeypatch.setenv("GENLAB_RENDER_QC_ENABLED", "1")
        assert is_enabled() is True


class TestAggregateVerdicts:
    def test_empty_returns_unknown_report(self):
        """Pin: no verdicts → frames_judged=0, recommendation='unknown'.
        Caller treats unknown as 'no signal' and falls back to manual
        review."""
        from genlab_core.media.render_qc import aggregate_verdicts

        report = aggregate_verdicts("/tmp/video.mp4", "bp_001", [])
        assert report.frames_judged == 0
        assert report.min_quality_score == 0.0
        assert report.avg_quality_score == 0.0
        assert report.unique_issues == []
        assert report.publishable is False
        assert report.recommendation == "unknown"

    def test_min_score_is_worst_frame(self):
        """Pin: min_quality_score is the bottleneck. Even if 2 frames
        are 9.0, one 3.0 frame should pull the min down to 3.0."""
        from genlab_core.media.render_qc import aggregate_verdicts

        verdicts = [
            _FakeVerdict(9.0, []),
            _FakeVerdict(3.0, ["logo_obscured"]),
            _FakeVerdict(9.0, []),
        ]
        report = aggregate_verdicts("/tmp/v.mp4", "bp_001", verdicts)
        assert report.min_quality_score == 3.0
        assert report.frames_judged == 3

    def test_avg_score_is_mean(self):
        from genlab_core.media.render_qc import aggregate_verdicts

        verdicts = [_FakeVerdict(6.0, []), _FakeVerdict(8.0, []), _FakeVerdict(4.0, [])]
        report = aggregate_verdicts("/tmp/v.mp4", "bp_001", verdicts)
        assert report.avg_quality_score == 6.0  # (6+8+4)/3

    def test_unique_issues_deduplicated_and_order_preserved(self):
        """Pin: same issue in multiple frames appears once. Order
        preserved by first appearance — frame 1's issues come first."""
        from genlab_core.media.render_qc import aggregate_verdicts

        verdicts = [
            _FakeVerdict(5.0, ["low_contrast", "logo_obscured"]),  # frame 1
            _FakeVerdict(5.0, ["low_contrast", "hook_unreadable"]),  # frame 2
            _FakeVerdict(5.0, ["logo_obscured"]),  # frame 3 (dup)
        ]
        report = aggregate_verdicts("/tmp/v.mp4", "bp_001", verdicts)
        # Order: low_contrast (frame 1 first), logo_obscured (frame 1
        # second), hook_unreadable (frame 2 first)
        assert report.unique_issues == ["low_contrast", "logo_obscured", "hook_unreadable"]

    def test_publishable_threshold_gates_recommendation(self):
        """Pin: min_quality_score >= threshold → publish, else regenerate."""
        from genlab_core.media.render_qc import aggregate_verdicts

        # Worst frame 6.0, threshold 5.0 → publishable
        good = aggregate_verdicts(
            "/tmp/v.mp4",
            "bp_001",
            [_FakeVerdict(6.0, []), _FakeVerdict(7.0, [])],
            min_publishable=5.0,
        )
        assert good.publishable is True
        assert good.recommendation == "publish"

        # Worst frame 4.0, threshold 5.0 → not publishable
        bad = aggregate_verdicts(
            "/tmp/v.mp4",
            "bp_001",
            [_FakeVerdict(4.0, []), _FakeVerdict(8.0, [])],
            min_publishable=5.0,
        )
        assert bad.publishable is False
        assert bad.recommendation == "regenerate"


class TestQcRenderFailOpen:
    def test_returns_none_when_env_unset(self, monkeypatch, tmp_path):
        from genlab_core.media.render_qc import qc_render

        monkeypatch.delenv("GENLAB_RENDER_QC_ENABLED", raising=False)
        # Even with a valid-looking path, env unset → None
        assert qc_render(tmp_path / "v.mp4", "bp_001") is None

    def test_missing_video_returns_none(self, monkeypatch, tmp_path):
        """Pin: probe_video_duration fails on missing file → None."""
        from genlab_core.media.render_qc import qc_render

        monkeypatch.setenv("GENLAB_RENDER_QC_ENABLED", "1")
        assert qc_render(tmp_path / "does_not_exist.mp4", "bp_001") is None

    def test_all_frame_extracts_fail_returns_none(self, monkeypatch, tmp_path):
        """Pin: probe succeeds but every extract_frame fails → no
        verdicts → None."""
        from genlab_core.media import render_qc

        monkeypatch.setenv("GENLAB_RENDER_QC_ENABLED", "1")

        # Stub probe to return a valid duration
        with patch.object(render_qc, "probe_video_duration", return_value=30.0):
            # Stub extract_frame to always fail
            with patch.object(render_qc, "extract_frame", return_value=False):
                result = render_qc.qc_render(tmp_path / "v.mp4", "bp_001")
        assert result is None

    def test_all_judges_return_none_returns_none(self, monkeypatch, tmp_path):
        """Pin: extracts succeed but vision_judge returns None for
        every frame → no verdicts → report None."""
        from genlab_core.media import render_qc

        monkeypatch.setenv("GENLAB_RENDER_QC_ENABLED", "1")

        with patch.object(render_qc, "probe_video_duration", return_value=30.0):
            with patch.object(render_qc, "extract_frame", return_value=True):
                with patch(
                    "genlab_core.media.vision_judge.judge_frame",
                    return_value=None,
                ):
                    result = render_qc.qc_render(tmp_path / "v.mp4", "bp_001")
        assert result is None


class TestQcRenderHappyPath:
    def test_full_ensemble_produces_report(self, monkeypatch, tmp_path):
        """Pin: probe + 3 extracts + 3 judges → report with frames_judged=3
        and the min/avg/unique_issues fields correctly aggregated."""
        from genlab_core.media import render_qc

        monkeypatch.setenv("GENLAB_RENDER_QC_ENABLED", "1")

        # Fake three verdicts at different quality levels
        fake_verdicts = [
            MagicMock(quality_score=8.0, issues=["low_contrast"], confidence=0.9),
            MagicMock(quality_score=6.0, issues=["logo_obscured"], confidence=0.8),
            MagicMock(quality_score=9.0, issues=[], confidence=0.95),
        ]
        # Call_count drives which verdict is returned per call
        call_idx = [0]

        def fake_judge(*args, **kwargs):
            v = fake_verdicts[call_idx[0]]
            call_idx[0] += 1
            return v

        with patch.object(render_qc, "probe_video_duration", return_value=30.0):
            with patch.object(render_qc, "extract_frame", return_value=True):
                with patch("genlab_core.media.vision_judge.judge_frame", side_effect=fake_judge):
                    report = render_qc.qc_render(
                        tmp_path / "v.mp4",
                        "bp_001",
                        hook_text="test hook",
                        niche_id="gaming",
                    )

        assert report is not None
        assert report.frames_judged == 3
        assert report.min_quality_score == 6.0  # worst frame
        assert report.avg_quality_score == round((8.0 + 6.0 + 9.0) / 3, 2)
        # Issues deduplicated + order-preserved across frames
        assert "low_contrast" in report.unique_issues
        assert "logo_obscured" in report.unique_issues
        assert report.publishable is True  # min=6.0 >= default 5.0
        assert report.recommendation == "publish"
