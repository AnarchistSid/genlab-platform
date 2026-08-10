"""Typed story shape for the pipeline context.

Why this module exists
======================

On 2026-06-19 we shipped three PRs (#358, #359, #360) fixing three different
silent-drop bugs in the gaming pipeline. All three were the same architectural
class: the ``context["stories"]`` interface is a free-form ``list[dict[str, Any]]``
with no shared schema, no convention enforcement, and no cross-stage contract test.

The three bug variants:

* **PR #358** — ``FetchGamingStories`` REPLACED ``context["stories"]`` instead of
  merging — discarded ~45 upstream stories per run. The merge-vs-replace
  convention was undocumented; each new stage author re-derives it.

* **PR #359** — KeyError 'score' on the upstream schema. Upstream fetchers
  didn't all emit a ``score`` field; ``sorted(..., key=lambda s: s["score"])``
  crashed mid-pipeline. No schema validation at fetcher boundaries.

* **PR #360** — ``_TRUSTED_GAMING_SOURCES`` allowlist drifted from producers.
  Filter had 2 entries; 4 upstream fetchers' source values were silently
  rejected. Operator saw "useless content" because real Twitch / YouTube /
  Reddit / Steam clips were filter-dropped.

This module is the architectural fix for the bug *class*. It introduces:

1. **``StoryCandidate``** — Pydantic model that fetchers instantiate at their
   output boundary. Schema validation at fetcher time, not at sort time three
   stages later. Solves PR #359.

2. **``merge_stories(context, new)``** / **``replace_stories(context, kept)``** —
   intent-revealing helpers. A fetcher calls ``merge_stories``; a filter calls
   ``replace_stories``. The named function is the contract: there is no third
   option that "looks like" assignment. Solves PR #358.

3. **``FetcherStage``** mixin + **``collect_emitted_sources()``** — fetcher stages
   declare ``EMITTED_SOURCES: ClassVar[frozenset[str]]``. Filter stages aggregate
   the registry instead of maintaining a hardcoded frozenset. Drift becomes a
   startup assertion, not a runtime silent drop. Solves PR #360.

Migration is backward compatible. Existing fetchers that emit raw ``dict``
keep working via ``StoryCandidate.from_raw(dict)`` at the helper boundary —
opt in incrementally, one fetcher per PR.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

logger = logging.getLogger(__name__)


def _extract_validation_reason(exc: ValidationError) -> str:
    """Extract the human-relevant line from a pydantic ValidationError.

    pydantic v2 formats errors as multi-line output with a URL footer;
    ``splitlines()[-1]`` grabs the URL instead of the useful message.
    Prefer the line starting with 'Value error,' when present; fall back
    to a compact stringification.
    """
    text = str(exc)
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Value error,"):
            # Trim the pydantic [type=value_error, ...] suffix for brevity
            trimmed = stripped[len("Value error,") :].strip()
            bracket = trimmed.find(" [type=")
            return trimmed[:bracket].strip() if bracket >= 0 else trimmed
    # Fall back to the first non-empty line that isn't the header
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith(("For further information", "1 validation")):
            return stripped[:200]
    return "validation_error"


class StoryCandidate(BaseModel):
    """Schema-validated story shape used by every pipeline stage.

    Required fields are the contract every fetcher MUST emit. Optional fields
    are niche-specific enrichment populated by enrichment stages (EnrichWithIGDB,
    ExtractGamingMedia, etc.). Extra fields are allowed via ``model_config`` so
    existing stages that write scratch keys (e.g. ``_trending_video``,
    ``clip_index``) keep working unchanged.

    Schema validation happens at ``StoryCandidate.from_raw(dict)`` — call it at
    the fetcher's output boundary and divergence becomes a clear error, not a
    KeyError three stages later.

    Video-invariant contract (2026-08-10, Option C — Phase 1)
    ---------------------------------------------------------
    A story is one of two shapes:

    1. **Video-bearing story** — the fetcher found a specific video clip.
       MUST populate ``video_id`` (non-empty string) so downstream dedup
       works. This is what ``FetchTrendingVideos`` / ``FetchTwitchClips`` /
       ``FetchAnimePromos`` emit for the 4 healthy niches. ``channel_id``
       is strongly encouraged (drives attribution layers L1-L6) but
       intentionally NOT required in Phase 1 — sports has 36/40 rows with
       NULL source_channel_id today, so enforcement would drop working
       content. Phase 2 will tighten this after per-fetcher retrofits.

    2. **Signal story (bypass)** — the fetcher emits a trending signal
       (e.g. "LoL is spiking on Steam today") that isn't itself a playable
       clip; a downstream enrichment stage may find a clip for it. MUST
       set ``bypass_video_id_dedup=True`` AND ``bypass_reason=<slug>`` so
       the pipeline knows this story is intentionally without video_id.

    Enforced via ``model_validator``. Prevents the ``fetcher-schema-drift-
    from-downstream-contract`` class-of-bug that produced the 30-day
    gaming repeat pattern (42 blueprints / 19 titles / 1 distinct video_id;
    every dedup key silently no-op'd because video_id was empty).
    """

    model_config = ConfigDict(extra="allow")

    # Required — every fetcher MUST emit these
    title: str
    source: str
    source_url: str

    # Optional — defaulted at validate time so downstream sort/score never KeyError
    score: float = Field(default=0.5, ge=0.0, le=1.0)
    summary: str = ""
    published_at: str = ""

    # Identifiers — populated by various fetchers, optional everywhere else
    video_id: str | None = None
    story_id: str | None = None

    # Source channel — populated by YouTube-based fetchers (trending, RSS,
    # keyword search). PR #B (2026-07-10, Markanimation incident): declared
    # as first-class fields so they survive round-trip through model_dump()
    # into stories.extra JSONB. Before this PR, `channel_name` was passed
    # as an unstructured extra and `channel_id` was silently dropped at
    # TrendingVideo.to_story() — the resulting NULL source_channel_id on
    # blueprints broke source-discovery + made source-creator credit
    # impossible. Non-YouTube sources leave these None.
    channel_id: str | None = None
    channel_name: str | None = None

    # Video-invariant bypass (2026-08-10 Option C — see class docstring).
    # A fetcher that legitimately can't populate video_id + channel_id at
    # emit time (Steam spike, Twitch top-games, RSS text stories) MUST set
    # both fields together. ``bypass_reason`` is a slug of the form
    # ``<source>:<why>`` — e.g. ``steam_spike:signal_not_video`` — that
    # shows up in structured logs + metrics, so operators can see WHICH
    # bypass path is active. A missing reason with the flag set is a
    # validation error (bypass without justification is worse than no
    # bypass).
    bypass_video_id_dedup: bool = False
    bypass_reason: str = ""

    # Gaming-specific enrichment (set by EnrichWithIGDB / SteamSpikeFetcher).
    # Optional everywhere; non-gaming pipelines simply leave them None.
    steam_app_id: int | None = None
    igdb_game_id: int | None = None
    developer: str | None = None

    @model_validator(mode="after")
    def _enforce_video_invariant(self) -> StoryCandidate:
        """Enforce the video-invariant contract described in the class docstring.

        Two legal shapes:
          A. video_id populated (video-bearing story)
          B. bypass_video_id_dedup=True + bypass_reason non-empty (signal story)

        Anything else raises ValueError — caught + logged by ``merge_stories``.

        channel_id is not enforced in Phase 1 (see class docstring) — sports
        currently ships 36/40 blueprints with NULL source_channel_id, and
        forcing that field would drop working content. Attribution-layer
        strictness is Phase 2 scope.
        """
        has_video_id = bool(self.video_id)
        declares_bypass = bool(self.bypass_video_id_dedup)

        if has_video_id:
            # Shape A — video-bearing. Bypass flag ignored (a fetcher that
            # populated video_id doesn't need bypass; we tolerate the flag
            # being set defensively rather than making the two shapes
            # mutually exclusive).
            return self

        if declares_bypass:
            if not self.bypass_reason.strip():
                raise ValueError(
                    f"story from source={self.source!r} declared "
                    f"bypass_video_id_dedup=True but bypass_reason is empty; "
                    f"bypass requires an explicit reason slug (e.g. "
                    f"'steam_spike:signal_not_video')"
                )
            return self

        # No video_id AND no bypass — this is the shape that produced the
        # gaming repeat pattern. Fail loudly so merge_stories drops the
        # story with a diagnostic log.
        raise ValueError(
            f"story from source={self.source!r} title={self.title!r} lacks "
            f"video_id AND does not declare bypass_video_id_dedup=True with "
            f"a reason. Video-bearing fetchers must populate video_id; "
            f"signal-only fetchers must set bypass_video_id_dedup=True + "
            f"bypass_reason=<source>:<why>. "
            f"See genlab_core.pipeline.models.StoryCandidate docstring."
        )

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> StoryCandidate:
        """Convert a legacy dict-shaped story to a typed candidate.

        Tolerant by design — missing optional fields use defaults; unknown keys
        are preserved via ``extra='allow'``. Use this at fetcher boundaries
        while migrating. Once a fetcher emits ``StoryCandidate`` directly the
        conversion is a no-op.
        """
        return cls.model_validate(raw)


# ─── merge helpers — intent-revealing names ───────────────────────────────────


def merge_stories(
    context: dict[str, Any],
    new_stories: list[StoryCandidate | dict[str, Any]],
    *,
    prepend: bool = False,
) -> None:
    """Append- (or prepend-) merge — for fetcher stages that ADD to the pool.

    Replaces the ``context["stories"] = existing + new_stories`` pattern
    copy-pasted across 7+ fetcher files. The named function IS the contract:
    if you call ``merge_stories`` you are an additive producer. If you call
    ``replace_stories`` you are a filter. PR #358's bug becomes impossible
    because there is no third option that "looks like" assignment.

    Validates each item against ``StoryCandidate`` at the merge boundary, so
    schema divergence raises with a clear ``ValidationError`` instead of a
    KeyError three stages later.

    ``prepend=True`` puts the new stories FIRST in the resulting list, then
    the existing ones — used by FetchTrendingVideos at the direct-fetch
    merge site (P1 phase 4) where downstream stages give earlier-in-list
    items priority (top-N selection by position before scoring). Default
    ``prepend=False`` matches the historical append semantics that every
    other migrated fetcher uses.

    Stories are stored as dicts (existing consumers read with ``.get(...)``).
    Typed access via ``StoryCandidate.from_raw(item)`` is a 1-line opt-in at
    the consumer's discretion.

    Video-invariant enforcement (2026-08-10 Option C)
    -------------------------------------------------
    Each story is validated against ``StoryCandidate``'s video invariant
    (see class docstring). Two failure modes:

    * **Contract violation (no video_id + no bypass declared)** — the story
      is DROPPED from the merge with a WARNING log carrying source, title,
      and the exception message. The rest of the batch continues.
    * **Legitimate bypass (bypass_video_id_dedup=True + reason)** — the
      story is kept, an INFO log fires with the reason slug so operators
      can audit which fetcher paths are non-video.

    This is fail-open-with-visible-signal (rule #19): the merge never
    raises, but every skipped story leaves a diagnostic. Enforcement mode
    can be tightened later (e.g. raise ``ValidationError`` if drop rate
    exceeds a threshold) without changing the caller contract.
    """
    existing = context.get("stories", [])
    # Materialize once so len() + iteration don't double-consume a generator.
    incoming = list(new_stories)
    validated: list[StoryCandidate] = []
    dropped_count = 0
    niche_id = context.get("niche_id", "unknown")

    for item in incoming:
        try:
            candidate = (
                item if isinstance(item, StoryCandidate) else StoryCandidate.from_raw(item)
            )
        except ValidationError as exc:
            # Contract violation — drop the story, log the diagnostic. Rest
            # of the batch continues. If this fires repeatedly for a specific
            # fetcher, that fetcher needs either video_id population OR an
            # explicit bypass declaration (see StoryCandidate docstring).
            source = (
                item.get("source", "?") if isinstance(item, dict) else getattr(item, "source", "?")
            )
            title = (
                item.get("title", "?") if isinstance(item, dict) else getattr(item, "title", "?")
            )
            logger.warning(
                "[merge_stories] DROPPED story from fetcher; niche=%s source=%s "
                "title=%r reason=%s",
                niche_id,
                source,
                title[:80] if isinstance(title, str) else title,
                _extract_validation_reason(exc),
            )
            dropped_count += 1
            continue

        # Bypass audit trail — INFO log per bypassed story so operators can
        # see WHICH source is signal-only. Aggregate via journalctl or a
        # future Postgres counter.
        if candidate.bypass_video_id_dedup:
            logger.info(
                "[merge_stories] bypass niche=%s source=%s reason=%s title=%r",
                niche_id,
                candidate.source,
                candidate.bypass_reason,
                candidate.title[:60],
            )
        validated.append(candidate)

    if dropped_count:
        logger.warning(
            "[merge_stories] niche=%s dropped %d of %d incoming stories on "
            "video-invariant contract; see prior WARN lines for per-story detail",
            niche_id,
            dropped_count,
            len(incoming),
        )

    new_dicts = [s.model_dump() for s in validated]
    if prepend:
        context["stories"] = new_dicts + list(existing)
    else:
        context["stories"] = list(existing) + new_dicts


def replace_stories(
    context: dict[str, Any],
    kept: list[StoryCandidate | dict[str, Any]],
) -> None:
    """Replace — for filter/gate stages that NARROW the pool.

    The name communicates intent. Used by ``relevance_gate``,
    ``pre_download_dedup``, ``video_gate``, ``filter_gaming_stories``,
    ``score_gaming_clips`` top-N selection, etc.

    Validates each kept item against ``StoryCandidate`` including the
    video-invariant contract (Option C, 2026-08-10). A filter that
    accidentally strips ``video_id`` / ``channel_id`` from a valid story
    gets that story dropped with a WARN log — same fail-open pattern as
    ``merge_stories``. Filters are usually working on already-validated
    dicts, so this is a defense-in-depth pin.
    """
    incoming = list(kept)
    validated: list[StoryCandidate] = []
    dropped_count = 0
    niche_id = context.get("niche_id", "unknown")

    for item in incoming:
        try:
            candidate = (
                item if isinstance(item, StoryCandidate) else StoryCandidate.from_raw(item)
            )
        except ValidationError as exc:
            source = (
                item.get("source", "?") if isinstance(item, dict) else getattr(item, "source", "?")
            )
            title = (
                item.get("title", "?") if isinstance(item, dict) else getattr(item, "title", "?")
            )
            logger.warning(
                "[replace_stories] DROPPED story from filter; niche=%s source=%s "
                "title=%r reason=%s",
                niche_id,
                source,
                title[:80] if isinstance(title, str) else title,
                _extract_validation_reason(exc),
            )
            dropped_count += 1
            continue
        validated.append(candidate)

    if dropped_count:
        logger.warning(
            "[replace_stories] niche=%s filter dropped %d of %d kept stories on "
            "video-invariant contract",
            niche_id,
            dropped_count,
            len(incoming),
        )

    context["stories"] = [s.model_dump() for s in validated]


# ─── producer registry — single source of truth for source values ────────────


class FetcherStage:
    """Mixin that fetcher stages inherit from to declare their emitted sources.

    Resolves PR #360's bug class: filter stages can read this registry at
    startup and assert it matches their trust list. The registry IS the truth;
    the trust list is generated from it, not maintained by hand.

    Usage::

        class FetchTwitchClips(FetcherStage):
            EMITTED_SOURCES = frozenset({"twitch_clips"})

            def execute(self, context): ...
    """

    EMITTED_SOURCES: ClassVar[frozenset[str]] = frozenset()


def collect_emitted_sources(stage_classes: list[type]) -> frozenset[str]:
    """Aggregate every fetcher's declared source values into one frozenset.

    Used by ``FilterGamingStories`` (and any other allowlist consumer) to
    derive its trust list from the actual producers instead of maintaining
    a hardcoded frozenset. Adding a new fetcher with
    ``EMITTED_SOURCES = frozenset({"new_source"})`` auto-extends the trust
    list. A contract test pins the relationship so any future fetcher that
    forgets to declare ``EMITTED_SOURCES`` is caught at CI time.

    Classes that aren't ``FetcherStage`` subclasses are skipped silently so
    you can pass the full pipeline stage list without filtering first.
    """
    sources: set[str] = set()
    for cls in stage_classes:
        if isinstance(cls, type) and issubclass(cls, FetcherStage):
            sources.update(cls.EMITTED_SOURCES)
    return frozenset(sources)


__all__ = [
    "StoryCandidate",
    "FetcherStage",
    "merge_stories",
    "replace_stories",
    "collect_emitted_sources",
]
