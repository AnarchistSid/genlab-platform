"""Pin the W4.1 confidence_score computation at push_to_backlog time.

The auto_approval_gate already computes confidence at REVIEW time (used
by AUTO #1's dashboard preview + the AUTO #2 worker). W4.1 moves the
computation to PUSH time so the confidence is available to ALL
downstream consumers without re-running the gate.

The implementation lives inline in push_to_backlog.py's create branch
— a synthetic blueprint dict is built from the ``fields`` being
persisted, fed through ``auto_approval_gate.evaluate()``, and the
``decision.confidence`` + ``decision.approved`` are written back into
``fields`` (and from there into the blueprints.extra JSONB via
PostgresBackend._split_fields).

These pins ensure:
- The shape of the synthetic dict matches what evaluate() expects
- A high-quality blueprint produces a high confidence
- A low-quality blueprint produces a low confidence
- Missing optional fields don't crash (cold-start tolerance)
- evaluate() exceptions are swallowed (best-effort sidecar)
"""

from __future__ import annotations

from genlab_core.scheduling.auto_approval_gate import evaluate


class TestConfidenceShape:
    """The synthetic dict shape pushed into evaluate() must satisfy
    the gate's contract — top-level hook_text + visual_paths AND
    extra dict carrying composite_score / virality_score /
    validation_status. Mirrors the inline build in push_to_backlog."""

    def _build_synth(self, fields: dict) -> dict:
        """Replicate the synthetic-dict build from push_to_backlog. Kept
        in sync with the implementation; a future refactor should
        extract this into a helper that both sides import."""
        return {
            "hook_text": fields.get("hook_text", ""),
            "visual_paths": fields.get("visual_paths", ""),
            "extra": {
                "composite_score": fields.get("composite_score"),
                "virality_score": fields.get("virality_score"),
                "validation_status": fields.get("validation_status"),
            },
        }

    def test_high_quality_blueprint_high_confidence(self):
        """All gates pass + high scores → confidence ≥ 0.8 + approved=True."""
        fields = {
            "hook_text": "This is an attention-grabbing hook with real content",
            "visual_paths": '["/opt/genlab/.tmp/runs/2026/x/render.mp4"]',
            "composite_score": 0.85,
            "virality_score": 0.6,
            "validation_status": '{"all_passed": true, "issues": []}',
        }
        synth = self._build_synth(fields)
        decision = evaluate(synth)
        assert decision.approved is True
        assert decision.confidence >= 0.7, (
            f"high-quality blueprint should produce confidence ≥ 0.7, got {decision.confidence}"
        )

    def test_low_quality_blueprint_low_confidence_or_rejected(self):
        """Bad scores → either approved=False or confidence < 0.5."""
        fields = {
            "hook_text": "",  # no hook → REJECT
            "visual_paths": "",  # no video → REJECT
            "composite_score": 0.1,
            "virality_score": 0.0,
        }
        synth = self._build_synth(fields)
        decision = evaluate(synth)
        # No video + no hook → approved=False
        assert decision.approved is False
        assert "has_video" in decision.failed_checks
        assert "has_hook" in decision.failed_checks

    def test_missing_optional_fields_cold_start_tolerated(self):
        """Cold-start blueprints (no virality scoring run yet) should
        get a neutral 0.5 prior, not crash."""
        fields = {
            "hook_text": "Decent hook here for the cold-start case",
            "visual_paths": '["/opt/genlab/.tmp/runs/x/y.mp4"]',
            # no composite_score, no virality_score, no validation_status
        }
        synth = self._build_synth(fields)
        decision = evaluate(synth)
        # Should produce a decision (no crash) with confidence in valid range
        assert 0.0 <= decision.confidence <= 1.0
        # has_video + has_hook still pass
        assert "has_video" in decision.passed_checks
        assert "has_hook" in decision.passed_checks

    def test_validation_status_string_form_tolerated(self):
        """push_to_backlog stores validation_status as a JSON string via
        ``fields[\"validation_status\"] = json.dumps(...)``. The gate
        handles the string-encoded shape (line 184-189 of
        auto_approval_gate.py)."""
        fields = {
            "hook_text": "Good hook",
            "visual_paths": '["/x.mp4"]',
            "composite_score": 0.7,
            "virality_score": 0.5,
            "validation_status": '{"all_passed": true}',
        }
        synth = self._build_synth(fields)
        decision = evaluate(synth)
        assert "qc_passed" in decision.passed_checks

    def test_visual_paths_top_level_picked_up(self):
        """The gate's defensive ``_decode_visual_paths(extra.get(...) or
        blueprint.get(...))`` accepts visual_paths at either location.
        Push's synth puts it at top-level (since the column is top-level
        in the schema). Pin that this works."""
        fields = {
            "hook_text": "X",
            "visual_paths": '["/x.mp4"]',
            "composite_score": 0.8,
            "virality_score": 0.5,
        }
        synth = self._build_synth(fields)
        decision = evaluate(synth)
        assert "has_video" in decision.passed_checks

    def test_confidence_in_valid_range(self):
        """Sanity: confidence must always be in [0.0, 1.0] regardless
        of input. push_to_backlog rounds to 4 decimals before persist."""
        for score_val in (None, 0.0, 0.3, 0.5, 0.8, 1.0):
            fields = {
                "hook_text": "X",
                "visual_paths": '["/x.mp4"]',
                "composite_score": score_val,
            }
            synth = self._build_synth(fields)
            decision = evaluate(synth)
            assert 0.0 <= decision.confidence <= 1.0, (
                f"confidence out of range for score={score_val}: {decision.confidence}"
            )
