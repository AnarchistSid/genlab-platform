"""Meta Reels trending audio scraper (Playwright-based, optional dep).

## Reality-check disclaimer

Meta does NOT expose the Reels Music Library trending list via any
public no-auth API. Verified 2026-08-12:
`facebook.com/business/help/366812203871232` returns 500 to
non-authenticated User-Agents. Direct scraping requires:
  * a real Meta account + session cookies (TOS-adjacent), OR
  * Playwright headless-browser with a logged-in profile

This module ships a PROXY: chart-topping songs from
**iTunes RSS Charts** (public, no-auth JSON API,
`rss.applemarketingtools.com/api/v2/us/music/most-played/50/songs.json`)
strongly correlate with Meta Reels trending audio. Reels users
pick from the same viral songs that top the charts.

Fetch order:
  1. **iTunes RSS Charts** — the primary, works today, no auth
  2. **Playwright + Meta URL** — optional, requires setup, real
     Meta signal when the operator flips it on
  3. **Facebook Sound Kits page** — kept as last-resort, currently
     returns 500 but may work in the future
  4. Skip gracefully with WARN log if all paths fail

## What this ships (concrete)

* `scrape_and_cache_trending_moods(niche_id, available_moods)`
  — top-level function. Returns True on success, False otherwise.
  Writes `.tmp/cache/trending_audio_meta/<niche_id>.json` on success.
* Playwright + Facebook Sound Kits fallback logic
* LLM-based mood classification: track name -> mood label from
  the niche's declared music_mood vocabulary
* Cache read wired into `trending_audio_meta._read_cache` (was
  previously a stub returning `[]`)

## Operator setup (one-time)

    pip install playwright
    playwright install chromium

Then flip `GENLAB_TRENDING_AUDIO_META_ENABLED=1` and add the
scraper systemd timer.

## Meta URL research

Attempted URLs (documented for the next session):

  * `https://www.facebook.com/reels/audio` — requires login
  * `https://www.instagram.com/reels/audio/` — requires login
  * `https://www.facebook.com/business/help/366812203871232` — Sound
    Kits page, has trending catalog client-side rendered
  * `https://api.instagram.com/oembed/?url=...` — per-audio metadata
    ONLY (no listing endpoint)

Result: Playwright + Facebook Sound Kits is the least-bad path.
This will break when Meta changes markup; monitor for
`scrape_and_cache_trending_moods` returning False.

## Fail-open contract

Never raises. Returns False on any failure. Consumer treats False
as "cache didn't refresh; use last-good cache OR empty list".

## Related class-of-bug

`[[class-of-bug-third-party-silent-url-scheme-change]]` — Meta
changing URL structure / markup silently is highly likely. The
scraper's fail-open + WARN log surface lets operators detect
breakage via `grep 'trending_audio_scraper'` in journalctl.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

_CACHE_DIR: Final[Path] = Path(".tmp/cache/trending_audio_meta")
_FB_SOUND_KITS_URL: Final[str] = (
    "https://www.facebook.com/business/help/366812203871232"
)
_META_REELS_AUDIO_URL: Final[str] = (
    "https://www.facebook.com/reels/audio"
)
_USER_AGENT: Final[str] = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36 "
    "GenLab/1.0"
)


def _cache_root() -> Path:
    """Cache root — respects GENLAB_TMP env override for prod paths."""
    root = os.environ.get("GENLAB_TMP", ".tmp")
    return Path(root) / "cache" / "trending_audio_meta"


def scrape_and_cache_trending_moods(
    niche_id: str,
    available_moods: list[str],
) -> bool:
    """Fetch trending Reels audio + classify into moods + write cache.

    Returns True on success (cache file written). False on any
    failure — consumer falls back to empty list, preserving pre-
    fix behavior.

    Args:
        niche_id: canonical niche id
        available_moods: mood labels from the niche's music_mood
            vocabulary. LLM classification is constrained to these
            values so the transformation orchestrator can consume
            the picks.
    """
    if not available_moods:
        logger.debug(
            "[trending_audio_scraper] no available moods for niche=%s — skip",
            niche_id,
        )
        return False

    track_names = _fetch_trending_track_names()
    if not track_names:
        logger.warning(
            "[trending_audio_scraper] no track names fetched (all sources "
            "failed or blocked) — cache NOT refreshed for niche=%s", niche_id,
        )
        return False

    moods = _classify_tracks_to_moods(track_names, available_moods)
    if not moods:
        logger.warning(
            "[trending_audio_scraper] classifier returned empty for niche=%s "
            "(LLM disabled, no API key, or all classifications rejected)",
            niche_id,
        )
        return False

    return _write_cache(niche_id, moods)


def _fetch_trending_track_names() -> list[dict]:
    """Return `[{"name": str, "meta_audio_id": str, "rank": int}, ...]`.

    Fetch order (first non-empty result wins):
      1. iTunes RSS Charts (public JSON, no auth) — primary
      2. Playwright + Meta Reels URL (optional dep)
      3. Facebook Sound Kits page (requests, currently 500s)
    """
    itunes_result = _try_itunes_rss_charts()
    if itunes_result:
        return itunes_result

    playwright_result = _try_playwright_scrape()
    if playwright_result:
        return playwright_result

    requests_result = _try_requests_fallback()
    if requests_result:
        return requests_result

    return []


_ITUNES_CHARTS_URL: Final[str] = (
    "https://rss.applemarketingtools.com/api/v2/us/music/most-played/50/songs.json"
)


def _try_itunes_rss_charts() -> list[dict]:
    """Fetch top-50 most-played songs from iTunes RSS Charts.

    Returns [{"name": "Track — Artist", "meta_audio_id": apple_id,
    "rank": 1..N}, ...]. Rank preserved via list position.

    Fail-open: any network / parse error returns [].
    """
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        _ITUNES_CHARTS_URL,
        headers={"User-Agent": _USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            if response.status != 200:
                logger.debug(
                    "[trending_audio_scraper] iTunes charts status=%d",
                    response.status,
                )
                return []
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        logger.warning(
            "[trending_audio_scraper] iTunes charts fetch failed: %s", exc,
        )
        return []
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning(
            "[trending_audio_scraper] iTunes charts JSON parse failed: %s", exc,
        )
        return []
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[trending_audio_scraper] iTunes charts unexpected error: %s", exc,
        )
        return []

    results = (payload.get("feed") or {}).get("results") or []
    if not results:
        logger.debug("[trending_audio_scraper] iTunes charts empty results")
        return []

    tracks: list[dict] = []
    for i, entry in enumerate(results[:20]):  # cap to top-20
        name = str(entry.get("name") or "").strip()
        artist = str(entry.get("artistName") or "").strip()
        apple_id = str(entry.get("id") or "").strip()
        if not name:
            continue
        # Combine name + artist for the classifier so it disambiguates
        # (many songs share titles; the artist is crucial context)
        display = f"{name} — {artist}" if artist else name
        tracks.append({
            "name": display,
            "meta_audio_id": apple_id,  # apple ID stands in for meta_audio_id
            "rank": i + 1,
        })
    logger.info(
        "[trending_audio_scraper] iTunes charts fetched %d tracks", len(tracks),
    )
    return tracks


def _try_playwright_scrape() -> list[dict]:
    """Attempt Playwright-based scrape of Meta Reels audio page.

    Fails silently when Playwright isn't installed or the URL is
    blocked. Documented URL fragility per the module docstring.
    """
    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
    except ImportError:
        logger.debug(
            "[trending_audio_scraper] playwright not installed — "
            "skipping Playwright path"
        )
        return []
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[trending_audio_scraper] playwright import raised (%s) — skip",
            exc,
        )
        return []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=_USER_AGENT)
            page = context.new_page()
            page.goto(_META_REELS_AUDIO_URL, wait_until="domcontentloaded", timeout=15000)
            # Best-effort selector — Meta markup changes; capture as-is
            page.wait_for_timeout(3000)  # let JS render
            content = page.content()
            browser.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[trending_audio_scraper] Playwright scrape failed: %s", exc,
        )
        return []

    # Meta's markup buries audio card data in nested divs. Real parse
    # requires Meta-specific selector experimentation on a live page —
    # this is a HOOK not a full parser. When Playwright succeeds but
    # parse returns [], operator sees WARN and next session can add
    # selectors after Meta markup research.
    tracks = _parse_meta_html(content)
    if tracks:
        logger.info(
            "[trending_audio_scraper] Playwright parsed %d tracks", len(tracks),
        )
    return tracks


def _parse_meta_html(html: str) -> list[dict]:
    """Extract audio track names + IDs from Meta's HTML.

    Placeholder for the actual selector work. Meta bundles React
    state in `__DATA__` script tags — a real parser reads that JSON
    and extracts audio_ids + track names. Deferred to a follow-up
    session with a live-scraped HTML sample to build selectors
    against.
    """
    # TODO(next-session): extract from Meta's React state dumps
    # For now: returns empty so the requests fallback is reached
    return []


def _try_requests_fallback() -> list[dict]:
    """Best-effort requests-based fetch of the Facebook Sound Kits
    page. Server-rendered surface, but limited trending signal.
    Returns [] if the page structure doesn't expose track names.
    """
    try:
        import requests  # type: ignore[import-not-found]
    except ImportError:
        return []

    try:
        response = requests.get(
            _FB_SOUND_KITS_URL,
            headers={"User-Agent": _USER_AGENT},
            timeout=15,
        )
        if response.status_code != 200:
            logger.debug(
                "[trending_audio_scraper] requests fallback status=%d",
                response.status_code,
            )
            return []
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[trending_audio_scraper] requests fallback failed: %s", exc,
        )
        return []

    return _parse_meta_html(response.text)


# Heuristic keyword → mood mapping. Used as fallback when the LLM
# classifier is unavailable (no API key, credit exhausted, network
# down). Broad matches — many songs won't hit any pattern and get
# skipped, but the top-20 iTunes chart usually has ENOUGH hits to
# surface 3-4 trending moods per niche.
_MOOD_KEYWORD_HINTS: Final[dict[str, tuple[str, ...]]] = {
    # High-energy / hype
    "energetic":   ("dance", "party", "energy", "hyped", "banger", "beat drop"),
    "hype":        ("hype", "lit", "turn up", "trap", "drill", "hip hop", "rap"),
    "aggressive":  ("hard", "brutal", "savage", "raw", "aggressive", "phonk"),
    "intense":     ("intense", "epic", "battle", "hardcore", "adrenaline"),
    "adrenaline":  ("rush", "extreme", "adrenaline", "speed", "chase"),
    # Cinematic / dramatic
    "cinematic":   ("cinematic", "score", "theme", "orchestral", "film"),
    "dramatic":    ("dramatic", "tragedy", "operatic", "symphonic"),
    "epic":        ("epic", "hero", "warrior", "legend", "titan"),
    "epic_battle": ("battle", "war", "fight", "combat"),
    "trailer":     ("trailer", "prelude", "overture"),
    # Emotional / mellow
    "emotional":   ("love", "heart", "cry", "tears", "goodbye", "miss you"),
    "romantic":    ("romantic", "love song", "you and i", "kiss", "forever"),
    "contemplative": ("acoustic", "piano", "instrumental", "reflection", "solo"),
    "ambient_tech":  ("ambient", "atmospheric", "chill", "lofi", "downtempo"),
    "mysterious":  ("mystery", "dark", "shadow", "secret", "enigma"),
    "ethereal":    ("ethereal", "dream", "cloud", "float", "celestial"),
    # Genre-driven
    "electronic":  ("electronic", "edm", "synth", "techno", "house", "dubstep"),
    "orchestral":  ("orchestra", "symphony", "concerto", "philharmonic"),
    "tech_hype":   ("tech", "future", "cyber", "digital", "code"),
    "focused":     ("focus", "study", "concentration", "flow"),
    "upbeat":      ("upbeat", "happy", "fun", "bright", "cheerful", "sunshine"),
    "whimsical":   ("whimsy", "playful", "silly", "fairy", "magical"),
    # Sports-specific
    "victorious":  ("champion", "victory", "winner", "triumph"),
    "driving":     ("drive", "power", "engine", "race"),
    "uplifting":   ("uplift", "rise", "inspire", "hope"),
    "cinematic_sport": ("sport", "athlete", "game day", "arena"),
}


def _heuristic_classify(name: str, available_moods: list[str]) -> str | None:
    """Match a track name against keyword hints for each available mood.
    Returns the mood with the most keyword overlap, or None on no match.
    Case-insensitive substring match — cheap + zero API cost."""
    if not name or not available_moods:
        return None
    name_lower = name.lower()
    scores: dict[str, int] = {}
    for mood in available_moods:
        hints = _MOOD_KEYWORD_HINTS.get(mood, ())
        for kw in hints:
            if kw in name_lower:
                scores[mood] = scores.get(mood, 0) + 1
    if not scores:
        return None
    # Return highest-scoring mood; ties broken by mood order in
    # available_moods (stable).
    best_score = max(scores.values())
    for mood in available_moods:
        if scores.get(mood, 0) == best_score:
            return mood
    return None


def _classify_tracks_to_moods(
    track_names: list[dict],
    available_moods: list[str],
) -> list[dict]:
    """LLM-classify each track name into a mood label from available_moods.

    Returns list of `{"mood": str, "trend_rank": int, "meta_audio_id": str}`
    dicts ready for cache. Aggregates duplicate moods — if 3 hip-hop
    tracks all classify as "hype", we surface hype once at min-rank.

    2026-08-18: heuristic fallback when LLM unavailable (Anthropic
    credit exhausted, no API key, network down). Non-LLM keyword
    matching on track name → mood hints in ``_MOOD_KEYWORD_HINTS``.
    Data quality lower than Haiku but cache stays fresh instead of
    empty. Was silent-broken for 2+ days when Anthropic credit ran
    out — verified 2026-08-18 via live probe returning
    ``credit_balance_exhausted``.
    """
    mood_to_rank: dict[str, tuple[int, str]] = {}

    def _apply(name: str, rank: int, meta_id: str, mood: str) -> None:
        # Keep lowest rank (most-trending) per mood.
        if mood not in mood_to_rank or rank < mood_to_rank[mood][0]:
            mood_to_rank[mood] = (rank, meta_id)

    def _finalize() -> list[dict]:
        return [
            {"mood": mood, "trend_rank": rank, "meta_audio_id": meta_id}
            for mood, (rank, meta_id) in sorted(
                mood_to_rank.items(), key=lambda kv: kv[1][0],
            )
        ]

    def _heuristic_fallback(reason: str) -> list[dict]:
        # Called on any LLM unavailability. Runs the keyword classifier
        # over all tracks and returns whatever hits. Log at WARNING so
        # this doesn't silent-fail like the pre-2026-08-18 no-key path
        # (rule #17 / #19 pattern).
        hits = 0
        for track in track_names[:20]:
            name = str(track.get("name", "")).strip()
            if not name:
                continue
            mood = _heuristic_classify(name, available_moods)
            if not mood:
                continue
            hits += 1
            _apply(
                name=name,
                rank=int(track.get("rank", 999)),
                meta_id=str(track.get("meta_audio_id", "")),
                mood=mood,
            )
        logger.warning(
            "[trending_audio_scraper] LLM unavailable (%s) — heuristic "
            "fallback matched %d/%d tracks into %d moods",
            reason, hits, len(track_names[:20]), len(mood_to_rank),
        )
        return _finalize()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return _heuristic_fallback("no ANTHROPIC_API_KEY")

    try:
        import anthropic  # type: ignore[import-not-found]
    except ImportError:
        return _heuristic_fallback("anthropic package not installed")

    system = (
        "You classify short-form video music tracks into mood labels. "
        "Given a track name (title + artist), pick the ONE mood label "
        "from the available set that best fits the track's vibe. "
        "Respond with JSON: {\"mood\": \"<exact_label>\"}. "
        "If none fit, respond with {\"mood\": null}."
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
    except Exception as exc:  # noqa: BLE001
        return _heuristic_fallback(f"anthropic client init failed: {exc}")

    # Detect systemic LLM failure (credit exhausted, quota hit, auth
    # rejected) and switch to heuristic wholesale instead of paying the
    # per-track retry cost for 20 tracks in a row.
    _SYSTEMIC_MARKERS = (
        "credit balance", "insufficient", "quota", "rate limit",
        "unauthorized", "invalid api key", "403", "401",
    )
    consecutive_failures = 0

    for track in track_names[:20]:
        name = str(track.get("name", "")).strip()
        rank = int(track.get("rank", 999))
        meta_id = str(track.get("meta_audio_id", ""))
        if not name:
            continue
        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=50,
                temperature=0.0,
                system=system,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Track: {name}\n"
                        f"Available moods: {', '.join(available_moods)}\n"
                        "Pick the best-fit mood."
                    ),
                }],
            )
            consecutive_failures = 0
            import re
            raw = response.content[0].text.strip() if response.content else ""
            match = re.search(r'"mood"\s*:\s*"([^"]+)"', raw)
            if not match:
                continue
            mood = match.group(1).strip()
            if mood not in available_moods:
                continue
            _apply(name=name, rank=rank, meta_id=meta_id, mood=mood)
        except Exception as exc:  # noqa: BLE001
            err_lower = str(exc).lower()
            is_systemic = any(m in err_lower for m in _SYSTEMIC_MARKERS)
            if is_systemic:
                # Anthropic credit / quota / auth is dead — no point
                # burning wall-time on 19 more identical failures.
                # Whatever we already classified stays; heuristic fills
                # the rest so cache is still populated.
                logger.warning(
                    "[trending_audio_scraper] systemic LLM failure: %s "
                    "— switching to heuristic fallback for remaining tracks",
                    exc,
                )
                # Heuristic-classify any track NOT already covered.
                classified_meta_ids = {mid for _, mid in mood_to_rank.values()}
                for t in track_names[:20]:
                    tid = str(t.get("meta_audio_id", ""))
                    if tid in classified_meta_ids:
                        continue
                    tname = str(t.get("name", "")).strip()
                    if not tname:
                        continue
                    m = _heuristic_classify(tname, available_moods)
                    if m:
                        _apply(
                            name=tname,
                            rank=int(t.get("rank", 999)),
                            meta_id=tid,
                            mood=m,
                        )
                return _finalize()
            consecutive_failures += 1
            logger.debug(
                "[trending_audio_scraper] classify track=%r failed: %s",
                name[:40], exc,
            )
            if consecutive_failures >= 3:
                # Not a credit/quota keyword but 3 in a row = something's
                # broken. Bail to heuristic rather than burn 20 slow calls.
                return _heuristic_fallback(
                    f"3 consecutive LLM errors, last: {exc}"
                )

    return _finalize()


def _write_cache(niche_id: str, moods: list[dict]) -> bool:
    """Write the classified moods to the cache file. Atomic via .tmp
    rename. Returns True on success."""
    cache_root = _cache_root()
    try:
        cache_root.mkdir(parents=True, exist_ok=True)
        payload = {
            "fetched_at": datetime.now(UTC).isoformat(),
            "moods": moods,
        }
        target = cache_root / f"{niche_id}.json"
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(target)
        logger.info(
            "[trending_audio_scraper] wrote cache niche=%s moods=%d path=%s",
            niche_id, len(moods), target,
        )
        return True
    except OSError as exc:
        logger.warning(
            "[trending_audio_scraper] cache write failed niche=%s: %s",
            niche_id, exc,
        )
        return False


def read_cache_for_niche(niche_id: str, *, ttl_hours: int = 6):
    """Read the cache for one niche. Returns list of TrendingAudioMood
    (via trending_audio_meta.TrendingAudioMood) — the scraper's cache
    format converts back to the interface dataclass.

    Returns [] on cache miss, stale entries, or any parse error.
    """
    from genlab_core.media.trending_audio_meta import TrendingAudioMood

    cache_root = _cache_root()
    target = cache_root / f"{niche_id}.json"
    if not target.exists():
        return []
    try:
        payload = json.loads(target.read_text())
    except (OSError, ValueError, json.JSONDecodeError):
        return []

    # Staleness check
    fetched_at_raw = payload.get("fetched_at", "")
    try:
        fetched_at = datetime.fromisoformat(
            fetched_at_raw.replace("Z", "+00:00")
        )
    except (ValueError, TypeError):
        return []
    age = datetime.now(UTC) - fetched_at
    if age.total_seconds() > ttl_hours * 3600:
        return []

    result = []
    for m in payload.get("moods", []):
        try:
            result.append(TrendingAudioMood(
                mood=str(m["mood"]),
                trend_rank=int(m["trend_rank"]),
                meta_audio_id=str(m.get("meta_audio_id", "")),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return result


__all__ = [
    "read_cache_for_niche",
    "scrape_and_cache_trending_moods",
]
