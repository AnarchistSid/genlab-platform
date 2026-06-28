"""Pin: ``_classify_arm_with_propensity`` — propensity-aware variant
of ``_classify_arm`` (PR U, 2026-06-28).

PR #634 shipped per-decision propensity logging at the
``LinUCBBandit.select_with_propensity`` level + the
``pending_feedback.propensity`` storage column. PR U closes the wire:
the new ``_classify_arm_with_propensity`` returns ``(arm_id,
propensity)`` so the production path in push_to_backlog can persist
the propensity into the blueprint ``fields`` dict → into
``register_pending_feedback`` → into the
``pending_feedback.propensity`` column.

The backward-compat wrapper ``_classify_arm`` drops the propensity so
the 20+ existing test callers don't need updating.

These pins lock in:

  Backward-compat wrapper (1):
  1. test_classify_arm_backward_compat_returns_str — ``_classify_arm``
     still returns ``str``; signature unchanged.

  LinUCB path (1):
  2. test_new_variant_returns_arm_and_propensity_for_linucb_path —
     when LinUCB path runs (env on + matches + context + linucb_arms
     warm), returns ``(arm, float)``.

  Thompson fallback (1):
  3. test_new_variant_returns_arm_and_none_for_thompson_path —
     LinUCB cold-start → Thompson → ``(arm, None)``.

  Single-candidate (1):
  4. test_new_variant_single_candidate_returns_arm_and_1 — ``[X]``
     match → ``(X, 1.0)``.

  Random-control override (1):
  5. test_new_variant_random_control_returns_arm_and_none — random
     control fires → propensity drops to None.
"""

from __future__ import annotations

import numpy as np
from genlab_core.learning.linucb import CONTEXT_DIM, LinUCBArm
from genlab_core.pipeline.stages.push_to_backlog import (
    _classify_arm,
    _classify_arm_with_propensity,
)


def _trained_arm(*, n_obs: int = 20, bias_dim: int = 0, reward: float = 1.0) -> LinUCBArm:
    arm = LinUCBArm(d=CONTEXT_DIM)
    ctx = np.zeros(CONTEXT_DIM, dtype=np.float64)
    ctx[bias_dim] = 1.0
    for _ in range(n_obs):
        arm.update(ctx, reward)
    return arm


def _ctx(bias_dim: int = 0) -> np.ndarray:
    ctx = np.zeros(CONTEXT_DIM, dtype=np.float64)
    ctx[bias_dim] = 1.0
    return ctx


# Real keywords from _ARM_KEYWORDS — "patch" → patch_news, "launch" →
# trailer_reaction (these match the I2 wire-up tests' fixtures).
_MULTI_MATCH_STORY = {"title": "Patch notes", "summary": "Season 2 launch"}
_MULTI_MATCH_CONTENT = {"hook": "Big patch dropping"}


class _AlwaysRandomRng:
    """RNG that always returns 0.0 so epsilon-greedy ALWAYS hits the
    random-control branch. Mirrors the helper in
    ``test_classify_arm_i1_wire.py``."""

    def random(self) -> float:
        return 0.0

    def choice(self, seq):
        return seq[1] if len(seq) > 1 else seq[0]


def test_classify_arm_backward_compat_returns_str(monkeypatch):
    """Pin: the public ``_classify_arm`` signature is unchanged — it
    returns ``str``, not a tuple. The 20+ existing test callers in
    ``test_classify_arm_*.py`` and the inline pre-PR-U callers in
    push_to_backlog can keep unpacking a single string.

    This is CRITICAL — without this guarantee, every existing wire
    test would break on tuple-vs-str shape mismatch."""
    monkeypatch.delenv("GENLAB_LINUCB_PICK_ENABLED", raising=False)
    monkeypatch.delenv("GENLAB_EXPERIMENTATION_ENABLED", raising=False)

    result = _classify_arm(
        "gaming",
        _MULTI_MATCH_STORY,
        _MULTI_MATCH_CONTENT,
        arm_boosts={"patch_news": 1.5, "trailer_reaction": 0.8},
    )
    # Must be a string, NOT a tuple — protects every existing caller.
    assert isinstance(result, str)
    assert result == "patch_news"


