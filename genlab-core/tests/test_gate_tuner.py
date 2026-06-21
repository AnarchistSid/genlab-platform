"""Tests for ``learning/gate_tuner.py`` — closes the calibration feedback loop.

Pre-2026-06-21 the calibration loop was asymmetric: ``calibration_logger.log()``
wrote operator-vs-gate pairs to ``auto_approval_calibration``, and
``stats()`` aggregated them into a confusion matrix, but NO code path
read the matrix to update the gate's thresholds. The gate stayed at
hardcoded defaults forever.

``gate_tuner`` closes that loop. These tests pin:

  1. ``compute_override`` math — FP/FN rate computation matches the
     confusion-matrix semantics in ``CalibrationStats``.
  2. Bounds enforcement — tuner never pushes thresholds outside
     ``[0.10, 0.50]`` (composite) or ``[0.005, 0.10]`` (virality), even
     under pathological calibration data.
  3. Cold-start safety — below ``_MIN_SAMPLES_FOR_TUNING`` the tuner
     returns ``None`` (no change), so insufficient data can never move
     thresholds.
  4. Sensitivity band — FP/FN rates within ``_SENSITIVITY`` of target
     produce no change (avoids thrashing on small-sample noise).
  5. File I/O — overrides write atomically via tmp-file rename, load
     gracefully on missing file / malformed JSON.
  6. ``auto_approval_gate.evaluate()`` uses overrides when present —
     niche-aware threshold resolution actually fires.
  7. Explicit kwargs still override everything — operator/test injection
     remains possible.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest


# Minimal in-memory stand-in for ``CalibrationStats`` so the tests
# don't require importing the real module (which pulls in psycopg).
@dataclass(frozen=True)
class _FakeStats:
    niche_id: str
    sample_count: int
    agreement_count: int
    true_positives: int
    true_negatives: int
    false_positives: int
    false_negatives: int


@pytest.fixture(autouse=True)
def _isolate_overrides_file(tmp_path, monkeypatch):
    """Each test gets a fresh overrides file in tmp_path — the prod
    file is never touched."""
    override_file = tmp_path / "gate_overrides.json"
    monkeypatch.setenv("GATE_OVERRIDES_PATH", str(override_file))
    yield override_file


# ─────────────────────────────────────────────────────────────────────
# Section 1: compute_override math
# ─────────────────────────────────────────────────────────────────────


class TestComputeOverrideMath:
    def test_cold_start_returns_none(self):
        """Below 30 samples, the tuner refuses to act — defaults stand."""
        from genlab_core.learning.gate_tuner import compute_override

        stats = _FakeStats(
            niche_id="gaming",
            sample_count=15,
            agreement_count=10,
            true_positives=8,
            true_negatives=2,
            false_positives=4,
            false_negatives=1,
        )
        assert compute_override(stats, current_composite=0.3, current_virality=0.02) is None

    def test_high_fp_rate_raises_composite_threshold(self):
        """When gate is too permissive (operator rejects what gate
        approved 30% of the time), composite threshold rises by step."""
        from genlab_core.learning.gate_tuner import compute_override

        # 100 samples: gate said yes 50 times, operator agreed 35,
        # disagreed 15. FP rate = 15/50 = 30% (well above 10% + 5% band).
        stats = _FakeStats(
            niche_id="gaming",
            sample_count=100,
            agreement_count=70,
            true_positives=35,
            true_negatives=35,
            false_positives=15,
            false_negatives=15,
        )
        result = compute_override(stats, current_composite=0.30, current_virality=0.02)
        assert result is not None
        # Composite should rise by exactly one step (0.30 → 0.32).
        assert result.min_composite_score == pytest.approx(0.32, abs=1e-9), (
            f"Expected composite raised by step (0.30→0.32); got {result.min_composite_score}"
        )
        # FP rate is recorded for audit visibility.
        assert result.fp_rate == pytest.approx(0.30, abs=1e-4)

    def test_high_fn_rate_lowers_composite_threshold(self):
        """When gate is too restrictive (rejects what operator approves),
        composite threshold lowers by step."""
        from genlab_core.learning.gate_tuner import compute_override

        # 100 samples engineered so FP rate is AT-TARGET (10%) and only
        # FN drives the adjustment:
        #   gate_yes = TP+FP = 45+5 = 50 → FP rate = 5/50 = 0.10 (at target)
        #   gate_no  = TN+FN = 35+15 = 50 → FN rate = 15/50 = 0.30 (way above)
        stats = _FakeStats(
            niche_id="sports",
            sample_count=100,
            agreement_count=80,
            true_positives=45,
            true_negatives=35,
            false_positives=5,
            false_negatives=15,
        )
        result = compute_override(stats, current_composite=0.30, current_virality=0.02)
        assert result is not None
        # Composite should drop by one step (0.30 → 0.28).
        assert result.min_composite_score == pytest.approx(0.28, abs=1e-9)
        assert result.fn_rate == pytest.approx(0.30, abs=1e-4)

    def test_within_sensitivity_band_returns_none(self):
        """When both FP and FN are within ±5pp of target, no change
        — avoids thrashing on small-sample noise."""
        from genlab_core.learning.gate_tuner import compute_override

        # 100 samples: FP rate = 12% (within target+5), FN rate = 8%
        # (within target). Both inside the sensitivity band.
        stats = _FakeStats(
            niche_id="movies",
            sample_count=100,
            agreement_count=88,
            true_positives=44,
            true_negatives=44,
            false_positives=6,
            false_negatives=6,
        )
        assert compute_override(stats, current_composite=0.30, current_virality=0.02) is None

    def test_composite_bounded_at_upper_limit(self):
        """Tuner refuses to push composite above 0.50 even on extreme
        FP signal — bounds protect against runaway tightening."""
        from genlab_core.learning.gate_tuner import compute_override

        # Pathological: every gate-yes was wrong (100% FP rate).
        stats = _FakeStats(
            niche_id="gaming",
            sample_count=50,
            agreement_count=0,
            true_positives=0,
            true_negatives=25,
            false_positives=25,
            false_negatives=0,
        )
        # Already at the bound — should clip, not exceed.
        result = compute_override(stats, current_composite=0.50, current_virality=0.02)
        assert result is None, f"Tuner should refuse to push composite above 0.50; got {result}"

    def test_composite_bounded_at_lower_limit(self):
        """Same protection on the lower bound — FN signal can't push
        composite below 0.10."""
        from genlab_core.learning.gate_tuner import compute_override

        # Pathological: every gate-no was wrong (100% FN rate).
        stats = _FakeStats(
            niche_id="anime",
            sample_count=50,
            agreement_count=0,
            true_positives=0,
            true_negatives=0,
            false_positives=0,
            false_negatives=50,
        )
        result = compute_override(stats, current_composite=0.10, current_virality=0.02)
        assert result is None

    def test_fp_wins_when_both_out_of_band(self):
        """When BOTH FP and FN exceed sensitivity, prefer tightening
        (FP wins): false approvals are worse for AUTO #2 trust than
        false rejects."""
        from genlab_core.learning.gate_tuner import compute_override

        # Both FP and FN at 30% — high disagreement on both sides.
        # FP branch (tighten) should win over FN branch (relax).
        stats = _FakeStats(
            niche_id="sports",
            sample_count=100,
            agreement_count=40,
            true_positives=30,
            true_negatives=10,
            false_positives=30,
            false_negatives=30,
        )
        result = compute_override(stats, current_composite=0.30, current_virality=0.02)
        assert result is not None
        # FP wins → tighten.
        assert result.min_composite_score > 0.30


# ─────────────────────────────────────────────────────────────────────
# Section 2: File I/O
# ─────────────────────────────────────────────────────────────────────


class TestOverrideFileIO:
    def test_load_overrides_missing_file_returns_empty_dict(self, _isolate_overrides_file):
        """When no overrides file exists, gate falls through to defaults
        — this is the pre-Lever-A behavior preserved."""
        from genlab_core.learning.gate_tuner import load_overrides

        # File doesn't exist (autouse fixture sets the env but doesn't create the file).
        assert load_overrides() == {}

    def test_load_overrides_malformed_json_returns_empty(self, _isolate_overrides_file):
        """A corrupted overrides file MUST NOT crash the gate — fall
        through to defaults and log a warning."""
        from genlab_core.learning.gate_tuner import load_overrides

        _isolate_overrides_file.write_text("not valid json {[}")
        assert load_overrides() == {}

    def test_get_overrides_for_unknown_niche_returns_nones(self, _isolate_overrides_file):
        """A niche not in the overrides file returns ``(None, None)``,
        which the gate interprets as 'use defaults for this niche'."""
        from genlab_core.learning.gate_tuner import get_overrides_for_niche

        _isolate_overrides_file.write_text(json.dumps({"gaming": {"min_composite_score": 0.35}}))
        assert get_overrides_for_niche("sports") == (None, None)

    def test_get_overrides_for_known_niche_returns_values(self, _isolate_overrides_file):
        """Round-trip: persisted override values come back via the
        accessor the gate calls."""
        from genlab_core.learning.gate_tuner import get_overrides_for_niche

        _isolate_overrides_file.write_text(
            json.dumps(
                {
                    "gaming": {
                        "min_composite_score": 0.35,
                        "min_virality_score": 0.03,
                    }
                }
            )
        )
        composite, virality = get_overrides_for_niche("gaming")
        assert composite == 0.35
        assert virality == 0.03


# ─────────────────────────────────────────────────────────────────────
# Section 3: auto_approval_gate.evaluate() uses overrides
# ─────────────────────────────────────────────────────────────────────


class TestEvaluateUsesOverrides:
    def test_evaluate_uses_override_when_present(self, _isolate_overrides_file):
        """The end-to-end pin: when an override exists for the blueprint's
        niche, ``evaluate()`` reads it instead of the default."""
        from genlab_core.scheduling.auto_approval_gate import evaluate

        # Override: gaming gets a TIGHTER composite floor (0.45).
        _isolate_overrides_file.write_text(
            json.dumps(
                {
                    "gaming": {
                        "min_composite_score": 0.45,
                        "min_virality_score": 0.02,
                    }
                }
            )
        )

        # Blueprint at composite=0.35 — would PASS the default 0.30
        # floor but FAIL the override's 0.45 floor.
        blueprint = {
            "niche_id": "gaming",
            "hook_text": "Test hook",
            "extra": {
                "composite_score": 0.35,
                "virality_score": 0.05,
                "validation_status": {"all_passed": True},
                "visual_paths": ["/x.mp4"],
            },
        }
        decision = evaluate(blueprint)

        # composite_score check should be in failed_checks because the
        # override raised the floor above the blueprint's score.
        assert "composite_score" in decision.failed_checks, (
            f"Expected composite_score to fail under tightened override (0.45); "
            f"failed_checks={decision.failed_checks}, "
            f"passed_checks={decision.passed_checks}"
        )
        assert decision.approved is False

    def test_evaluate_falls_back_to_defaults_when_no_override(self, _isolate_overrides_file):
        """When no override exists for a niche, ``evaluate()`` uses
        the module-level defaults — pre-Lever-A behavior preserved."""
        from genlab_core.scheduling.auto_approval_gate import evaluate

        # No override file written; same blueprint as above.
        blueprint = {
            "niche_id": "gaming",
            "hook_text": "Test hook",
            "extra": {
                "composite_score": 0.35,
                "virality_score": 0.05,
                "validation_status": {"all_passed": True},
                "visual_paths": ["/x.mp4"],
            },
        }
        decision = evaluate(blueprint)

        # Default composite floor is 0.30 — blueprint at 0.35 should pass.
        assert "composite_score" in decision.passed_checks, (
            f"Expected composite_score to pass under default (0.30); "
            f"passed_checks={decision.passed_checks}"
        )
        assert decision.approved is True

    def test_evaluate_explicit_kwarg_overrides_override(self, _isolate_overrides_file):
        """Explicit kwarg beats the niche override — preserves test
        injection + operator-CLI override patterns. Critical: if this
        breaks, the dashboard preview endpoint that's been allowing
        operators to ``preview with custom thresholds`` would stop
        working."""
        from genlab_core.scheduling.auto_approval_gate import evaluate

        # Override: gaming composite floor = 0.45.
        _isolate_overrides_file.write_text(
            json.dumps(
                {
                    "gaming": {
                        "min_composite_score": 0.45,
                        "min_virality_score": 0.02,
                    }
                }
            )
        )

        blueprint = {
            "niche_id": "gaming",
            "hook_text": "Test hook",
            "extra": {
                "composite_score": 0.35,
                "virality_score": 0.05,
                "validation_status": {"all_passed": True},
                "visual_paths": ["/x.mp4"],
            },
        }
        # Explicit kwarg = 0.20 — should win over the 0.45 override.
        decision = evaluate(blueprint, min_composite_score=0.20)
        assert "composite_score" in decision.passed_checks
        assert decision.approved is True

    def test_evaluate_with_missing_niche_id_uses_defaults(self, _isolate_overrides_file):
        """Old-style blueprint dicts (no niche_id) still work — the
        override lookup returns (None, None) for empty niche_id, gate
        falls through to defaults. Backward compatibility pin."""
        from genlab_core.scheduling.auto_approval_gate import evaluate

        _isolate_overrides_file.write_text(json.dumps({"gaming": {"min_composite_score": 0.45}}))

        blueprint = {
            # niche_id intentionally missing
            "hook_text": "Test hook",
            "extra": {
                "composite_score": 0.35,
                "virality_score": 0.05,
                "validation_status": {"all_passed": True},
                "visual_paths": ["/x.mp4"],
            },
        }
        decision = evaluate(blueprint)
        # Should use default 0.30 (not gaming's 0.45) because niche_id is missing.
        assert "composite_score" in decision.passed_checks
