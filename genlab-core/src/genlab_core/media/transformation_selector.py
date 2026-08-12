"""Per-dimension bandit selector for the intelligent transformation stack.

At render time this module picks one arm per transformation dimension
for a given niche. Each dimension has independent Thompson-sampling
posteriors over the arms declared in that niche's ``intelligent_transform``
config. The 11-dimensional factorization avoids the combinatorial
explosion (5^11 = 48M) of picking a single reel-level arm.

Selection algorithm per dimension:

  1. Load all arms from ``bandit_arms`` scoped to this niche.
  2. Filter to arms with prefix ``transform__<dim>__``.
  3. Cross-check against the niche's current config — only arms whose
     ``dimension_value`` is declared in ``visuals.yaml`` are candidates.
     This handles the "operator removed an arm from config but DB still
     has it" case gracefully.
  4. ε-greedy explore: with probability 0.15, pick uniformly random.
     Lower than the content-bandit's 25% because transformation
     exploration compounds — a single reel exploring on 11 dimensions
     at 25% each would rarely land on any-exploit combination.
  5. Otherwise: Thompson sampling. Draw one sample per candidate from
     Beta(α, β), pick argmax.
  6. Return arm_id + dimension_value + propensity.

The propensity is used later for IPS-style counterfactual reward
estimation (Intervention 7). It's an approximation — true Thompson
propensity requires expensive integration over Beta distributions;
we use ``(1-ε)/n + ε/n`` as a lower bound sufficient for the ratio
math IPS actually needs.

Reward attribution routes back per-dimension via the ``pending_
feedback.arm_ids_by_dimension`` JSONB (migration a7v8w9x0y1z2).

Config gating: this module respects TWO independent flags. Both must
be true for any selection to happen:

  * ``config.enabled`` (per-niche, from visuals.yaml)
  * ``GENLAB_INTELLIGENT_TRANSFORM_ENABLED`` (global env kill-switch)

Either can rollback without touching the other.

Related sprint context:
- [[intelligent-transformation-architecture-2026-07-05]] — full design
- PR 1 (#665) — schema migration providing arm_type + dimension columns
- PR 2 (#666) — config schema
- PR 3 (#667) — arm registration script
- PR 5 (upcoming) — RewardShaper multi-arm attribution consumer
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field

from genlab_core.media.intelligent_transform import (
    IntelligentTransformConfig,
)

logger = logging.getLogger(__name__)

_ARM_ID_PREFIX = "transform__"
_EPSILON = 0.15
"""ε-greedy exploration rate. Lower than content bandit's 25%
because transformation compounds — 11 dims at 25% ε means p(pure
exploit) = (0.75)^11 ≈ 4%, essentially always mixing exploration
somewhere. At 15% ε, p(pure exploit) ≈ 17% — still frequent
exploration but with more "learned choice" reels for engagement
signal accumulation."""

# 2026-07-23: exploration-debt–aware ε boost.
# Anime + ai_creators had 50/51 and 41/52 untouched transformation arms
# respectively (98% and 79% debt) after ~3 months of publishing. At the
# static 15% ε rate the bandit sees each dimension's untouched arms
# ~15% / n_arms of the time — even at 100 publishes, a dimension with
# 5 arms only lands on any particular unplayed arm ~3 times. Some arms
# never get sampled for months, meaning the bandit can never form an
# opinion on them and picks them ~never (the Thompson posterior stays
# at Beta(1,1) uniform prior forever).
#
# Boost ε when the current dimension has high fraction of unplayed arms.
# Higher ε ≈ higher chance of picking uniformly-random rather than
# argmax — which is the whole point when arms are unplayed and there's
# nothing to argmax on. Propensity math is preserved because the
# BOOSTED ε is what the chooser saw, so IPS reads the same value.
_EPSILON_DEBT_HEAVY = 0.35  # >70% arms in this dimension untouched
_EPSILON_DEBT_MODERATE = 0.25  # 40-70% untouched


def _epsilon_for_candidates(candidates: list[tuple[str, str, float, float]]) -> float:
    """Compute ε per-dimension based on exploration debt in the candidate
    pool. Untouched = alpha == beta == 1.0 (Beta(1,1) uniform prior).

    Returns _EPSILON (0.15) for well-explored dimensions; higher values
    for dimensions with substantial unplayed arms so the bandit can
    finally form posteriors on them.
    """
    if not candidates:
        return _EPSILON
    # Untouched = still at the Beta(1,1) uniform prior (α == 1 AND β == 1).
    # Beta(1.5, 0.5) also sums to 2 but represents partial information
    # (posterior mean 0.75) — don't count it as untouched.
    untouched = sum(1 for _arm, _val, a, b in candidates if a == 1.0 and b == 1.0)
    debt_ratio = untouched / len(candidates)
    if debt_ratio > 0.7:
        return _EPSILON_DEBT_HEAVY
    if debt_ratio > 0.4:
        return _EPSILON_DEBT_MODERATE
    return _EPSILON

_DIMENSION_ARM_LIST_FIELDS = {
    "music_mood": "moods",
    "caption_style": "styles",
    "hook_framing": "framings",
    "intro_animation": "templates",
    "outro_cta": "styles",
    "pan_zoom": "patterns",
    "highlight_moment": "methods",
    "audio_ducking": "levels_db",
    "music_visual_sync": "modes",
    "caption_emphasis_color": "colors",
    "caption_pacing": "pacing_ms_options",
}
"""Maps dimension name → the Pydantic field holding the arm-value list.
Symmetric with ``scripts/register_transformation_arms.enumerate_arms_for_niche``."""


@dataclass
class TransformationChoice:
    """One dimension's selection result."""

    dimension: str
    dimension_value: str
    arm_id: str
    propensity: float
    """P(arm_id | context) under the selection policy. Used by IPS
    counterfactual replay (Intervention 7) for unbiased reward
    estimation. Approximate — see module docstring."""


