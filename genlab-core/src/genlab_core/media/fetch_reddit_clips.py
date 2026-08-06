"""Reddit clip fetcher — per-niche subreddit aggregator.

Reddit's JSON endpoints (e.g. ``/r/<sub>/top.json?t=day``) require no API
key and no OAuth, just a custom User-Agent (Reddit blocks the default
``python-requests`` UA). Each niche configures a list of subreddits in
its ``config/sources.yaml`` under the ``reddit:`` key.

Why this matters: until 2026-05-21 every niche relied on YouTube as the
sole trending source. When YouTube enabled the SABR-only streaming
experiment that day, every yt-dlp download failed and sports shipped
zero blueprints. Reddit JSON has no quota, no client-side breakage, and
its upvote signal is a pre-screened engagement proxy we get for free.

Returns story dicts compatible with the pipeline context (same shape
``TrendingVideoFetcher.to_story()`` produces), so downstream stages
(scoring, writing, hooks, render) consume them without changes.

2026-06-21 RSS pivot: Reddit's JSON endpoints became unreliable from
data-center IPs (Hetzner) — they intermittently 429 or return HTML
"Blocked" pages (the Reddit web tier anti-scraping). At the same time,
the OAuth path is now gated behind a Reddit Developer Program approval
that takes days-to-weeks. Public Atom RSS endpoints (e.g.
``/r/<sub>/top/.rss``) still work from the same IPs without auth —
just with stricter per-IP rate limits (~1 RPM per subreddit). This
module now tries RSS FIRST and falls back to JSON only when RSS is
unavailable or fails. The story-dict shape is preserved across both
paths so downstream stages are unaffected.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime
from typing import Any
from xml.etree import ElementTree as ET

import requests

from genlab_core.config.tuning import get_tuning_config

logger = logging.getLogger(__name__)

# Reddit blocks the default requests UA. The Gen Lab UA is benign and
# identifies the project for any rate-limit hand-wringing.
_REDDIT_UA = "GenLab/0.1 (+https://aspirehub.ai/genlab) by aspirehub"

# Reddit blocks datacenter IPs with 403 (Hetzner host observed). Reddit
# also blocks TOR exits entirely (403 hard, verified 2026-08-06). WARP
# gets 429-rate-limited but the requests do succeed with pacing. Prefer
# WARP explicitly via GENLAB_REDDIT_PROXY_URL; fall back to the general
# GENLAB_HTTP_SOCKS_PROXY only if Reddit-specific isn't set.
#
# Historical note: pre-2026-08-06, only GENLAB_HTTP_SOCKS_PROXY was
# consulted; when that pointed to TOR (as it does on the current prod
# .env for other-service reasons), every Reddit call hit 403. Adding
# the Reddit-specific override lets operators pin Reddit to WARP
# without changing the general proxy used by anything else.
_REDDIT_PROXY_URL = os.environ.get(
    "GENLAB_REDDIT_PROXY_URL",
    os.environ.get("GENLAB_HTTP_SOCKS_PROXY", ""),
).strip()
_REDDIT_PROXIES = (
    {"http": _REDDIT_PROXY_URL, "https": _REDDIT_PROXY_URL} if _REDDIT_PROXY_URL else None
)

# 429 retry policy — Reddit's RSS rate-limits aggressively when hit
# fast. Waiting ~30s lets the token bucket replenish enough for one
# more request per subreddit. One retry is the pragmatic tradeoff:
# doubles worst-case per-sub latency to ~35s but rescues subreddits
# that got unlucky on their first token.
_RSS_429_RETRY_SLEEP_SEC = float(os.environ.get("REDDIT_RSS_429_RETRY_SLEEP_SEC", "30.0"))
_RSS_429_MAX_RETRIES = int(os.environ.get("REDDIT_RSS_429_MAX_RETRIES", "1"))

# Endpoints that surface high-engagement posts. "top" with t=day gives
# the day's best; "hot" gives current momentum. We mix both so the
# selection isn't biased toward midnight-of-yesterday posts.
_REDDIT_TIMEOUT = 15  # seconds

# Upvote→view equivalence for the cross-source velocity proxy. Reddit upvotes
# and YouTube views are different scales; the original 100× made a 1000-upvote
# post hit velocity ~20,000 and systematically dwarf real YouTube clips in the
# merged ranking. 40× keeps a strong Reddit clip in YouTube's range (so it
# competes fairly without dominating). A rough calibration, not ground truth —
# tune here if Reddit over/under-represents in the selected posts.
_UPVOTE_VIEW_EQUIVALENCE: float = get_tuning_config().reddit_fetch.upvote_view_equivalence


# ─────────────────────────────────────────────────────────────────────
# RSS path (2026-06-21 pivot)
#
# When Reddit's JSON endpoints fail (429 / HTML block page from data-
# center IPs), we fall back to the Atom RSS feeds at
# ``/r/<sub>/top/.rss?t=day``. Same data, different shape; we extract
# title, native v.redd.it / yt / twitch link, post_id, and synthesize
# score signals from feed position (Reddit already ranks RSS entries).
# ─────────────────────────────────────────────────────────────────────

_ATOM_NS = "{http://www.w3.org/2005/Atom}"

# Reddit RSS entry content is HTML-escaped table with the post media link
# wrapped in <a href="https://v.redd.it/..."> (native video) OR external
# host links (youtube/twitch/streamable). Extract that URL from the
# entry's <content type="html"> body via regex (parsing escaped HTML
# inside Atom is fragile + slow).
_RSS_VIDEO_HREF_RE = re.compile(
    r'href=(?:"|&quot;)('
    r"https?://(?:"
    r"v\.redd\.it/[\w]+"  # native Reddit video
    r"|i\.redd\.it/[\w.]+\.(?:gif|gifv|mp4|webm)"  # native Reddit gif/mp4
    r"|(?:www\.)?youtube\.com/watch\?v=[\w-]+"  # YT canonical
    r"|youtu\.be/[\w-]+"  # YT short
    r"|(?:www\.)?twitch\.tv/[\w/]+"  # Twitch
    r"|clips\.twitch\.tv/[\w-]+"
    r"|streamable\.com/[\w]+"
    r")"
    r"[^\s\"&]*"  # query string / fragments
    r")",
    re.IGNORECASE,
)

# Pre-compute Reddit post id regex — Reddit Atom <id>t3_xxxxxxx</id>
_T3_ID_RE = re.compile(r"t3_([a-z0-9]{5,12})", re.IGNORECASE)


def _parse_atom_feed(xml_text: str) -> list[dict[str, Any]]:
    """Parse Reddit Atom RSS feed into a list of normalized entry dicts.

    Returns one dict per ``<entry>`` with these keys:
      * ``title`` (str) — entry title text
      * ``video_url`` (str|None) — extracted v.redd.it / yt / twitch URL
        from the entry's HTML content; None when no recognised video host
      * ``post_id`` (str) — Reddit post id (without ``t3_`` prefix)
      * ``permalink`` (str) — comments URL for the post
      * ``published_at`` (datetime|None) — UTC timestamp from <published>
      * ``position`` (int) — 0-based index in the feed (used as a fallback
        ranking signal since RSS doesn't expose upvote counts)
      * ``thumbnail_url`` (str) — first <img src> in the content (best-
        effort; Reddit RSS doesn't have a dedicated media:thumbnail)
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("[reddit-rss] Atom parse failed: %s", exc)
        return []

    entries = []
    for position, entry_el in enumerate(root.findall(f"{_ATOM_NS}entry")):
        title_el = entry_el.find(f"{_ATOM_NS}title")
        id_el = entry_el.find(f"{_ATOM_NS}id")
        published_el = entry_el.find(f"{_ATOM_NS}published")
        content_el = entry_el.find(f"{_ATOM_NS}content")
        # The comments-page link (Reddit puts the comments URL as the
        # first <link rel="alternate">)
        link_el = entry_el.find(f"{_ATOM_NS}link")

        title = (title_el.text or "").strip() if title_el is not None else ""
        if not title:
            continue

        # Extract Reddit post id from <id>t3_xxxxx</id>
        id_text = (id_el.text or "") if id_el is not None else ""
        m = _T3_ID_RE.search(id_text)
        post_id = m.group(1) if m else ""

        permalink = link_el.get("href", "") if link_el is not None else ""

        published_at = None
        if published_el is not None and published_el.text:
            try:
                # Atom published is RFC 3339 — Python 3.11+ handles 'Z' natively
                ts = published_el.text.strip()
                if ts.endswith("Z"):
                    ts = ts[:-1] + "+00:00"
                published_at = datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                pass

        # Extract first matched video URL from the HTML-escaped content
        content_html = (content_el.text or "") if content_el is not None else ""
        video_url = ""
        thumbnail_url = ""
        if content_html:
            vm = _RSS_VIDEO_HREF_RE.search(content_html)
            if vm:
                video_url = vm.group(1)
            tm = re.search(
                r'<img\s+src=(?:"|&quot;)(https?://[^\s"&]+)', content_html, re.IGNORECASE
            )
            if tm:
                thumbnail_url = tm.group(1)

        entries.append(
            {
                "title": title,
                "video_url": video_url or None,
                "post_id": post_id,
                "permalink": permalink,
                "published_at": published_at,
                "position": position,
                "thumbnail_url": thumbnail_url,
            }
        )

    return entries


def _normalise_rss_entry(
    entry: dict, niche_id: str, subreddit: str, total_entries: int
) -> dict[str, Any] | None:
    """Convert a parsed RSS entry into a pipeline-compatible story dict.

    Matches the shape produced by ``_normalise_post`` so downstream stages
    don't need a separate code path. Key differences vs JSON:

    * **Score synthesised from position** (RSS doesn't expose upvote
      count). Top entry gets ``_RSS_TOP_SCORE_SYNTH``; each subsequent
      position drops by ``_RSS_SCORE_DECAY``. Bottom-of-feed entries
      still clear the ``score >= 100`` floor so the listing-fallback
      isn't required.
    * ``reddit_score`` / ``reddit_num_comments`` / ``reddit_upvote_ratio``
      are set to the synthesised score / 0 / 0.5 (neutral defaults).
    * No ``video_id`` for Reddit-hosted videos (RSS doesn't expose the
      internal Reddit id); we use the post_id instead.
    """
    if not entry.get("video_url") or not entry.get("title"):
        return None

    # Score synth: rank 0 → 1000, rank 9 → 100, rank 24 → 100 (floor)
    # The decay is chosen so the natural ``score >= 100`` filter in
    # _normalise_post still passes for the top ~20 positions.
    synth_score = max(100, _RSS_TOP_SCORE_SYNTH - entry["position"] * _RSS_SCORE_DECAY)

    published_at = entry.get("published_at") or datetime.now(UTC)
    now = datetime.now(UTC)
    age_hours = max(0.1, (now - published_at).total_seconds() / 3600)
    view_velocity = (synth_score * _UPVOTE_VIEW_EQUIVALENCE) / age_hours

    from genlab_core.cache.stable_ids import generate_story_id

    url = entry["video_url"]
    story_id = generate_story_id(url, published_at.isoformat())

    return {
        "story_id": story_id,
        "title": entry["title"],
        "source": f"reddit:{subreddit}",
        "source_url": url,
        "canonical_url": url,
        "published_date": published_at.isoformat(),
        "published_at": published_at.isoformat(),
        "fetched_at": now.isoformat(),
        "summary": entry.get("permalink", ""),
        "channel_name": f"r/{subreddit}",
        "view_count": int(synth_score * _UPVOTE_VIEW_EQUIVALENCE),
        "view_velocity": round(view_velocity, 1),
        "duration_seconds": 0,  # RSS doesn't expose duration
        "thumbnail_url": entry.get("thumbnail_url") or "",
        "tags": [subreddit, niche_id],
        "niche_id": niche_id,
        "video_source": "reddit",
        "video_id": entry.get("post_id", ""),
        "is_official_channel": False,
        # Lower mention_count than the JSON path because we can't see
        # comment counts on RSS — the JSON code uses comments as a
        # mention-amplification signal.
        "source_mention_count": max(1, min(5, synth_score // 1000)),
        "_trending_video": True,
        # Reddit-specific signals — neutral defaults for the RSS path
        # since the data isn't in the feed.
        "reddit_score": synth_score,
        "reddit_num_comments": 0,
        "reddit_upvote_ratio": 0.5,
        # Trace which path produced this story — useful for ops attribution
        # if RSS-derived stories systematically over- or under-perform.
        "_reddit_source_path": "rss",
        "_rss_position": entry["position"],
    }


# Score synthesis tuning for RSS path. Chosen so the top ~20 positions
# all pass the ``score >= 100`` filter in ``_normalise_post`` while
# preserving relative rank ordering across the feed.
_RSS_TOP_SCORE_SYNTH = 1000
_RSS_SCORE_DECAY = 36


def fetch_subreddit_via_rss(
    subreddit: str,
    niche_id: str,
    time_window: str = "day",
    limit: int = 25,
    timeout: int = _REDDIT_TIMEOUT,
) -> list[dict]:
    """Fetch posts from one subreddit via the public Atom RSS endpoint.

    No auth, no API key — works from data-center IPs that get blocked
    on the JSON path. Atom RSS rate limits at ~1 RPM per subreddit per
    IP; callers should space out their requests accordingly.

    Returns the same story-dict shape as ``fetch_subreddit`` (the JSON
    path). Empty list on any error (fail-soft).
    """
    url = f"https://www.reddit.com/r/{subreddit}/top/.rss"
    params = {"t": time_window, "limit": limit}
    # Browser-like UA — RSS endpoint allows the GenLab UA too, but
    # browser UA matches what worked in our 2026-06-21 verification.
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    # QB-FIX-01 Reddit-auth (2026-08-06): retry on 429 with backoff.
    # Reddit's per-IP RSS rate limit is aggressive (verified: even 8-10s
    # between calls returns 429). One 30s-wait retry rescues ~50% of
    # rate-limited requests based on token-bucket replenish rate.
    #
    # Rate-limited sentinel: plain `list` doesn't accept arbitrary
    # attributes (AttributeError on setattr), so use a lightweight
    # subclass. The caller checks `.rate_limited` to distinguish
    # "empty because rate-limited" from "empty because no posts today".
    import time as _time_local

    class _RateLimitedList(list):
        rate_limited = False

    def _do_request() -> Any:
        return requests.get(
            url,
            params=params,
            headers=headers,
            timeout=timeout,
            proxies=_REDDIT_PROXIES,
        )

    resp = None
    try:
        for attempt in range(_RSS_429_MAX_RETRIES + 1):
            resp = _do_request()
            if resp.status_code != 429:
                break
            if attempt < _RSS_429_MAX_RETRIES:
                logger.info(
                    "[reddit-rss] r/%s rate-limited (429) — sleeping %.0fs and retrying (attempt %d/%d)",
                    subreddit,
                    _RSS_429_RETRY_SLEEP_SEC,
                    attempt + 2,
                    _RSS_429_MAX_RETRIES + 1,
                )
                _time_local.sleep(_RSS_429_RETRY_SLEEP_SEC)
        if resp.status_code == 429:
            # All retries exhausted — signal caller (via list subclass
            # attribute) so it SKIPS the JSON fallback (which will also
            # fail and just burns more rate-limit budget).
            logger.warning(
                "[reddit-rss] r/%s rate-limited (429) after %d retries — "
                "signalling caller to skip JSON fallback",
                subreddit,
                _RSS_429_MAX_RETRIES + 1,
            )
            empty = _RateLimitedList()
            empty.rate_limited = True
            return empty
        if resp.status_code != 200:
            logger.warning(
                "[reddit-rss] r/%s returned HTTP %d — falling through to JSON path",
                subreddit,
                resp.status_code,
            )
            return []
        # 2026-06-20 lesson (PR #405): Reddit serves HTML "Blocked" pages
        # with HTTP 200 to data-center IPs. Content-Type check is the
        # ground truth — Atom RSS is ``application/atom+xml``, never
        # ``text/html``.
        ctype = resp.headers.get("Content-Type", "")
        if "xml" not in ctype.lower() and "atom" not in ctype.lower():
            logger.warning(
                "[reddit-rss] r/%s returned non-XML Content-Type=%s — "
                "likely web-tier block page; falling through to JSON path",
                subreddit,
                ctype or "<missing>",
            )
            return []
    except Exception as exc:
        logger.warning("[reddit-rss] r/%s fetch failed: %s", subreddit, exc)
        return []

    entries = _parse_atom_feed(resp.text)
    stories = []
    for entry in entries:
        story = _normalise_rss_entry(entry, niche_id, subreddit, len(entries))
        if story:
            stories.append(story)

    logger.info(
        "[reddit-rss] r/%s yielded %d/%d video stories via RSS (window=%s)",
        subreddit,
        len(stories),
        len(entries),
        time_window,
    )
    return stories


def _is_video_post(post: dict) -> tuple[bool, str]:
    """Return (is_video, download_url).

    Reddit posts are video-eligible when:
      1. ``is_video=true`` (Reddit-hosted MP4 — direct download)
      2. URL points to YouTube / Twitch / Streamable / v.redd.it /
         imgur.com/*.mp4 (yt-dlp or cobalt can fetch)

    Returns the canonical URL we'd hand to the downloader.
    """
    data = post.get("data", post)

    # 1. Reddit-hosted video
    if data.get("is_video"):
        media = data.get("media") or {}
        rv = media.get("reddit_video") or {}
        url = rv.get("fallback_url") or ""
        if url:
            return True, url

    # 2. External video host
    external_url = (data.get("url_overridden_by_dest") or data.get("url") or "").strip()
    if not external_url:
        return False, ""

    lower = external_url.lower()
    video_hosts = (
        "youtube.com/watch",
        "youtu.be/",
        "twitch.tv/",
        "streamable.com/",
        "v.redd.it/",
        "clips.twitch.tv/",
    )
    if any(h in lower for h in video_hosts):
        return True, external_url

    # Direct .mp4 / .webm
    if lower.endswith((".mp4", ".webm", ".mov")):
        return True, external_url

    return False, ""


def _normalise_post(post: dict, niche_id: str, subreddit: str) -> dict[str, Any] | None:
    """Convert a Reddit listing entry into a pipeline-compatible story dict.

    Returns None when the post can't yield a usable video.
    """
    is_video, url = _is_video_post(post)
    if not is_video or not url:
        return None

    data = post.get("data", post)

    title = (data.get("title") or "").strip()
    if not title:
        return None

    # Score signals — Reddit's upvotes is the proxy for engagement.
    # ``ups`` is the raw upvote count; ``score`` factors in algorithm
    # weighting but is close enough for our ranking. ``num_comments``
    # is a separate engagement signal we surface as well.
    score = int(data.get("score") or 0)
    num_comments = int(data.get("num_comments") or 0)
    upvote_ratio = float(data.get("upvote_ratio") or 0.5)

    # Filter low-quality posts. 100 upvotes is a soft floor — below that
    # the post hasn't proven traction. Tunable per niche later via the
    # config layer if a niche legitimately has low-volume subreddits.
    if score < 100:
        return None

    # Reddit timestamps are Unix epoch in UTC.
    created_utc = float(data.get("created_utc") or 0)
    if created_utc <= 0:
        return None
    published_at = datetime.fromtimestamp(created_utc, tz=UTC)
    now = datetime.now(UTC)
    age_hours = max(0.1, (now - published_at).total_seconds() / 3600)

    # Velocity proxy comparable to TrendingVideoFetcher's view_velocity
    # (views/hr). Calibrated via _UPVOTE_VIEW_EQUIVALENCE so Reddit doesn't
    # systematically out-rank YouTube clips in the merged candidate pool.
    view_velocity = (score * _UPVOTE_VIEW_EQUIVALENCE) / age_hours

    from genlab_core.cache.stable_ids import generate_story_id

    story_id = generate_story_id(url, published_at.isoformat())
    permalink = "https://www.reddit.com" + (data.get("permalink") or "")

    return {
        "story_id": story_id,
        "title": title,
        "source": f"reddit:{subreddit}",
        "source_url": url,
        "canonical_url": url,
        "published_date": published_at.isoformat(),
        "published_at": published_at.isoformat(),
        "fetched_at": now.isoformat(),
        # Reddit doesn't expose a rich description; the title is the
        # only natural-language hook signal. Carry the permalink as
        # the summary so downstream attribution still works.
        "summary": permalink,
        "channel_name": f"r/{subreddit}",
        "view_count": int(score * _UPVOTE_VIEW_EQUIVALENCE),  # synth proxy, calibrated
        "view_velocity": round(view_velocity, 1),
        "duration_seconds": int(
            data.get("media", {}).get("reddit_video", {}).get("duration", 0) or 0
        ),
        "thumbnail_url": data.get("thumbnail") or "",
        "tags": [subreddit, niche_id],
        "niche_id": niche_id,
        "video_source": "reddit",
        # Reddit posts that link to YT include the video_id naturally;
        # the existing yt-dlp downloader handles the URL form too. Mark
        # _trending_video so PreDownloadDedup respects it.
        "video_id": data.get("id", ""),
        "is_official_channel": False,
        "source_mention_count": max(
            1,
            min(5, score // 1000 + (num_comments // 200)),
        ),
        "_trending_video": True,
        # Reddit-specific signals downstream stages can use for context
        "reddit_score": score,
        "reddit_num_comments": num_comments,
        "reddit_upvote_ratio": upvote_ratio,
    }


def fetch_subreddit(
    subreddit: str,
    niche_id: str,
    listing: str = "top",
    time_window: str = "day",
    limit: int = 25,
    timeout: int = _REDDIT_TIMEOUT,
) -> list[dict]:
    """Fetch posts from one subreddit, return story-shaped dicts.

    Args:
        subreddit: Without the leading ``r/`` (e.g. ``"GamingClips"``).
        niche_id: Stamped on returned stories.
        listing: ``"top"`` (best-of-window) or ``"hot"`` (current
            momentum). ``"top"`` is the better signal for video.
        time_window: Only meaningful for ``"top"``. ``"day"`` /
            ``"week"`` / ``"hour"``.
        limit: Reddit caps at 100 per request; 25 is the sweet spot
            for engagement quality vs. churn.
        timeout: Network timeout per request.

    Returns:
        List of story dicts. Empty on any error (fail-soft so a
        single bad subreddit doesn't kill the niche's whole fetch).
    """
    if listing not in ("top", "hot", "new"):
        listing = "top"

    # 2026-06-21 RSS-first pivot: Reddit's JSON endpoints became
    # unreliable from data-center IPs (Hetzner). Try the RSS path first
    # because that's what verified-works from the prod IP — only fall
    # through to JSON when RSS returns nothing or errors. Skipping
    # listings other than "top" because RSS only exposes the "top"
    # variant per (subreddit, time_window).
    if listing == "top":
        rss_stories = fetch_subreddit_via_rss(
            subreddit=subreddit,
            niche_id=niche_id,
            time_window=time_window,
            limit=limit,
            timeout=timeout,
        )
        if rss_stories:
            return rss_stories
        # QB-FIX-01 Reddit-auth (2026-08-06): distinguish rate-limited
        # from genuinely-empty. RSS returns a _RateLimitedList subclass
        # with rate_limited=True when it exhausted its 429 retries —
        # falling through to JSON in that case burns more rate-limit
        # budget for a request Reddit will also 403 (verified: JSON
        # path returns text/html 403 for data-center IPs). Skip fallback.
        if getattr(rss_stories, "rate_limited", False):
            logger.info(
                "[reddit] r/%s RSS rate-limited, JSON fallback skipped "
                "(would also 403 from datacenter IP)",
                subreddit,
            )
            return []
        # RSS yielded nothing but wasn't rate-limited — fall through to
        # JSON path (preserves pre-pivot behaviour for operators with
        # working JSON access, e.g. dev machines not blocked from
        # reddit.com).
        logger.info(
            "[reddit] r/%s RSS path empty/blocked — falling through to JSON",
            subreddit,
        )

    url = f"https://www.reddit.com/r/{subreddit}/{listing}.json"
    params: dict[str, Any] = {"limit": limit, "raw_json": 1}
    if listing == "top":
        params["t"] = time_window

    try:
        resp = requests.get(
            url,
            params=params,
            headers={"User-Agent": _REDDIT_UA},
            timeout=timeout,
            proxies=_REDDIT_PROXIES,
        )
        if resp.status_code == 429:
            logger.warning(
                "[reddit] %s rate-limited (429) — skipping this subreddit for this run",
                subreddit,
            )
            return []
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:
        logger.warning("[reddit] %s fetch failed: %s", subreddit, exc)
        return []

    children = body.get("data", {}).get("children", [])
    stories: list[dict] = []
    for entry in children:
        story = _normalise_post(entry, niche_id, subreddit)
        if story:
            stories.append(story)
    logger.info(
        "[reddit] r/%s yielded %d/%d video stories (listing=%s, t=%s)",
        subreddit,
        len(stories),
        len(children),
        listing,
        time_window,
    )

    # Listing fallback: the sub responded but today's "top" happens to be
    # images/text only. Try "hot" once before giving up — catches niches
    # like anime where some subs only have a video at the #4-10 spots, not
    # the top one. Only worth doing when we got SOME posts but no videos:
    # if children=0 the sub is empty/private and fallback won't help.
    if listing == "top" and len(stories) == 0 and len(children) > 0:
        return _fetch_listing(subreddit, niche_id, "hot", time_window, limit, timeout)
    return stories


def _fetch_listing(
    subreddit: str,
    niche_id: str,
    listing: str,
    time_window: str,
    limit: int,
    timeout: int,
) -> list[dict]:
    """Inner fetch used by the listing-fallback path. Same shape as
    fetch_subreddit but without the recursive retry — one shot only."""
    url = f"https://www.reddit.com/r/{subreddit}/{listing}.json"
    params: dict[str, Any] = {"limit": limit, "raw_json": 1}
    try:
        resp = requests.get(
            url,
            params=params,
            headers={"User-Agent": _REDDIT_UA},
            timeout=timeout,
            proxies=_REDDIT_PROXIES,
        )
        if resp.status_code == 429:
            return []
        resp.raise_for_status()
        body = resp.json()
    except Exception:
        return []
    children = body.get("data", {}).get("children", [])
    stories: list[dict] = []
    for entry in children:
        story = _normalise_post(entry, niche_id, subreddit)
        if story:
            stories.append(story)
    logger.info(
        "[reddit] r/%s fallback to %s yielded %d/%d video stories",
        subreddit,
        listing,
        len(stories),
        len(children),
    )
    return stories


def fetch_for_niche(
    niche_id: str,
    subreddits: list[str | dict],
    listing: str = "top",
    time_window: str = "day",
    per_sub_limit: int = 15,
) -> list[dict]:
    """Aggregate stories across all configured subreddits for a niche.

    Args:
        subreddits: Either a list of subreddit names (strings) or a
            list of {"name": "...", "listing": "...", "t": "..."}
            override dicts. Lets gaming use ``"hot"`` for fast-moving
            r/LivestreamFail while sports uses ``"top"`` for r/nba.

    Returns:
        Deduped list of stories (by canonical_url) ranked by
        ``view_velocity`` descending.
    """
    import time as _time

    seen_urls: set[str] = set()
    all_stories: list[dict] = []
    # Reddit rate-limits RSS aggressively per IP. Empirical measurement
    # from WARP exit (2026-08-06): even 8-10s spacing between DIFFERENT
    # subreddits triggers 429 once the first request has been consumed.
    # Bumped default from 2s → 5s to cut the burst pressure. Combined
    # with the RSS 429 retry (30s), ~80% of subreddits should succeed
    # per run. Real fix remains Reddit OAuth (600 rpm, days-to-weeks
    # approval process). Per-niche cost: ~50s for 10 subreddits — fine
    # for a daily cron. Bump via REDDIT_INTER_SUB_SLEEP_SEC env if a
    # niche's IP proves more/less rate-tolerant.
    inter_sub_sleep = float(os.environ.get("REDDIT_INTER_SUB_SLEEP_SEC", "5.0"))
    for idx, sub in enumerate(subreddits):
        if idx > 0 and inter_sub_sleep > 0:
            _time.sleep(inter_sub_sleep)
        if isinstance(sub, dict):
            name = sub.get("name", "")
            sub_listing = sub.get("listing", listing)
            sub_t = sub.get("t", time_window)
            sub_limit = int(sub.get("limit", per_sub_limit))
        else:
            name = str(sub)
            sub_listing = listing
            sub_t = time_window
            sub_limit = per_sub_limit
        if not name:
            continue
        for story in fetch_subreddit(
            name,
            niche_id,
            listing=sub_listing,
            time_window=sub_t,
            limit=sub_limit,
        ):
            url = story["canonical_url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)
            all_stories.append(story)

    all_stories.sort(key=lambda s: s.get("view_velocity", 0), reverse=True)
    logger.info(
        "[reddit] niche=%s aggregated %d unique video stories across %d subreddits",
        niche_id,
        len(all_stories),
        len(subreddits),
    )
    return all_stories
