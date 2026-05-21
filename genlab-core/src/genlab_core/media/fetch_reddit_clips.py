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
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

import requests

logger = logging.getLogger(__name__)

# Reddit blocks the default requests UA. The Gen Lab UA is benign and
# identifies the project for any rate-limit hand-wringing.
_REDDIT_UA = "GenLab/0.1 (+https://aspirehub.ai/genlab) by aspirehub"

# Reddit blocks datacenter IPs with 403. The Hetzner host hits this; we
# route through the same Cloudflare WARP SOCKS5 proxy that yt-dlp uses.
# Set GENLAB_HTTP_SOCKS_PROXY to e.g. socks5://127.0.0.1:40000 to enable.
# Without it, requests go direct (works fine from dev machines).
_REDDIT_PROXY_URL = os.environ.get("GENLAB_HTTP_SOCKS_PROXY", "").strip()
_REDDIT_PROXIES = (
    {"http": _REDDIT_PROXY_URL, "https": _REDDIT_PROXY_URL} if _REDDIT_PROXY_URL else None
)

# Endpoints that surface high-engagement posts. "top" with t=day gives
# the day's best; "hot" gives current momentum. We mix both so the
# selection isn't biased toward midnight-of-yesterday posts.
_REDDIT_TIMEOUT = 15  # seconds


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

    # Velocity proxy similar to TrendingVideoFetcher's view_velocity:
    # upvotes per hour scaled to be comparable with YouTube's
    # view_velocity (which is views/hr in the 100-50000 range).
    # 1 upvote ~= 100 views as a rough engagement-equivalence.
    view_velocity = (score * 100.0) / age_hours

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
        "view_count": score * 100,  # synth proxy for the velocity field
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
    # Anonymous Reddit allows ~60 req/hr per IP. A daily fetch hitting
    # 9 subreddits in ~5 seconds reliably gets 429s on the last few subs
    # when routed through a single TOR exit (observed 2026-05-21). A 2s
    # spacer between subs spreads the burst enough that a typical TOR
    # exit doesn't trip the limit. Total per-niche delay: ~18s, fine
    # for a once-daily cron. Real fix is Reddit OAuth (600/min).
    inter_sub_sleep = float(os.environ.get("REDDIT_INTER_SUB_SLEEP_SEC", "2.0"))
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