def test_new_variant_returns_arm_and_propensity_for_linucb_path(monkeypatch):
    """Pin: when LinUCB path actually runs (env on + linucb_arms + warm
    enough + context), returns ``(arm, float)``. The float is the
    softmax weight from ``pick_best_arm_with_propensity``."""
    monkeypatch.setenv("GENLAB_LINUCB_PICK_ENABLED", "1")
    monkeypatch.delenv("GENLAB_EXPERIMENTATION_ENABLED", raising=False)

    # Both arms warm; LinUCB favors trailer_reaction via the bias_dim=0
    # query context (mirror of test_linucb_wins_over_thompson_when_enabled).
    linucb_arms = {
        "patch_news": _trained_arm(n_obs=20, bias_dim=1),
        "trailer_reaction": _trained_arm(n_obs=20, bias_dim=0),
    }
    arm_id, propensity = _classify_arm_with_propensity(
        "gaming",
        _MULTI_MATCH_STORY,
        _MULTI_MATCH_CONTENT,
        arm_boosts={"patch_news": 1.5, "trailer_reaction": 0.8},
        linucb_arms=linucb_arms,
        context=_ctx(bias_dim=0),
    )
    assert arm_id == "trailer_reaction"
    assert propensity is not None
    assert isinstance(propensity, float)
    # Propensity is a softmax weight: strictly in (0, 1] given the
    # floor + sum-to-1 invariant.
    assert 0.0 < propensity <= 1.0


def test_new_variant_returns_arm_and_none_for_thompson_path(monkeypatch):
    """Pin: when LinUCB cold-starts (any matched arm under-observed),
    falls back to Thompson. Thompson has no IPS-compatible propensity
    concept, so the second tuple element is ``None``."""
    monkeypatch.setenv("GENLAB_LINUCB_PICK_ENABLED", "1")
    monkeypatch.delenv("GENLAB_EXPERIMENTATION_ENABLED", raising=False)

    # One arm under-observed (n_obs=3 < default min_obs=10) → cold-start
    # collapses the picker. Thompson takes over → patch_news (higher boost).
    linucb_arms = {
        "patch_news": _trained_arm(n_obs=3, bias_dim=0),
        "trailer_reaction": _trained_arm(n_obs=20, bias_dim=0),
    }
    arm_id, propensity = _classify_arm_with_propensity(
        "gaming",
        _MULTI_MATCH_STORY,
        _MULTI_MATCH_CONTENT,
        arm_boosts={"patch_news": 1.5, "trailer_reaction": 0.8},
        linucb_arms=linucb_arms,
        context=_ctx(bias_dim=0),
    )
    # Thompson wins the tie-break (patch_news has higher boost).
    assert arm_id == "patch_news"
    # Propensity is None because Thompson doesn't produce a softmax
    # weight — explicit "not applicable" sentinel for IPS replay.
    assert propensity is None


def test_new_variant_single_candidate_returns_arm_and_1(monkeypatch):
    """Pin: single-candidate short-circuit returns ``(arm, 1.0)`` — the
    selection is trivially deterministic regardless of which downstream
    policy would have fired. Matches
    ``pick_best_arm_with_propensity``'s convention."""
    monkeypatch.delenv("GENLAB_LINUCB_PICK_ENABLED", raising=False)
    monkeypatch.delenv("GENLAB_EXPERIMENTATION_ENABLED", raising=False)

    # Story matches only one gaming arm — "esports" hits tournament_recap.
    story = {"title": "ESL Cologne grand finals", "summary": "Tournament wrap-up"}
    content = {"hook": "ESL one wrap"}

    # Sanity: confirm single match (mirrors I2 wire test guard).
    from genlab_core.pipeline.stages.push_to_backlog import _ARM_KEYWORDS

    text = f"{story['title']} {content['hook']} {story['summary']}".lower()
    matches = [aid for aid, kws in _ARM_KEYWORDS.get("gaming", []) if any(kw in text for kw in kws)]
    assert len(matches) == 1, f"Test setup invalid — expected 1 match, got {matches}"

    arm_id, propensity = _classify_arm_with_propensity("gaming", story, content)
    assert arm_id == matches[0]
    assert propensity == 1.0


def test_new_variant_random_control_returns_arm_and_none(monkeypatch):
    """Pin: when the I1 random-control wrap fires, propensity drops to
    None — the override's selection probability is independent of the
    LinUCB softmax weight that the picker would have published. Mixing
    them would corrupt IPS replay (two convention spaces collapsed
    into one column)."""
    monkeypatch.setenv("GENLAB_EXPERIMENTATION_ENABLED", "1")
    monkeypatch.delenv("GENLAB_LINUCB_PICK_ENABLED", raising=False)  # Thompson path

    arm_id, propensity = _classify_arm_with_propensity(
        "gaming",
        _MULTI_MATCH_STORY,
        _MULTI_MATCH_CONTENT,
        arm_boosts={"patch_news": 1.5, "trailer_reaction": 0.8},
        _random_control_rng=_AlwaysRandomRng(),
    )
    # AlwaysRandomRng forces the random-control branch. The override
    # picks something different from bandit pick (specifically seq[1]).
    assert isinstance(arm_id, str)
    # Propensity is None because random control overrode any LinUCB
    # propensity that might have been computed upstream.
    assert propensity is None
