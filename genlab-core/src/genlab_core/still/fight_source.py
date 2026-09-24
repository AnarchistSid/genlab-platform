"""Whether a fight has a source we may use. Asked BEFORE anything else.

ANIME-PEAK-01 §1. Footage source is the whole rights question for the edit
lane. A fight whose only source is a fan upload, a compilation channel, or a
full episode outside a licensed channel is NOT MADE: `source_unlicensed`,
counted, and never worked around.

This module only reads metadata — search results, uploader names. It does not
download. The download happens after a verdict, and only on a pass.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_RIGHTS = Path(__file__).resolve().parents[3] / "config" / "anime_rights.yaml"

HOSTILE = "hostile"
MODERATE = "moderate"


@lru_cache(maxsize=1)
def rights() -> dict:
    import yaml

    return yaml.safe_load(_RIGHTS.read_text())


def _norm(s: str) -> str:
    return "".join(c for c in (s or "").lower() if c.isalnum() or c == " ").strip()


def channel_tier(uploader: str) -> str:
    """'official', 'licensed_regional' or '' — the empty string means no."""
    u = _norm(uploader)
    if not u:
        return ""
    for tier, names in rights()["channels"].items():
        for n in names:
            nn = _norm(n)
            if not nn:
                continue
            # The ALLOWED name must appear in the uploader — not the reverse.
            # Matching both ways let a channel literally called "anime" pass
            # as official, because "anime" is a substring of "Netflix Anime".
            # A rights allowlist that admits a one-word generic name is not an
            # allowlist. The reverse direction is dropped ENTIRELY rather than
            # length-guarded: with a >=8 character guard a channel named
            # "Official" still passed, because it is a substring of "One Piece
            # Official". An uploader that is a strict prefix of an official
            # name is rare; admitting one impostor is not.
            if nn in u:
                return tier
    return ""


def studio_posture(show: str) -> tuple[str, str]:
    """(studio_key, posture) for a show. Unknown defaults to HOSTILE.

    Defaulting an unknown studio to the strictest posture is the whole point:
    the alternative defaults a show we have not thought about to the loosest
    handling we have.
    """
    s = _norm(show)
    for key, cfg in rights()["studios"].items():
        if key == "default":
            continue
        for title in cfg.get("shows", []):
            t = _norm(title)
            if t and (t in s or s in t):
                return key, cfg["posture"]
    return "default", rights()["studios"]["default"]["posture"]


@dataclass
class SourceVerdict:
    fight: str
    show: str
    studio: str
    posture: str
    ok: bool = False
    reason: str = ""
    url: str = ""
    uploader: str = ""
    tier: str = ""
    title: str = ""
    duration_s: float = 0.0
    gate: str = ""
    episode: int | None = None
    considered: list[dict] = field(default_factory=list)

    def row(self) -> str:
        if self.ok:
            return (
                f"{self.fight:<26} OK      {self.tier:<17} {self.uploader[:26]:<26} "
                f"{self.duration_s:5.0f}s"
            )
        return f"{self.fight:<26} REFUSED {self.reason}"


#: Words that appear in half of anime titles and identify nobody.
_GENERIC_WORDS = frozenset(
    {
        "man", "the", "and", "devil", "demon", "king", "lord", "master",
        "hero", "boy", "girl", "clan", "slayer", "hunter", "chainsaw", "titan",
    }
)


def names_the_fight(video_title: str, fight: str, show: str) -> bool:
    """Whether this clip is THE FIGHT, not merely from the right show.

    The channel gate says the uploader is official. It says nothing about
    which footage this is. Measured on the pilot, the first pass returned,
    all from Crunchyroll and all passing the channel gate:

        "Goku vs Frieza"      -> "Dragon Ball Z: Broly - The Legendary..."
        "Maki vs the Zenin"   -> "JUJUTSU KAISEN Shibuya Incident | TRAILER"
        "Yuta vs Yuji"        -> "Yuji Has Become a War God"

    A selector calibrated on a Broly clip would be calibrated on nothing.
    Both combatants must be named, because "Goku" alone matches half of
    Dragon Ball and a trailer matches everything.
    """
    vt = _norm(video_title)
    if not vt:
        return False
    parts = [p.strip() for p in _norm(fight).replace(" vs ", "|").split("|")]
    combatants = [p for p in parts if p and p not in ("the",)]
    if len(combatants) < 2:
        return all(_norm(c) in vt for c in combatants)

    # Each side may be a phrase ("the zenin"); any content word of it counts.
    def side_present(side: str) -> bool:
        """A side is present only on a DISTINCTIVE word, or the whole phrase.

        Matching any word over two characters let "katana man" match
        "Chainsaw Man" — so the gate accepted "Denji vs The Eternity Devil"
        as Denji vs Katana Man, from an official channel, and it would have
        been marked and rendered as the wrong fight. The same too-permissive
        substring shape as the channel allowlist, one layer along.
        """
        if side in vt:
            return True
        words = [w for w in side.split() if len(w) >= 5 and w not in _GENERIC_WORDS]
        return any(w in vt for w in words)

    if not all(side_present(c) for c in combatants):
        return False
    # A trailer is not a fight, whatever it names.
    return not any(
        w in vt for w in ("trailer", "teaser", "opening", "ending", "op ", "ed ", "promo")
    )


#: Channels that upload FULL EPISODES under licence. For these the gate is the
#: episode number, not the fight's name — a licensed full episode is a
#: licensed source of every fight inside it, and no full-episode upload names
#: the fights it contains.
FULL_EPISODE_CHANNELS = {
    "muse asia", "muse indonesia", "ani-one asia", "ani-one asia ultra",
    "medialink", "medialinkanimation",
}

_EP_PATTERNS = (
    r"episode\s*0*{n}\b",
    r"\bep\.?\s*0*{n}\b",
    r"\be0*{n}\b",
    r"#0*{n}\b",
    r"\u7b2c0*{n}\u8a71",
    r"\|\s*0*{n}\s*\|",
)


def is_full_episode_channel(uploader: str) -> bool:
    u = _norm(uploader)
    return any(c in u or u in c for c in (_norm(x) for x in FULL_EPISODE_CHANNELS))


def episode_matches(video_title: str, episode: int | list | None,
                    season: int | None = None) -> bool:
    """Whether this upload is the episode the fight is in.

    ANIME-PEAK-02 §1. ``names_the_fight`` is the right gate for a CLIP
    channel, where the upload is cut to one scene and titled after it. It is
    the wrong gate for a licensed full episode, which never names the fights
    it contains — applying it there rejects the most clearly-licensed source
    available.

    Matched against several numbering conventions because one distributor
    writes "Episode 19", "EP19", "#19" and "\u7b2c19\u8a71" across its own catalogue.
    """
    import re

    if not episode:
        return False
    numbers = episode if isinstance(episode, (list, tuple)) else [episode]
    t = (video_title or "").lower()
    for n in numbers:
        if not n:
            continue
        if any(re.search(pat.format(n=n), t) for pat in _EP_PATTERNS):
            return True
        # Season-relative forms: "S2E12", "S2 E12", "(S2E12)".
        if season and re.search(rf"s0*{season}\s*e0*{n}\b", t):
            return True
    return False


def find(
    fight: str,
    show: str,
    queries: list[str],
    *,
    ytdlp: str = "yt-dlp",
    per_query: int = 8,
    max_duration_s: float = 1500.0,
    episode: int | list | None = None,
    season: int | None = None,
    aka: list[str] | None = None,
) -> SourceVerdict:
    """Search for a usable clip. Metadata only; nothing is downloaded here."""
    studio, posture = studio_posture(show)
    v = SourceVerdict(fight=fight, show=show, studio=studio, posture=posture)
    best: tuple[int, dict] | None = None

    for q in queries:
        r = subprocess.run(
            [ytdlp, "--no-warnings", "--flat-playlist", "-J", f"ytsearch{per_query}:{q}"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        try:
            entries = json.loads(r.stdout or "{}").get("entries") or []
        except ValueError:
            continue
        for e in entries:
            up = e.get("channel") or e.get("uploader") or ""
            tier = channel_tier(up)
            rec = {
                "id": e.get("id"),
                "uploader": up[:50],
                "title": (e.get("title") or "")[:80],
                "duration_s": float(e.get("duration") or 0.0),
                "tier": tier or "-",
            }
            v.considered.append(rec)
            if not tier:
                continue
            full_ep = is_full_episode_channel(up)
            if full_ep:
                if not episode_matches(rec["title"], episode, season):
                    rec["tier"] = f"{tier} (licensed channel, but not episode {episode})"
                    continue
                rec["gate"] = f"episode {episode}"
            else:
                if rec["duration_s"] > max_duration_s:
                    rec["tier"] = f"{tier} (too long — a full episode on a clip channel)"
                    continue
                if not (names_the_fight(rec["title"], fight, show)
                        or any(_norm(a) in _norm(rec["title"]) for a in (aka or []))):
                    rec["tier"] = f"{tier} (official channel, but not this fight)"
                    continue
                rec["gate"] = "names the fight"
            # A named clip beats a full episode: less to search, and the
            # uploader has already decided this scene is the moment.
            rank = (0 if tier == "official" else 1) + (2 if full_ep else 0)
            if best is None or rank < best[0]:
                best = (rank, rec)

    if best is None:
        n_off = sum(1 for c in v.considered if c["tier"] == "-")
        n_wrong = sum(1 for c in v.considered if "not this fight" in str(c["tier"]))
        if n_wrong:
            v.reason = (
                f"source_wrong_footage: {len(v.considered)} results — "
                f"{n_off} from channels that are neither official nor "
                f"licensed, and {n_wrong} from allowed channels that are "
                f"not this fight"
            )
        else:
            v.reason = (
                f"source_unlicensed: {len(v.considered)} results, {n_off} from "
                f"channels that are neither official nor licensed-regional, "
                f"0 usable"
            )
        logger.warning("[fight] %s — %s", fight, v.reason)
        return v

    rec = best[1]
    v.ok, v.tier = True, rec["tier"]
    v.url = f"https://www.youtube.com/watch?v={rec['id']}"
    v.uploader, v.title, v.duration_s = rec["uploader"], rec["title"], rec["duration_s"]
    v.gate = rec.get("gate", "")
    logger.info("[fight] %s — %s via %s (%s)", fight, v.tier, v.uploader, v.title[:50])
    return v


def attribution(show: str, studio: str, channel: str) -> str:
    return rights()["attribution"]["template"].format(show=show, studio=studio, channel=channel)
