"""Pin: G3 render_qc wire into validate_videos (Lever G3 wire).

PR #440 shipped ``render_qc.qc_render`` (frame-ensemble vision QC) but
with ZERO production callers. THIS test file pins the FIRST production
caller — ``_run_render_qc`` invoked from ``validate_videos`` after
spec checks pass.

The wire records the report under
``media["video_validation"]["render_qc"]`` but does NOT block publish
on a bad verdict — surfacing the signal is step 1; gating publication
is a follow-up PR after operators calibrate thresholds.

These pins lock in:

  Default-off (1):
  1. env unset → no-op, media untouched

  Wire integration (4):
  2. env=1 + report returned → record stashed on media with structured fields
  3. env=1 + qc_render returns None (fail-open) → no record added
  4. env=1 + ImportError on render_qc → no-op (graceful degradation)
  5. blueprint_id falls back to candidate_id when blueprint_id missing

  Non-blocking guarantee (1):
  6. report with publishable=False does NOT remove valid=True from media
     (this PR surfaces signal; gating is future PR)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.pipeline.stages.validate_videos import _run_render_qc


class TestDefaultOff:
    def test_env_unset_is_no_op(self, monkeypatch):
        """Pin: with GENLAB_RENDER_QC_ENABLED unset, the helper does
        nothing — no media mutation, no qc_render call. Zero overhead
        on the existing validate_videos hot path when disabled."""
        monkeypatch.delenv("GENLAB_RENDER_QC_ENABLED", raising=False)

        media: dict = {}
        with patch("genlab_core.media.render_qc.qc_render") as mock_qc:
            _run_render_qc(media, {"hook": "x"}, "/path/to/v.mp4")
        assert media == {}
        mock_qc.assert_not_called()


class TestWireIntegration:
    def test_report_stashed_on_media_when_qc_runs(self, monkeypatch):
        """Pin: env on + qc_render returns a report → report fields
        stashed under media[video_validation][render_qc] with the
        structured shape the dashboard + downstream consumers expect."""
        monkeypatch.setenv("GENLAB_RENDER_QC_ENABLED", "1")

        fake_report = MagicMock()
        fake_report.frames_judged = 3
        fake_report.min_quality_score = 6.5
        fake_report.avg_quality_score = 8.0
        fake_report.unique_issues = ["low_contrast"]
        fake_report.publishable = True
        fake_report.recommendation = "publish"

        media: dict = {}
        story = {"blueprint_id": "bp_001", "hook": "test hook", "niche_id": "gaming"}

        with patch("genlab_core.media.render_qc.qc_render", return_value=fake_report):
            _run_render_qc(media, story, "/path/to/v.mp4")

        assert "video_validation" in media
        rq = media["video_validation"]["render_qc"]
        assert rq["frames_judged"] == 3
        assert rq["min_quality_score"] == 6.5
        assert rq["avg_quality_score"] == 8.0
        assert rq["unique_issues"] == ["low_contrast"]
        assert rq["publishable"] is True
        assert rq["recommendation"] == "publish"

    def test_qc_render_returns_none_no_record_added(self, monkeypatch):
        """Pin: qc_render's fail-open (returns None when env unset
        inside the module, no API key, ffmpeg missing, etc.) → no
        record added. Caller proceeds with the rest of validation."""
        monkeypatch.setenv("GENLAB_RENDER_QC_ENABLED", "1")

        media: dict = {}
        with patch("genlab_core.media.render_qc.qc_render", return_value=None):
            _run_render_qc(media, {"hook": "x"}, "/path/to/v.mp4")
        # No render_qc key added when None
        assert media.get("video_validation", {}).get("render_qc") is None

    def test_import_error_is_graceful(self, monkeypatch):
        """Pin: if render_qc module can't be imported (deploy in
        progress, version mismatch, etc.) → no-op, no crash. Pipeline
        validation continues uninterrupted."""
        monkeypatch.setenv("GENLAB_RENDER_QC_ENABLED", "1")

        media: dict = {}
        # Patch the import statement inside _run_render_qc by removing
        # the module attribute temporarily
        import sys

        original = sys.modules.get("genlab_core.media.render_qc")
        sys.modules["genlab_core.media.render_qc"] = None  # type: ignore[assignment]
        try:
            _run_render_qc(media, {"hook": "x"}, "/path/to/v.mp4")
        finally:
            if original is not None:
                sys.modules["genlab_core.media.render_qc"] = original
            else:
                sys.modules.pop("genlab_core.media.render_qc", None)

        # Wire silently no-op'd — no crash, no record
        assert media.get("video_validation", {}).get("render_qc") is None

    def test_blueprint_id_falls_back_to_candidate_id(self, monkeypatch):
        """Pin: stories pre-publish often have candidate_id but not
        yet blueprint_id. Wire falls back so the QC report is still
        usefully tagged."""
        monkeypatch.setenv("GENLAB_RENDER_QC_ENABLED", "1")

        captured = {}

        def fake_qc(video_path, **kwargs):
            captured["blueprint_id"] = kwargs.get("blueprint_id")
            return None  # we only care about the args, not the result

        story = {"candidate_id": "cand_999", "hook": "x"}  # no blueprint_id
        with patch("genlab_core.media.render_qc.qc_render", side_effect=fake_qc):
            _run_render_qc({}, story, "/path/to/v.mp4")
        assert captured["blueprint_id"] == "cand_999"


class TestNonBlockingGuarantee:
    def test_bad_render_qc_verdict_does_not_remove_valid(self, monkeypatch):
        """Pin: even when render_qc says publishable=False, the wire
        only ADDS the render_qc key — it does NOT touch media[video_validation][valid].
        Gating publication on render_qc is a future PR; this PR is
        observability-only."""
        monkeypatch.setenv("GENLAB_RENDER_QC_ENABLED", "1")

        fake_report = MagicMock()
        fake_report.frames_judged = 3
        fake_report.min_quality_score = 2.0  # very bad
        fake_report.avg_quality_score = 2.5
        fake_report.unique_issues = ["logo_obscured", "hook_unreadable"]
        fake_report.publishable = False  # would-be reject
        fake_report.recommendation = "regenerate"

        # Pre-populate media as if spec checks already passed (valid=True)
        media: dict = {"video_validation": {"valid": True, "issues": []}}

        with patch("genlab_core.media.render_qc.qc_render", return_value=fake_report):
            _run_render_qc(media, {"hook": "x"}, "/path/to/v.mp4")

        # Pre-existing valid=True PRESERVED — wire is observability-only
        assert media["video_validation"]["valid"] is True
        # New render_qc record added with bad verdict for dashboard visibility
        assert media["video_validation"]["render_qc"]["publishable"] is False
        assert media["video_validation"]["render_qc"]["recommendation"] == "regenerate"
