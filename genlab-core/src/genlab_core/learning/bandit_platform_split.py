"""Per-platform bandit arm helpers.

The content-type bandit (gameplay_clip, style:sports:bold_claim, etc.)
splits into per-(content_type, platform) arms when
``GENLAB_PER_PLATFORM_BANDIT_ENABLED=1``. Reads + writes use the
helpers here to keep the doubled-underscore convention (matches
``meta_prior.py:100`` format) in one place.

Why per-platform arms
=====================
The single-arm-per-niche bandit can only learn "what content type
works on AVERAGE across platforms." But sports YouTube prefers
different hook types than sports Instagram; ai_creators IG prefers
different than ai_creators YT. Per-platform arms double the learning
signal granularity by splitting one (niche, content_type) row into
N rows: (niche, content_type), (niche, content_type__instagram),
(niche, content_type__youtube), ...

Asymmetric decision vs reward split
===================================
- **Decision time** (``push_to_backlog._classify_arm_with_propensity``):
  platform is NOT yet known — one blueprint publishes to multiple
  platforms. Pick ONE arm. The picker scores per-platform variants
  and aggregates via ``max`` (best-platform UCB wins).
- **Reward attribution time** (``metric_collector`` bandit_updater
  callsite): platform IS known — each platform's metrics arrive
  separately. Update the platform-specific arm
  ``{content_type}__{platform}`` instead of (or in addition to) the
  base arm.

Backward compatibility
======================
- Existing ``(niche, gameplay_clip)`` rows stay untouched
- New ``(niche, gameplay_clip__instagram)`` rows accumulate alongside
- Flag OFF (default) → only the base arm is updated (current behavior)
- Flag ON → BOTH the base arm AND the per-platform arm are updated
  for one transition period. Operator can later DELETE the base arms
  once per-platform have enough observations.

Opt-in via env. Default disabled = no behavior change.
"""

from __future__ import annotations

from typing import Final

# Platforms to split arms by. Start with the 2 highest-engagement
# platforms per the AGENT-LEARNING-ENGINES synthesis. Adding more
# platforms = more arm rows = slower convergence per arm. Revisit
# when each platform has >50 observations per arm consistently.
#
# Why not facebook/threads/twitter/tiktok yet: their engagement volume
# is comparable to IG/YT only in spikes; splitting them now means most
# arms hover below the LinUCB cold-start floor (10 obs) for months
# before producing trustworthy posteriors. Adding a platform here is
# a one-line change once volume warrants it.
_PER_PLATFORM_BANDIT_PLATFORMS: Final[tuple[str, ...]] = ("instagram", "youtube")


def is_enabled() -> bool:
    """Single source of truth for the opt-in flag.

    Strict ``"1"`` only (no ``"true"``/``"yes"``/``"on"``) — matches
    the PR #634 convention and avoids the "user thought they enabled
    it" failure mode. Default OFF preserves all existing behavior.

    Centralized so callers short-circuit cheaply
    (``if not is_enabled(): return base_arm_id``) without duplicating
    the check. Tests toggle via ``monkeypatch.setenv``.
    """
    from genlab_core.settings import env_true

    return env_true("GENLAB_PER_PLATFORM_BANDIT_ENABLED")


def platform_arm_id(base_arm_id: str, platform: str) -> str:
    """Compose the per-platform arm_id.

    Mirrors the ``content_type__platform`` format already in use at
    ``meta_prior.py:100``. The doubled-underscore separator is the
    canonical convention — single underscore would collide with arm
    names that contain underscores (e.g. ``gameplay_clip``).

    No validation: caller is expected to pass a platform from
    :func:`list_split_platforms`. We don't raise for unknown platforms
    because the inverse parser :func:`parse_platform_arm_id` is the
    contract enforcer.
    """
    return f"{base_arm_id}__{platform}"


def parse_platform_arm_id(arm_id: str) -> tuple[str, str | None]:
    """Inverse of :func:`platform_arm_id`. Returns ``(base, platform_or_None)``.

    - ``"gameplay_clip__instagram"`` → ``("gameplay_clip", "instagram")``
    - ``"style:gaming:revelation__youtube"`` → ``("style:gaming:revelation", "youtube")``
    - ``"gameplay_clip"`` → ``("gameplay_clip", None)`` — no separator
    - ``"foo__bar"`` → ``("foo__bar", None)`` — ``bar`` not in split set

    The ``platform`` field is None unless the trailing segment is
    actually in :data:`_PER_PLATFORM_BANDIT_PLATFORMS`. This prevents
    treating legacy arm names that happen to contain ``__`` (rare but
    possible) as per-platform arms.

    Uses ``rpartition`` so complex base names with multiple ``__``
    segments are preserved — only the LAST ``__platform`` is stripped.
    """
    if "__" not in arm_id:
        return arm_id, None
    base, _, platform = arm_id.rpartition("__")
    if platform in _PER_PLATFORM_BANDIT_PLATFORMS:
        return base, platform
    return arm_id, None


def list_split_platforms() -> tuple[str, ...]:
    """The current set of per-platform splits.

    Pinned in tests to prevent silent platform additions — adding a
    platform here changes the shape of the bandit posterior surface
    across all 5 niches and should be a deliberate decision with a
    matching test update.
    """
    return _PER_PLATFORM_BANDIT_PLATFORMS


def expand_arm_for_platforms(base_arm_id: str) -> list[str]:
    """Return ``[base, base__plat1, base__plat2, ...]`` when enabled.

    When the flag is enabled, returns the base arm plus every
    per-platform variant in :func:`list_split_platforms` order. When
    disabled, returns just ``[base_arm_id]`` — caller's lookup loop is
    unchanged in the off path.

    Used by the decision-time scorer to look up UCB scores across all
    variants and aggregate via ``max``. Order is stable (matches
    :data:`_PER_PLATFORM_BANDIT_PLATFORMS` declaration order) so tests
    can pin the iteration order without flake.
    """
    if not is_enabled():
        return [base_arm_id]
    return [base_arm_id] + [platform_arm_id(base_arm_id, p) for p in _PER_PLATFORM_BANDIT_PLATFORMS]
