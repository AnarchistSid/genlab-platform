"""2026-06-14 ε-greedy force-explore for under-explored style:* arms.

Bandit data audit (2026-06-14) found 2 of 5 niches (sports, movies)
with 0/5 converged style arms — the exploitation policy never picked
an unobserved arm when the default dominant arm had 100+ obs of
mediocre reward (~0.07-0.10). The bandit was structurally blind.

Fix: ε-greedy force-explore. With probability EPSILON, when the niche
has fewer than CONVERGED_STYLE_THRESHOLD converged style arms, pick a
random unobserved style:* arm instead of the keyword-matched default.
Seeds the bandit with exploration signal in 10-20 publishing cycles
even for the worst-off niches.

These pins assert the gates fire correctly and the legacy keyword-
matching path is preserved when force-explore doesn't trigger.
"""

from __future__ import annotations

from genlab_core.pipeline.stages.push_to_backlog import (
    _classify_arm,
    _maybe_force_explore_style_arm,
)

# ────────────────────────────────────────────────────────────────────────────
# _maybe_force_explore_style_arm — the gating helper
# ────────────────────────────────────────────────────────────────────────────


def test_force_explore_returns_unobserved_arm_when_niche_starved():
    """Sports has all 5 style arms unobserved + RNG triggers (rand=0
    < ε=0.15). Must return one of the unobserved arms."""
    arm_n_obs = {
        "highlight_play": 127,  # default arm — many obs, not style:*
        "style:sports:question": 0,
        "style:sports:comparison": 0,
        "style:sports:bold_claim": 0,
        "style:sports:controversy": 0,
        "style:sports:revelation": 0,
    }
    # rand() = 0.0 → always < ε. Forces the explore branch.
    picked = _maybe_force_explore_style_arm("sports", arm_n_obs, _rng=lambda: 0.0)
    assert picked is not None
    assert picked.startswith("style:sports:"), f"got {picked}"


def test_force_explore_returns_none_when_niche_has_enough_converged():
    """Gaming has 3 converged style arms (n_obs >= 5). Force-explore
    must short-circuit even when rand triggers — gaming already has
    per-style signal, more exploration is wasted."""
    arm_n_obs = {
        "gameplay_clip": 108,
        "style:gaming:question": 11,  # converged
        "style:gaming:controversy": 6,  # converged
        "style:gaming:revelation": 5,  # converged (threshold = 5)
        "style:gaming:bold_claim": 0,
        "style:gaming:comparison": 0,
    }
    picked = _maybe_force_explore_style_arm("gaming", arm_n_obs, _rng=lambda: 0.0)
    assert picked is None, f"converged niche must not force-explore; got {picked}"


def test_force_explore_returns_none_when_random_above_epsilon():
    """ε-greedy: when rand() >= ε, no force-explore even if the niche
    is starved. Pins that the probability gate actually fires."""
    arm_n_obs = {
        "highlight_play": 127,
        "style:sports:question": 0,
        "style:sports:comparison": 0,
        "style:sports:bold_claim": 0,
    }
    # rand() = 0.99 → well above ε=0.15 → no force-explore
    picked = _maybe_force_explore_style_arm("sports", arm_n_obs, _rng=lambda: 0.99)
    assert picked is None


def test_force_explore_returns_none_when_no_unexplored_arms_remain():
    """If every style arm has at least one observation, there's nothing
    to force-explore TOWARD. Must fall through to the normal classifier."""
    arm_n_obs = {
        "trailer_drop": 40,
        # All style arms have obs (just 1-2 each — not converged but not 0).
        "style:movies:question": 1,
        "style:movies:comparison": 2,
        "style:movies:bold_claim": 1,
        "style:movies:controversy": 1,
        "style:movies:revelation": 1,
    }
    picked = _maybe_force_explore_style_arm("movies", arm_n_obs, _rng=lambda: 0.0)
    assert picked is None


def test_force_explore_returns_none_for_unknown_niche():
    """A niche_id with no style:* arms at all (test niches, future
    niches before bandit init) must short-circuit cleanly."""
    arm_n_obs = {"random_arm": 5, "another_arm": 10}
    picked = _maybe_force_explore_style_arm("unknown_niche", arm_n_obs, _rng=lambda: 0.0)
    assert picked is None


# ────────────────────────────────────────────────────────────────────────────
# _classify_arm integration — force-explore takes precedence over keywords
# ────────────────────────────────────────────────────────────────────────────


def test_classify_arm_force_explore_overrides_keyword_match_when_triggered():
    """End-to-end: a sports story whose hook keyword-matches
    ``breaking_trade`` (signed → "sign" keyword) must STILL get
    force-explored to a style arm when the ε-greedy gate triggers.
    This is the whole point — the keyword path is what locks the
    bandit into the default. Force-explore must escape it."""
    story = {"title": "Trade signed", "summary": "Player signed contract"}
    content = {"hook": "Big trade just got signed"}
    arm_n_obs = {
        "highlight_play": 127,
        "style:sports:question": 0,
        "style:sports:bold_claim": 0,
        "style:sports:comparison": 0,
    }
    arm_id = _classify_arm(
        "sports",
        story,
        content,
        arm_boosts={"breaking_trade": 1.2},
        arm_n_obs=arm_n_obs,
        _rng=lambda: 0.0,  # force-explore triggers
    )
    assert arm_id.startswith("style:sports:"), (
        f"expected force-explored style arm, got {arm_id} — keyword path leaked through"
    )


def test_classify_arm_legacy_behavior_preserved_when_arm_n_obs_none():
    """Backward compat: callers that don't pass arm_n_obs (tests,
    older code paths) must see exactly the pre-fix behavior — keyword
    matching with bandit-boost tie-breaking."""
    story = {"title": "Patch notes", "summary": "Season 2 launch"}
    content = {"hook": "Big patch dropping"}
    # Two matches (patch_news for 'patch', trailer_reaction for 'launch')
    # — bandit boost should pick the higher-boosted one.
    arm_id = _classify_arm(
        "gaming",
        story,
        content,
        arm_boosts={"patch_news": 1.5, "trailer_reaction": 0.8},
        # arm_n_obs intentionally omitted — legacy path
    )
    assert arm_id == "patch_news"


def test_classify_arm_no_keyword_matches_returns_default_arm():
    """Sanity pin: with no keyword matches AND force-explore not
    triggered (high rand), return the niche default arm."""
    story = {"title": "Random article", "summary": "Generic text"}
    content = {"hook": "Generic hook"}
    arm_id = _classify_arm(
        "movies",
        story,
        content,
        arm_boosts={},
        arm_n_obs={"trailer_drop": 40},
        _rng=lambda: 0.99,  # above ε, no force-explore
    )
    assert arm_id == "trailer_drop"  # the default for movies
