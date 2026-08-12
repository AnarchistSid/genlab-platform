"""2026-08-12: pin the render_qc soft-signal check in auto_approval_gate.

Motivating chain: Lever G3 (`render_qc`) ships since PR #432,
runs a Claude Vision ensemble over 3 frames of rendered MP4, produces
`min_quality_score` (0-10). Prior gap: verdict landed on
`story["media"]["video_validation"]["render_qc"]` at pipeline time
but was NEVER persisted to blueprint.extra, so `auto_approval_gate`
had no way to read it. Verdict was purely observational for weeks.

This ship closes the loop:
* push_to_backlog persists `render_qc_min_score` to blueprints.extra
* auto_approval_gate reads it as check #7 (soft signal, same pattern
  as hook_classifier: contributes to confidence, never fails alone)

Consumer wire matches the "shipped observability BEFORE consumer"
pattern from CLAUDE.md — G3 shipped in one commit, gate integration
lands here.
"""

from __future__ import annotations


class TestRenderQCGateCheck:
    def _base_blueprint(self, extra_overrides: dict) -> dict:
        """Blueprint with the fields the gate expects, plus overridable
        extra JSONB for scenario-specific values."""
        extra = {
            "composite_score": 0.5,
            "virality_score": 0.10,
            "visual_paths": ["/tmp/x.mp4"],
            "validation_status": {"all_passed": True},
            "hook_classifier_score": 0.55,
        }
        extra.update(extra_overrides)
        return {
            "id": "bp-test",
            "niche_id": "anime",
            "status": "VISUAL_READY",
            "hook_text": "Test hook that is long enough",
            "title": "Test",
            "arm_id": "test_arm",
            "candidate_id": "test-cand",
            "extra": extra,
        }

    def test_render_qc_strong_boosts_confidence(self):
        from genlab_core.scheduling.auto_approval_gate import evaluate

        # min_quality_score = 8.5/10 (strong)
        bp = self._base_blueprint({"render_qc_min_score": 8.5})
        d = evaluate(bp)

        assert "render_qc" in d.passed_checks
        # Score >= 7 contributes >=0.9 confidence
        # Aggregate confidence should be higher than baseline
        bp_base = self._base_blueprint({})
        d_base = evaluate(bp_base)
        assert d.confidence > d_base.confidence, (
            f"strong render_qc must lift confidence; "
            f"with_qc={d.confidence} vs without={d_base.confidence}"
        )

    def test_render_qc_borderline_contributes_normalized(self):
        from genlab_core.scheduling.auto_approval_gate import evaluate

        bp = self._base_blueprint({"render_qc_min_score": 6.0})
        d = evaluate(bp)

        # Borderline: not in passed_checks (need >=7), not in failed
        assert "render_qc" not in d.passed_checks
        assert "render_qc" not in d.failed_checks
        assert any("borderline" in r or "6.0" in r for r in d.reasons)

    def test_render_qc_weak_drags_confidence_no_hard_reject(self):
        """Weak vision-judge score (< 5) drags confidence down but
        does NOT hard-fail the gate. Vision judge is high-recall
        low-precision — a false-negative would over-suppress good
        content."""
        from genlab_core.scheduling.auto_approval_gate import evaluate

        bp = self._base_blueprint({"render_qc_min_score": 3.0})
        d = evaluate(bp)

        # Not in failed_checks — soft signal only
        assert "render_qc" not in d.failed_checks
        # Confidence reflects the low score
        bp_base = self._base_blueprint({})
        d_base = evaluate(bp_base)
        # Weak render_qc contributes 0.3 confidence, dragging aggregate
        # below baseline (which has one less contributor at ~0.5-0.7)
        assert d.confidence <= d_base.confidence

    def test_render_qc_missing_no_contribution(self):
        """Cold-start / flag-off case: render_qc absent → no
        contribution. Preserves gate behavior for pre-Lever-G3 posts."""
        from genlab_core.scheduling.auto_approval_gate import evaluate

        bp = self._base_blueprint({})  # no render_qc_min_score
        d = evaluate(bp)

        assert "render_qc" not in d.passed_checks
        assert "render_qc" not in d.failed_checks
        # The gate should still resolve (approved based on other checks)
        assert any("render_qc_min_score missing" in r for r in d.reasons)

    def test_render_qc_out_of_range_clamps(self):
        """Defensive: score > 10 or < 0 clamps to bounds."""
        from genlab_core.scheduling.auto_approval_gate import evaluate

        # Absurd high score should clamp to 10 -> strong pass
        bp_high = self._base_blueprint({"render_qc_min_score": 999.0})
        d_high = evaluate(bp_high)
        assert "render_qc" in d_high.passed_checks

        # Negative score should clamp to 0 -> weak
        bp_neg = self._base_blueprint({"render_qc_min_score": -5.0})
        d_neg = evaluate(bp_neg)
        assert "render_qc" not in d_neg.passed_checks
        assert "render_qc" not in d_neg.failed_checks


class TestPushToBacklogPersistsRenderQC:
    """render_qc verdict must be persisted from story.media to
    blueprint.extra so auto_approval_gate can read it."""

    def test_push_to_backlog_source_references_render_qc(self):
        """Source-inspection pin: push_to_backlog must reference
        `render_qc_min_score` in a field-persist context (not just
        a comment). Structural check because the actual push is a
        complex integration path."""
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "genlab_core"
            / "pipeline"
            / "stages"
            / "push_to_backlog.py"
        )
        content = src.read_text()

        # Must be present AS a field key
        assert '"render_qc_min_score":' in content, (
            "push_to_backlog must persist render_qc_min_score to "
            "blueprints.extra. Without this write, auto_approval_gate's "
            "render_qc check gets None → no contribution → G3 shipped "
            "code goes unused."
        )