@dataclass
class TransformationChoices:
    """Full result of selecting one arm per enabled dimension.

    Empty ``choices`` dict is a valid state — means either the config
    was off, the env flag was off, or no arms were registered for this
    niche yet. The render pipeline handles empty choices by falling
    back to legacy (untransformed) render path.
    """

    niche_id: str
    choices: dict[str, TransformationChoice] = field(default_factory=dict)

    def to_arm_ids_by_dimension(self) -> dict[str, str]:
        """Payload for ``pending_feedback.arm_ids_by_dimension``.

        Returns ``{dimension_name: arm_id, ...}`` — the RewardShaper
        iterates this to attribute reward per dimension at 6h/24h/
        48h/168h collection windows.
        """
        return {dim: c.arm_id for dim, c in self.choices.items()}

    def is_empty(self) -> bool:
        return not self.choices


def _flag_enabled() -> bool:
    """Check the env kill-switch flag via the shared ``env_true`` helper
    so all 3 read sites for ``GENLAB_INTELLIGENT_TRANSFORM_ENABLED``
    agree on truthiness (round-3 flag audit, 2026-07-08)."""
    from genlab_core.settings import env_true

    return env_true("GENLAB_INTELLIGENT_TRANSFORM_ENABLED")


def _get_declared_values(dim_config, list_field: str) -> set[str]:
    """Extract the config's declared values for a dimension.

    Handles int values (audio_ducking dB, caption_pacing ms) by
    string-casting — matches the arm_id encoding.
    """
    values = getattr(dim_config, list_field, None)
    if not values:
        return set()
    return {str(v) for v in values}


def _parse_arm_id(arm_id: str) -> tuple[str, str] | None:
    """Parse ``transform__<dim>__<value>`` → ``(dim, value)``.

    Returns None if the arm_id doesn't match the expected format.
    """
    if not arm_id.startswith(_ARM_ID_PREFIX):
        return None
    rest = arm_id[len(_ARM_ID_PREFIX) :]
    parts = rest.split("__", 1)
    if len(parts) != 2:
        return None
    return parts[0], parts[1]


