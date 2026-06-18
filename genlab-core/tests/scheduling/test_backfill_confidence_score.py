"""Pin the W4.4 backfill script's synthetic-blueprint shape.

The script reads a Postgres row + builds a synthetic blueprint dict to
feed to `auto_approval_gate.evaluate()`. The synth-dict shape MUST
match what W4.1 push_to_backlog builds (PR #284) — otherwise backfill-
computed confidence won't match what future blueprints get computed at
push time, breaking historical comparability.

The synth-dict shape is defined in
``scripts/backfill_confidence_score.py:_build_synth_blueprint``;
the original at ``stages/push_to_backlog.py:~1685``.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts/ to path so we can import the backfill module
_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(_SCRIPTS))


class TestSynthBlueprintShape:
    """Pin the synthetic-dict shape against the W4.1 contract.

    auto_approval_gate.evaluate() reads from a documented set of
    paths; the synth-dict must populate them from the Postgres row.
    """

    def _import(self):
        from backfill_confidence_score import _build_synth_blueprint

        return _build_synth_blueprint

    def test_hook_text_pulled_from_top_level(self):
        _build = self._import()
        row = {"hook_text": "Just Chatting", "extra": {}}
        synth = _build(row)
        assert synth["hook_text"] == "Just Chatting"

    def test_visual_paths_from_extra_preferred(self):
        """When both top-level and extra have visual_paths, extra wins
        (matches push_to_backlog's W4.1 behaviour)."""
        _build = self._import()
        row = {
            "hook_text": "hk",
            "visual_paths": '["/topl.mp4"]',
            "extra": {"visual_paths": '["/extra.mp4"]'},
        }
        synth = _build(row)
        assert synth["visual_paths"] == '["/extra.mp4"]'

    def test_visual_paths_falls_back_to_top_level(self):
        _build = self._import()
        row = {"hook_text": "hk", "visual_paths": '["/topl.mp4"]', "extra": {}}
        synth = _build(row)
        assert synth["visual_paths"] == '["/topl.mp4"]'

    def test_extra_scoring_fields_propagated(self):
        """composite_score / virality_score / validation_status are
        read out of extra and re-nested under extra in the synth-dict
        — matches the W4.1 push_to_backlog code at
        stages/push_to_backlog.py."""
        _build = self._import()
        row = {
            "hook_text": "hk",
            "visual_paths": "",
            "extra": {
                "composite_score": 0.85,
                "virality_score": 0.6,
                "validation_status": '{"qc_passed": true}',
            },
        }
        synth = _build(row)
        assert synth["extra"]["composite_score"] == 0.85
        assert synth["extra"]["virality_score"] == 0.6
        assert synth["extra"]["validation_status"] == '{"qc_passed": true}'

    def test_missing_extra_treated_as_empty(self):
        """Some old rows have NULL extra — gate's cold-start tolerance
        must work."""
        _build = self._import()
        row = {"hook_text": "hk", "visual_paths": "", "extra": None}
        synth = _build(row)
        # All scoring fields should be None (gate treats as unknown)
        assert synth["extra"]["composite_score"] is None
        assert synth["extra"]["virality_score"] is None

    def test_non_dict_extra_treated_as_empty(self):
        """Defensive: bad extra type → empty extra, not exception."""
        _build = self._import()
        row = {"hook_text": "hk", "visual_paths": "", "extra": "not-a-dict"}
        synth = _build(row)
        # Should not crash; should return empty extra
        assert synth["extra"]["composite_score"] is None

    def test_full_end_to_end_evaluate_returns_confidence(self):
        """The synth-dict must be acceptable input to evaluate() —
        end-to-end sanity check that exercises the actual gate."""
        from genlab_core.scheduling.auto_approval_gate import evaluate

        _build = self._import()
        row = {
            "hook_text": "GTA 6 release date leaked",
            "visual_paths": "",
            "extra": {
                "composite_score": 0.85,
                "virality_score": 0.6,
                "validation_status": '{"qc_passed": true}',
                "visual_paths": '["/runs/x/visuals/abc/reel.mp4"]',
            },
        }
        synth = _build(row)
        decision = evaluate(synth)
        # Should be a valid AutoApprovalDecision with a numeric confidence
        assert 0.0 <= decision.confidence <= 1.0
        assert isinstance(decision.approved, bool)
        assert isinstance(decision.passed_checks, list)


class TestBackfillIdempotency:
    """The backfill SQL must be idempotent: re-running it on a row
    that already has the confidence field MUST be a no-op (no row
    update, no change). Pinned via the WHERE clause ``NOT (extra ?
    'auto_approval_confidence')``.
    """

    def test_where_clause_excludes_already_backfilled(self):
        """Pin the WHERE clause as-shipped — anyone changing it must
        also update this pin or risk re-backfilling (overwriting
        push-time-computed values with backfill-time values, which is
        the SAME math but the timestamp differs)."""
        import inspect

        import backfill_confidence_score as mod

        source = inspect.getsource(mod.main)
        # Pin the exact WHERE clause shape
        assert "NOT (extra ? 'auto_approval_confidence')" in source
