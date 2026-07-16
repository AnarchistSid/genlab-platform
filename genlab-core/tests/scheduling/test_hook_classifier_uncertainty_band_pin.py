"""Pin the 2026-07-17 hook_classifier uncertainty-band fix in auto_approval_gate.

## What broke pre-fix

Empirical prod distribution (session-2026-07-17 audit round 3):

    ai_creators: n=6,  avg=0.167, max=0.316 (0/6  ≥ 0.5)
    anime:       n=14, avg=0.261, max=0.653 (2/14 ≥ 0.5)
    gaming:      n=23, avg=0.293, max=0.705 (4/23 ≥ 0.5)
    movies:      n=21, avg=0.227, max=0.502 (1/21 ≥ 0.5)
    sports:      n=16, avg=0.251, max=0.531 (1/16 ≥ 0.5)

The XGBoost hook_classifier was trained at MIN_EXAMPLES=50 — raw
probas cluster in [0.05, 0.35]. Prior gate logic treated a 0.17
score as "17% confidence contribution", dragging the mean below
the 0.80 threshold. That was the structural block on the
auto-approver Week 1→2 ramp (~92% calibration agreement but
0 approvals for 20+ days).

## Fix contract (this test locks it)

- score >= 0.5: passed_checks + contribution = raw score
- 0.4 <= score < 0.5: borderline soft signal, contribution = raw score
- score < 0.4: under-trained-model noise, NO contribution (same as
  None cold-start path)

Above the noise floor the model is treated as informative;
below, it's treated as uninformative. Long-term fix is
model recalibration; this gate change unblocks the ramp meanwhile.
"""

from __future__ import annotations

from genlab_core.scheduling.auto_approval_gate import evaluate


def _base_bp(hook_clf: float | None) -> dict:
    """Build a passing blueprint with tunable hook_classifier_score."""
    extra = {
        "visual_paths": ["/x.mp4"],
        "validation_status": {"all_passed": True},
        "composite_score": 0.55,
        "virality_score": 0.18,
    }
    if hook_clf is not None:
        extra["hook_classifier_score"] = hook_clf
    return {
        "hook_text": "A realistic hook of moderate length here",
        "visual_paths": ["/x.mp4"],
        "extra": extra,
    }


def test_below_floor_does_not_contribute_to_confidence() -> None:
    """0.17 score → skipped, confidence math same as None case.

    Regression scenario: someone reverts the fix so raw 0.17
    contributes directly → drags mean below 0.80 → auto-approver
    stuck at 0 approvals again.
    """
    with_low = evaluate(_base_bp(0.17))
    with_none = evaluate(_base_bp(None))
    assert abs(with_low.confidence - with_none.confidence) < 1e-6, (
        f"score=0.17 should have SAME confidence as None (both = no signal), "
        f"got low={with_low.confidence:.3f} vs none={with_none.confidence:.3f}"
    )


def test_borderline_band_contributes_raw_score() -> None:
    """0.4-0.5 score → borderline, contribution = raw score.

    This band gives the model SOME voice without letting weak
    signals dominate. Locks the [0.4, 0.5) borderline behavior.
    """
    with_borderline = evaluate(_base_bp(0.45))
    with_none = evaluate(_base_bp(None))
    assert with_borderline.confidence < with_none.confidence, (
        f"score=0.45 should DRAG confidence below the None baseline "
        f"(borderline soft signal), got borderline={with_borderline.confidence:.3f} "
        f"vs none={with_none.confidence:.3f}"
    )


def test_strong_score_lifts_confidence() -> None:
    """0.65 score → passed_checks + contribution = 0.65."""
    with_strong = evaluate(_base_bp(0.65))
    assert "hook_classifier_score" in with_strong.passed_checks, (
        "score ≥ 0.5 must be recorded in passed_checks"
    )


def test_below_floor_reason_documents_uncertainty() -> None:
    """The gate must surface a reason string that mentions 'under-trained'
    or 'uncertainty' so operators diagnosing 'why did the gate reject'
    can trace to this design decision without spelunking git blame."""
    r = evaluate(_base_bp(0.20))
    matched = any(
        ("under-trained" in reason or "uncertainty" in reason)
        for reason in r.reasons
    )
    assert matched, (
        "Gate reasons must document that below-floor scores are "
        "treated as no-signal, so future audits can trace the "
        f"structural decision. Reasons: {r.reasons}"
    )


def test_realistic_ai_creators_blueprint_now_approves() -> None:
    """Empirical prod ai_creators shape: composite ~0.55, virality
    ~0.18, hook_classifier ~0.17. Pre-fix: confidence ~0.60
    (rejected under 0.80). Post-fix: confidence ~0.73 (approved).

    This test is the empirical anchor — if the confidence math
    changes, this pin catches drift back into 'auto-approver
    silently blocked' territory.
    """
    r = evaluate(_base_bp(0.17))
    assert r.approved is True, "gate must approve — no hard rejects"
    assert r.confidence >= 0.70, (
        f"realistic ai_creators shape must produce confidence ≥ 0.70 "
        f"post-fix (was ~0.60 pre-fix, blocking auto-approver ramp). "
        f"Got {r.confidence:.3f}"
    )