def _pick_arm(
    dimension: str,
    candidates: list[tuple[str, str, float, float]],
    *,
    rng: random.Random | None = None,
) -> TransformationChoice | None:
    """Pick one arm from candidates using ε-greedy Thompson sampling.

    Args:
        dimension: dimension name (for logging + result assembly).
        candidates: list of ``(arm_id, value, alpha, beta)`` tuples.
        rng: optional random.Random for deterministic tests. Uses the
            module ``random`` global when None.

    Returns None only if candidates list is empty.
    """
    if not candidates:
        return None

    r = rng or random
    n = len(candidates)
    # 2026-07-23: per-dimension exploration-debt-aware ε. When a dimension
    # has high fraction of unplayed arms, boost ε to accelerate coverage.
    # IPS-safe because the BOOSTED ε is the one the chooser used.
    epsilon = _epsilon_for_candidates(candidates)

    if r.random() < epsilon:
        # Uniform exploration
        arm_id, value, _, _ = r.choice(candidates)
        return TransformationChoice(
            dimension=dimension,
            dimension_value=value,
            arm_id=arm_id,
            propensity=epsilon / n,
        )

    # Thompson sampling — one Beta(α, β) draw per arm, pick argmax
    samples = [
        (r.betavariate(alpha, beta), arm_id, value) for arm_id, value, alpha, beta in candidates
    ]
    samples.sort(key=lambda t: t[0], reverse=True)
    _, chosen_arm_id, chosen_value = samples[0]

    # Propensity approximation — see module docstring.
    # True Thompson propensity requires integrating over Beta pdfs
    # which is O(n²). This lower bound is sufficient for IPS.
    propensity = (1.0 - epsilon) / n + epsilon / n
    return TransformationChoice(
        dimension=dimension,
        dimension_value=chosen_value,
        arm_id=chosen_arm_id,
        propensity=propensity,
    )


def select_transformation_dimensions(
    niche_id: str,
    config: IntelligentTransformConfig,
    *,
    proxy=None,
    rng: random.Random | None = None,
    blueprint_context: dict | None = None,
) -> TransformationChoices:
    """Pick one arm per enabled dimension for a niche.

    Args:
        niche_id: 'ai_creators', 'gaming', 'sports', 'movies', 'anime'.
        config: parsed ``IntelligentTransformConfig`` from the niche's
            ``visuals.yaml``.
        proxy: optional bandit_arms proxy. When None, constructs a
            fresh BacklogClient. Tests pass a mock proxy directly.
        rng: optional random.Random for deterministic tests.
        blueprint_context: optional dict carrying blueprint metadata
            (hook, title, summary) — used ONLY for observability
            steer-logging on the music_mood dimension. Fed to
            music_mood_llm_fit.suggest_mood() when the flag is on,
            which returns a suggestion the operator can compare
            against the bandit pick over the following weeks.
            Does NOT alter selection outcome in this commit.

    Returns TransformationChoices — may be empty if:
        - ``config.enabled`` is False
        - Env flag ``GENLAB_INTELLIGENT_TRANSFORM_ENABLED`` is not set
        - BacklogClient construction fails
        - No transformation arms registered for the niche
        - All candidate arms filtered out by config-drift check

    Partial choices are valid — the render pipeline skips dimensions
    that lack a selection.
    """
    result = TransformationChoices(niche_id=niche_id)

    # Gate 1: config
    if not config.enabled:
        logger.debug("[transform_selector] config disabled for %s", niche_id)
        return result

    # Gate 2: env kill-switch
    if not _flag_enabled():
        logger.debug("[transform_selector] GENLAB_INTELLIGENT_TRANSFORM_ENABLED off")
        return result

    # Load arms
    if proxy is None:
        try:
            from genlab_core.http.backlog_client import BacklogClient

            client = BacklogClient()
            proxy = getattr(client, "bandit_arms", None)
        except Exception as exc:
            logger.warning(
                "[transform_selector] BacklogClient construction failed for %s: %s",
                niche_id,
                exc,
            )
            return result

    if proxy is None:
        logger.warning("[transform_selector] no bandit_arms proxy available for %s", niche_id)
        return result

    try:
        from genlab_core.learning.arm_loader import load_all_arms

        all_arms = load_all_arms(proxy, niche_id)
    except Exception as exc:
        logger.warning("[transform_selector] arm load failed for %s: %s", niche_id, exc)
        return result

    # Filter to transformation arms + group by dimension
    arms_by_dim: dict[str, list[tuple[str, str, float, float]]] = {}
    for arm_id, (alpha, beta) in all_arms.items():
        parsed = _parse_arm_id(arm_id)
        if parsed is None:
            continue  # Not a transformation arm
        dim, value = parsed
        arms_by_dim.setdefault(dim, []).append((arm_id, value, alpha, beta))

    if not arms_by_dim:
        logger.info(
            "[transform_selector] no transformation arms in bandit_arms for %s. "
            "Run scripts/register_transformation_arms.py to bootstrap.",
            niche_id,
        )
        return result

    # For each enabled dimension, filter to config-declared arms + pick
    for dim_name, list_field in _DIMENSION_ARM_LIST_FIELDS.items():
        dim_config = getattr(config.dimensions, dim_name, None)
        if dim_config is None or not dim_config.enabled:
            continue

        declared_values = _get_declared_values(dim_config, list_field)
        if not declared_values:
            logger.debug(
                "[transform_selector] %s.%s: no declared values in config",
                niche_id,
                dim_name,
            )
            continue

        db_candidates = arms_by_dim.get(dim_name, [])
        if not db_candidates:
            logger.debug(
                "[transform_selector] %s.%s: no arms in bandit_arms table",
                niche_id,
                dim_name,
            )
            continue

        # Config-drift filter: only keep DB arms whose value is currently
        # declared in visuals.yaml.
        filtered_candidates = [
            (arm_id, value, alpha, beta)
            for arm_id, value, alpha, beta in db_candidates
            if value in declared_values
        ]
        if not filtered_candidates:
            logger.info(
                "[transform_selector] %s.%s: all DB arms filtered out by "
                "config-drift check. DB has %d arms, config declared %d "
                "values — no overlap.",
                niche_id,
                dim_name,
                len(db_candidates),
                len(declared_values),
            )
            continue

        choice = _pick_arm(dim_name, filtered_candidates, rng=rng)
        if choice is not None:
            result.choices[dim_name] = choice

    logger.info(
        "[transform_selector] %s: selected %d/%d dimensions",
        niche_id,
        len(result.choices),
        len(_DIMENSION_ARM_LIST_FIELDS),
    )

    # Observability-only steer for music_mood. Runs AFTER bandit pick,
    # never overrides. Follow-up commit (after operator sees ~1-2wk of
    # this log data) will decide whether to promote to override / veto /
    # prior-boost. See [[session-2026-08-12-fourteen-commit-arc]] for the
    # ship-observability-BEFORE-consumer discipline that produced this
    # split. Fail-open at every layer: missing context, disabled flag,
    # missing API key, LLM refusal — all silently no-op.
    _log_music_mood_llm_steer(niche_id, config, result, blueprint_context)

    return result


