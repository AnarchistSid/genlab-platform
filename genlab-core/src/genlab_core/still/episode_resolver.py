"""Which episode a fight is in, from authoritative per-episode data.

ANIME-PEAK-05 §1. A crowd locates WITHIN an episode; an authority names the
episode. Fan-clip title consensus was tried and returned "Jujutsu Kaisen
season 3 episode 4" on a three-vote plurality — for a show with no season 3.
That settled which source does which job.

Two authorities, tried in order:

* **AniList** ``streamingEpisodes`` — titles as the streaming services list
  them, e.g. "Episode 17 - Right and Wrong".
* **Jikan** (MyAnimeList) ``/anime/{id}/episodes`` — a numbered list with
  per-episode titles, and the romaji title as a second string to match.

A fight is resolved when an episode's TITLE or synopsis names it — via the
fight's ``aka`` list, which exists precisely because episodes are known by
their titles ("Hinokami"), or via both combatants. No match means
``episode_unresolved`` and the fight stays out of the pilot, because marking
against an unresolved episode wastes the marking pass.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Rule #25: an explicit UA. WAF-fronted APIs answer 403 to Python-urllib's
# default and it reads as a missing resource.
_UA = {"User-Agent": "GenLab/1.0 (+https://github.com/AnarchistSid/genlab-platform)"}
_ANILIST = "https://graphql.anilist.co"
_JIKAN = "https://api.jikan.moe/v4"

UNRESOLVED = "episode_unresolved"


@dataclass
class Episode:
    number: int | None
    title: str
    synopsis: str = ""
    source: str = ""


@dataclass
class Resolution:
    fight_id: str
    episode: int | None = None
    matched_title: str = ""
    source: str = ""
    confidence: str = ""       # high | medium | none
    evidence: str = ""
    n_episodes_seen: int = 0

    @property
    def resolved(self) -> bool:
        return self.episode is not None

    def row(self) -> str:
        if not self.resolved:
            return f"{self.fight_id:<22} {UNRESOLVED:<20} {self.evidence[:52]}"
        return (f"{self.fight_id:<22} ep {self.episode:<5} {self.source:<8} "
                f"{self.confidence:<7} {self.matched_title[:40]}")


def _post_anilist(query: str, variables: dict) -> dict:
    req = urllib.request.Request(
        _ANILIST, data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={**_UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def find_show(title: str, *, aliases: list[str] | None = None) -> dict:
    """AniList id + MAL id for a show, VALIDATED against the name asked for.

    AniList's search is fuzzy and confident: searching "Demon Slayer" returns
    "Onigiri" — a 13-episode show with nothing to do with it — while the
    romaji "Kimetsu no Yaiba" returns the right series. Taking the first hit
    would resolve every fight in that show against another show's episode
    list, which is the Fairy Tail failure in a new place.

    So each candidate name is tried and the result is only accepted when one
    of the returned titles actually contains, or is contained by, the name
    searched for.
    """
    q = """query ($s: String) { Media(search: $s, type: ANIME) {
             id idMal title { english romaji native } episodes } }"""
    for name in [title, *(aliases or [])]:
        if not name:
            continue
        try:
            d = _post_anilist(q, {"s": name})
        except Exception as exc:  # noqa: BLE001
            logger.info("[episodes] anilist search %r failed: %s", name, exc)
            continue
        m = (d.get("data") or {}).get("Media") or {}
        titles = [(m.get("title") or {}).get(k) or "" for k in ("english", "romaji", "native")]
        n = _norm(name)
        if n and any(n in _norm(t) or _norm(t) in n for t in titles if t):
            return m
        logger.info("[episodes] anilist returned %r for %r — rejected as a different show",
                    titles[0] or titles[1], name)
        time.sleep(0.4)
    return {}


def episodes_anilist(anilist_id: int) -> list[Episode]:
    q = """query ($id: Int) { Media(id: $id) {
             streamingEpisodes { title site } } }"""
    d = _post_anilist(q, {"id": anilist_id})
    eps = ((d.get("data") or {}).get("Media") or {}).get("streamingEpisodes") or []
    out: list[Episode] = []
    for e in eps:
        t = e.get("title") or ""
        m = re.search(r"episode\s*(\d{1,4})", t, re.I)
        out.append(Episode(int(m.group(1)) if m else None, t, source="anilist"))
    return out


def episodes_jikan(mal_id: int, *, max_pages: int = 6) -> list[Episode]:
    out: list[Episode] = []
    for page in range(1, max_pages + 1):
        url = f"{_JIKAN}/anime/{mal_id}/episodes?page={page}"
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.load(r)
        except Exception as exc:  # noqa: BLE001
            logger.info("[episodes] jikan page %d failed: %s", page, exc)
            break
        data = d.get("data") or []
        for e in data:
            out.append(Episode(e.get("mal_id"), e.get("title") or "",
                               synopsis="", source="jikan"))
        if not (d.get("pagination") or {}).get("has_next_page"):
            break
        time.sleep(1.0)      # Jikan rate limit
    return out


def _norm(s: str) -> str:
    return "".join(c for c in (s or "").lower() if c.isalnum() or c == " ").strip()


def match(fight_id: str, episodes: list[Episode], *, aka: list[str],
          combatants: list[str]) -> Resolution:
    """Find the episode whose TITLE names this fight.

    ``aka`` first and at high confidence: an episode known as "Hinokami" IS
    that episode, and the alias exists for exactly this. Both combatants in
    one title is medium — strong, but titles name characters for other
    reasons. One combatant is not a match at all; that is how "Yuji Has
    Become a War God" was once accepted for Yuta vs Yuji.
    """
    res = Resolution(fight_id=fight_id, n_episodes_seen=len(episodes))
    if not episodes:
        res.evidence = "no episode list available from either authority"
        return res

    for a in aka or []:
        na = _norm(a)
        if len(na) < 4:
            continue
        for e in episodes:
            if na and na in _norm(e.title):
                res.episode, res.matched_title = e.number, e.title
                res.source, res.confidence = e.source, "high"
                res.evidence = f"alias {a!r} in episode title"
                return res

    names = [_norm(c) for c in combatants if len(_norm(c)) >= 4]
    if len(names) >= 2:
        for e in episodes:
            t = _norm(e.title)
            if all(n in t for n in names):
                res.episode, res.matched_title = e.number, e.title
                res.source, res.confidence = e.source, "medium"
                res.evidence = "both combatants in the episode title"
                return res

    res.evidence = (f"{len(episodes)} episode titles searched; no alias and no "
                    f"two-combatant title match")
    return res


def resolve(fight: dict, *, show_id_cache: dict | None = None) -> Resolution:
    """Resolve one fight against AniList then Jikan."""
    cache = show_id_cache if show_id_cache is not None else {}
    show = fight["show"]
    if show not in cache:
        try:
            cache[show] = find_show(show, aliases=fight.get("show_aliases") or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("[episodes] show lookup failed for %s: %s", show, exc)
            cache[show] = {}
        time.sleep(0.6)
    meta = cache[show]

    eps: list[Episode] = []
    if meta.get("id"):
        try:
            eps = episodes_anilist(meta["id"])
        except Exception as exc:  # noqa: BLE001
            logger.info("[episodes] anilist episodes failed: %s", exc)
    if not eps and meta.get("idMal"):
        eps = episodes_jikan(meta["idMal"])

    return match(fight["id"], eps, aka=fight.get("aka") or [],
                 combatants=fight.get("combatants") or [])


def write_back(path: Path, resolutions: list[Resolution]) -> int:
    """Record the resolution beside each fight. Never silently overwrites the
    hand-entered number — it is kept as ``episode_unverified`` so the
    disagreement stays visible."""
    import yaml

    doc = yaml.safe_load(path.read_text())
    by_id = {r.fight_id: r for r in resolutions}
    n = 0
    for f in doc["fights"]:
        r = by_id.get(f["id"])
        if not r:
            continue
        f["episode_resolution"] = {
            "episode": r.episode, "source": r.source or None,
            "confidence": r.confidence or "none", "matched_title": r.matched_title or None,
            "evidence": r.evidence}
        if r.resolved and r.episode is not None:
            f.setdefault("episode_unverified", f.get("episode"))
            f["episode"] = r.episode
            n += 1
    path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True))
    return n
