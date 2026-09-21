"""Where each shot's picture comes from, and what it cost.

Preference order, from ANIME-03 §3:

  1. Key art or a press still the STORY CARRIES -- rights-clean, and it looks
     like the show rather than like a model's idea of the show. Carries an
     attribution slate.
  2. Otherwise a generated `still` per beat, from the kit's ONE prompt
     template in the niche palette.

"Carries" means the story record actually holds the URL. Fetching art from
elsewhere and calling it carried is how a rights problem gets laundered into
a pipeline: Firefly Wedding's cover art exists on AniList, but the story row
holds only title/summary/url, so this path generates instead. The difference
matters and is recorded per still in ``StillSource.origin``.

Every still is logged with its origin and its MEASURED cost, so the element
table in the deliverable is a receipt rather than an estimate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from genlab_core.capabilities import select
from genlab_core.still.beats import Beat, Storyboard

logger = logging.getLogger(__name__)

CARRIED = "carried"
GENERATED = "generated"


@dataclass(frozen=True)
class StillSource:
    """One shot's picture: where it came from and what it cost."""

    beat_index: int
    origin: str
    prompt: str = ""
    url: str = ""
    attribution: str = ""
    cost_usd: float = 0.0
    is_hero: bool = False

    @property
    def needs_attribution_slate(self) -> bool:
        return self.origin == CARRIED


def build_prompt(beat: Beat, kit: dict[str, Any]) -> str:
    """The kit's single template, filled. Never composed at a call site."""
    sp = kit["still_prompt"]
    pal = kit["palette"]
    return sp["template"].format(beat=beat.text.rstrip(". "), palette=pal["description"].strip())


def negative_prompt(kit: dict[str, Any]) -> str:
    return kit["still_prompt"]["negative"].format(avoid=kit["palette"]["avoid"].strip())


def carried_art(story: dict[str, Any]) -> tuple[str, str]:
    """(url, attribution) if the STORY ITSELF holds usable art, else ("", "").

    Deliberately narrow. It reads the story record and nothing else: a fetch
    from the upstream catalogue would be new material under someone else's
    licence, dressed as something the pipeline already had.
    """
    for key in ("key_art_url", "press_still_url", "thumbnail_url", "image_url"):
        url = str(story.get(key) or "").strip()
        if url.startswith("http"):
            attrib = str(story.get("art_attribution") or story.get("source") or "").strip()
            return url, attrib
    return "", ""


def plan_stills(
    board: Storyboard, story: dict[str, Any], *, context: str = "fire"
) -> list[StillSource]:
    """One StillSource per shot, priced from the registry before anything runs."""
    cap = select("still", context=context)
    unit = cap.cost_per_unit_usd or 0.0
    url, attrib = carried_art(story)

    out: list[StillSource] = []
    for beat in board.beats:
        is_hero = beat.index == board.hero_index
        if url:
            # Carried art is used for the hero shot only: it is one image, and
            # repeating it under every beat would read as a slideshow of the
            # same picture.
            if is_hero:
                out.append(
                    StillSource(
                        beat_index=beat.index, origin=CARRIED, url=url,
                        attribution=attrib, cost_usd=0.0, is_hero=True,
                    )
                )
                continue
        out.append(
            StillSource(
                beat_index=beat.index, origin=GENERATED,
                prompt=build_prompt(beat, board.kit),
                cost_usd=unit, is_hero=is_hero,
            )
        )
    logger.info(
        "[still] %d shots: %d carried, %d generated via %s, $%.5f",
        len(out),
        sum(1 for s in out if s.origin == CARRIED),
        sum(1 for s in out if s.origin == GENERATED),
        cap.ref,
        sum(s.cost_usd for s in out),
    )
    return out
