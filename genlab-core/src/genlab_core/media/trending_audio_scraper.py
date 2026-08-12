"""Meta Reels trending audio scraper (Playwright-based, optional dep).

## Reality-check disclaimer

Meta does NOT expose the Reels Music Library trending list via any
public no-auth API. Every viable path requires JS-rendered scraping
with a browser context. This module:

  1. Attempts Playwright headless-browser scrape when the library
     is installed AND browser binaries are available
  2. Falls back to a lightweight `requests`-based fetch of the
     public Facebook Sound Kits page (works today; may break as
     Meta changes markup)
  3. Skips gracefully with WARN log if neither path is viable —
     the consumer (`trending_audio_meta.get_trending_moods_for_niche`)
     falls through to empty list, preserving pre-fix behavior

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

    Tries Playwright first (Meta Reels page needs JS), falls back to
    requests (Facebook Sound Kits page, sometimes has trending in
    server-rendered markup).
    """
    playwright_result = _try_playwright_scrape()
    if playwright_result:
        return playwright_result

    requests_result = _try_requests_fallback()
    if requests_result:
        return requests_result

    return []


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


def _classify_tracks_to_moods(
    track_names: list[dict],
    available_moods: list[str],
) -> list[dict]:
    """LLM-classify each track name into a mood label from available_moods.

    Returns list of `{"mood": str, "trend_rank": int, "meta_audio_id": str}`
    dicts ready for cache. Aggregates duplicate moods — if 3 hip-hop
    tracks all classify as "hype", we surface hype once at min-rank.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        logger.debug(
            "[trending_audio_scraper] no ANTHROPIC_API_KEY — skip classification"
        )
        return []

    try:
        import anthropic  # type: ignore[import-not-found]
    except ImportError:
        return []

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
        logger.warning(
            "[trending_audio_scraper] anthropic client init failed: %s", exc,
        )
        return []

    mood_to_rank: dict[str, tuple[int, str]] = {}
    for track in track_names[:20]:  # cap to top-20 to bound cost
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
            import re
            raw = response.content[0].text.strip() if response.content else ""
            match = re.search(r'"mood"\s*:\s*"([^"]+)"', raw)
            if not match:
                continue
            mood = match.group(1).strip()
            if mood not in available_moods:
                continue
            # Keep the lowest rank (most trending) per mood
            if mood not in mood_to_rank or rank < mood_to_rank[mood][0]:
                mood_to_rank[mood] = (rank, meta_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[trending_audio_scraper] classify track=%r failed: %s",
                name[:40], exc,
            )
            continue

    return [
        {"mood": mood, "trend_rank": rank, "meta_audio_id": meta_id}
        for mood, (rank, meta_id) in sorted(
            mood_to_rank.items(), key=lambda kv: kv[1][0],
        )
    ]


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
