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

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field


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

    # Gaming-specific enrichment (set by EnrichWithIGDB / SteamSpikeFetcher).
    # Optional everywhere; non-gaming pipelines simply leave them None.
    steam_app_id: int | None = None
    igdb_game_id: int | None = None
    developer: str | None = None

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
) -> None:
    """Append-merge — for fetcher stages that ADD to the pool.

    Replaces the ``context["stories"] = existing + new_stories`` pattern
    copy-pasted across 7+ fetcher files. The named function IS the contract:
    if you call ``merge_stories`` you are an additive producer. If you call
    ``replace_stories`` you are a filter. PR #358's bug becomes impossible
    because there is no third option that "looks like" assignment.

    Validates each item against ``StoryCandidate`` at the merge boundary, so
    schema divergence raises with a clear ``ValidationError`` instead of a
    KeyError three stages later.

    Stories are stored as dicts (existing consumers read with ``.get(...)``).
    Typed access via ``StoryCandidate.from_raw(item)`` is a 1-line opt-in at
    the consumer's discretion.
    """
    existing = context.get("stories", [])
    validated = [
        s if isinstance(s, StoryCandidate) else StoryCandidate.from_raw(s) for s in new_stories
    ]
    context["stories"] = list(existing) + [s.model_dump() for s in validated]


def replace_stories(
    context: dict[str, Any],
    kept: list[StoryCandidate | dict[str, Any]],
) -> None:
    """Replace — for filter/gate stages that NARROW the pool.

    The name communicates intent. Used by ``relevance_gate``,
    ``pre_download_dedup``, ``video_gate``, ``filter_gaming_stories``,
    ``score_gaming_clips`` top-N selection, etc.

    Validates each kept item against ``StoryCandidate`` so a filter can't
    accidentally introduce malformed entries while narrowing.
    """
    validated = [s if isinstance(s, StoryCandidate) else StoryCandidate.from_raw(s) for s in kept]
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
