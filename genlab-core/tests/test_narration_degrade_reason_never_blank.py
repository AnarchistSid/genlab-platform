"""SCHED-01 Step 1 — a degraded narration row must always carry a reason.

CADENCE-01 measured the invariant holding in production: 41 of 41 degraded
ai_creators blueprints carried a specific reason, and zero rows anywhere had
``narration_degraded=True`` with a blank reason. But it held by CONVENTION —
every upstream site (``base_writing:903``, ``generate_audio:130``, the three
literals in ``transformation_orchestrator``) happens to set a non-empty string.
The persist choke point applied no defence of its own, which is the
"shared contract, N implementers" shape that drifts the moment a sixth site
is added.

Why blank is unacceptable rather than merely untidy: at query time an empty
reason is indistinguishable from a healthy row. Register #245 was filed —
and then retracted — on exactly that misreading, so this pin protects the
next reader as much as the next writer.
"""
import pytest

from genlab_core.pipeline.stages.push_to_backlog import _degrade_reason


def test_specific_reason_is_preserved_verbatim():
    """The whole point of the reason is that it distinguishes repairs."""
    for reason in ("script_too_long", "script_generation_failed",
                   "vo_overrun", "mix_failed", "storytime_mutex"):
        content = {"narration_degraded": True, "narration_degraded_reason": reason}
        assert _degrade_reason(content) == reason


def test_degraded_with_missing_reason_is_never_blank():
    """The invariant. A future site that forgets must not produce a blank."""
    assert _degrade_reason({"narration_degraded": True}) == "reason_unavailable"


def test_degraded_with_empty_reason_is_never_blank():
    assert _degrade_reason(
        {"narration_degraded": True, "narration_degraded_reason": ""}
    ) == "reason_unavailable"


def test_degraded_with_whitespace_reason_is_never_blank():
    """Whitespace is blank. `.strip()` is load-bearing, not cosmetic."""
    assert _degrade_reason(
        {"narration_degraded": True, "narration_degraded_reason": "   "}
    ) == "reason_unavailable"


def test_degraded_with_none_reason_is_never_blank():
    assert _degrade_reason(
        {"narration_degraded": True, "narration_degraded_reason": None}
    ) == "reason_unavailable"


def test_not_degraded_stays_blank():
    """The 154 non-BB rows CADENCE-01 measured. Blank here is CORRECT --
    it means no degradation happened. Stamping 'reason_unavailable' on a
    healthy row would manufacture the alarm #245 wrongly raised."""
    assert _degrade_reason({"narration_degraded": False}) == ""
    assert _degrade_reason({}) == ""


def test_not_degraded_but_reason_present_keeps_the_reason():
    """Defensive: if some site recorded a reason without the flag, keep it
    rather than discarding evidence."""
    assert _degrade_reason(
        {"narration_degraded": False, "narration_degraded_reason": "vo_overrun"}
    ) == "vo_overrun"


@pytest.mark.parametrize("flag", [True, 1, "true"])
def test_truthy_flag_variants_all_trigger_the_guard(flag):
    """bool() coercion must not let a truthy non-bool slip through blank."""
    assert _degrade_reason({"narration_degraded": flag}) == "reason_unavailable"
