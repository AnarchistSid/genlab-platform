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

#: AniList lists these as cast entries. They have portraits and they are not
#: characters; a reel that cuts to "Narrator" on a beat is worse than one that
#: cuts to footage. TOUGEN ANKI's top-billed entry is exactly this.
_NON_CHARACTERS = frozenset({"narrator", "narration", "announcer"})


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


#: Story keys holding a full-frame picture OF THE SHOW, most specific first.
#: ``cover_image_url`` leads because it is the show's key art -- the single
#: image most likely to be recognised as this title.
_ART_KEYS = (
    "cover_image_url",
    "key_art_url",
    "banner_image_url",
    "press_still_url",
    "thumbnail_url",
    "image_url",
)


@dataclass(frozen=True)
class ShowArt:
    """Everything OF THE SHOW that the story record itself holds.

    The narrow rule has not moved: this reads the story and nothing else. What
    changed is the record. The fetcher now carries cover, banner, PV id and
    character portraits out of the SAME AniList response it already made, so
    material that was previously "available upstream but not ours" is now
    genuinely carried, with ``provenance`` naming the entry it came from.
    """

    cover: str = ""
    banner: str = ""
    characters: tuple[dict[str, str], ...] = ()
    attribution: str = ""
    provenance: str = ""
    dominant_colour: str = ""

    @property
    def has_any(self) -> bool:
        return bool(self.cover or self.banner or self.characters)

    def character_for(self, text: str) -> dict[str, str] | None:
        """The portrait of a character NAMED in this line, if any.

        Matches the full name OR either part of it. AniList stores
        "Shinpei Gotou" and "Satoko Kirigaya"; a narration script says
        "Shinpei" and "Satoko". Requiring the full string meant the character
        slot never fired on either real reel — the grammar asked for a
        portrait on the beat that names someone and silently got a PV shot.

        Longest match first, so "Yami Sukehiro" beats a show that also has a
        character called "Yami", and a full name beats a bare given name.
        """
        low = text.lower()
        best: tuple[int, dict[str, str]] | None = None
        for c in self.characters:
            name = (c.get("name") or "").strip()
            if not name or name.lower() in _NON_CHARACTERS:
                continue
            candidates = [name] + [p for p in name.split() if len(p) >= 4]
            for cand in candidates:
                if cand.lower() in low and (best is None or len(cand) > best[0]):
                    best = (len(cand), c)
        return best[1] if best else None


def show_art(story: dict[str, Any]) -> ShowArt:
    """Assemble the show's own material from the story record."""

    def pick(*keys: str) -> str:
        for k in keys:
            v = str(story.get(k) or "").strip()
            if v.startswith("http"):
                return v
        return ""

    chars = tuple(
        {"name": str(c.get("name", "")), "image_url": str(c.get("image_url", ""))}
        for c in (story.get("characters") or [])
        if str(c.get("image_url", "")).startswith("http")
    )
    return ShowArt(
        cover=pick("cover_image_url", "key_art_url", "thumbnail_url", "image_url"),
        banner=pick("banner_image_url"),
        characters=chars,
        attribution=str(story.get("art_attribution") or story.get("source") or "").strip(),
        provenance=str(story.get("art_provenance") or "").strip(),
        dominant_colour=str(story.get("cover_color") or "").strip(),
    )


def carried_art(story: dict[str, Any]) -> tuple[str, str]:
    """(url, attribution) if the STORY ITSELF holds usable art, else ("", "").

    Deliberately narrow. It reads the story record and nothing else: a fetch
    from the upstream catalogue would be new material under someone else's
    licence, dressed as something the pipeline already had.
    """
    for key in _ART_KEYS:
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
                        beat_index=beat.index,
                        origin=CARRIED,
                        url=url,
                        attribution=attrib,
                        cost_usd=0.0,
                        is_hero=True,
                    )
                )
                continue
        out.append(
            StillSource(
                beat_index=beat.index,
                origin=GENERATED,
                prompt=build_prompt(beat, board.kit),
                cost_usd=unit,
                is_hero=is_hero,
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