def _log_music_mood_llm_steer(
    niche_id: str,
    config: IntelligentTransformConfig,
    result: TransformationChoices,
    blueprint_context: dict | None,
) -> None:
    """Emit a comparison log if the LLM steer has an opinion on this
    blueprint's music mood. Zero effect on selection.
    """
    if blueprint_context is None:
        return
    music_choice = result.choices.get("music_mood")
    if music_choice is None:
        return
    dim_config = getattr(config.dimensions, "music_mood", None)
    if dim_config is None:
        return
    available_moods = _get_declared_values(dim_config, "moods")
    if not available_moods:
        return
    try:
        from genlab_core.media.music_mood_llm_fit import suggest_mood
    except Exception as exc:  # noqa: BLE001
        logger.debug("[transform_selector] music_mood_llm_fit import failed: %s", exc)
        return
    try:
        suggestion = suggest_mood(
            niche_id=niche_id,
            hook=str(blueprint_context.get("hook") or ""),
            title=str(blueprint_context.get("title") or ""),
            summary=str(blueprint_context.get("summary") or ""),
            available_moods=sorted(available_moods),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[transform_selector] suggest_mood raised (should not): %s", exc)
        return
    if suggestion is None:
        return
    agree = suggestion.top_mood == music_choice.dimension_value
    logger.info(
        "[transform_selector] LLM_STEER music_mood niche=%s bandit=%s llm=%s "
        "confidence=%.2f agree=%s reasoning=%s",
        niche_id,
        music_choice.dimension_value,
        suggestion.top_mood,
        suggestion.confidence,
        agree,
        suggestion.reasoning[:120] if suggestion.reasoning else "",
    )


__all__ = [
    "TransformationChoice",
    "TransformationChoices",
    "select_transformation_dimensions",
]
