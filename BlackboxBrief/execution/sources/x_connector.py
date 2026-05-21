"""X/Twitter connector for creator media discovery."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List


def _build_client() -> Any:
    """Build Tweepy client with user-auth credentials."""
    api_key = (os.getenv("X_API_KEY") or "").strip()
    api_secret = (os.getenv("X_API_SECRET") or "").strip()
    access_token = (os.getenv("X_ACCESS_TOKEN") or "").strip()
    access_secret = (os.getenv("X_ACCESS_SECRET") or "").strip()

    if not api_key or not api_secret or not access_token or not access_secret:
        raise ValueError("Missing X credentials (X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET)")

    try:
        import tweepy
    except ImportError as exc:
        raise RuntimeError("tweepy is not installed") from exc

    return tweepy.Client(
        consumer_key=api_key,
        consumer_secret=api_secret,
        access_token=access_token,
        access_token_secret=access_secret,
        wait_on_rate_limit=False,
    )


def _to_iso(ts: Any) -> str:
    if ts is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc).isoformat()
    return str(ts)


def _tweet_entries(resp: Any) -> List[Dict[str, Any]]:
    """Convert Tweepy response to normalized entry objects."""
    if not resp or not getattr(resp, "data", None):
        return []

    includes = getattr(resp, "includes", {}) or {}

    media_items = includes.get("media", []) if isinstance(includes, dict) else []
    users = includes.get("users", []) if isinstance(includes, dict) else []

    media_map = {m.media_key: m for m in media_items if getattr(m, "media_key", None)}
    user_map = {u.id: u for u in users if getattr(u, "id", None)}

    entries: List[Dict[str, Any]] = []
    for tweet in resp.data:
        media_urls: List[str] = []
        media_keys = list(getattr(getattr(tweet, "attachments", None), "get", lambda *_: [])("media_keys", []))

        # attachments may be dict when tweepy returns plain JSON-like object
        if isinstance(getattr(tweet, "attachments", None), dict):
            media_keys = tweet.attachments.get("media_keys", [])

        for key in media_keys:
            m = media_map.get(key)
            if not m:
                continue
            url = getattr(m, "url", None) or getattr(m, "preview_image_url", None)
            if url:
                media_urls.append(url)

        # We only keep tweets with concrete media evidence.
        if not media_urls:
            continue

        author_id = getattr(tweet, "author_id", None)
        user = user_map.get(author_id)
        username = getattr(user, "username", "creator") if user else "creator"

        text = (getattr(tweet, "text", "") or "").replace("\n", " ").strip()
        title = text[:120] if text else f"Creator media post by @{username}"
        link = f"https://x.com/{username}/status/{tweet.id}"

        summary_bits = [text[:500] if text else ""]
        summary_bits.extend([f"Media: {u}" for u in media_urls[:4]])
        summary_bits.append(f"Tweet: {link}")

        entries.append(
            {
                "title": title,
                "link": link,
                "summary": "\n".join([s for s in summary_bits if s]),
                "published": _to_iso(getattr(tweet, "created_at", None)),
                "author": f"@{username}",
            }
        )

    return entries


def _search_keywords(client: Any, queries: List[str], per_query: int, days: int) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    seen = set()

    full_since = datetime.now(timezone.utc) - timedelta(days=days)
    recent_days = min(days, max(1, int(os.getenv("X_RECENT_SEARCH_MAX_DAYS", "6"))))
    recent_since = datetime.now(timezone.utc) - timedelta(days=recent_days)
    full_since_iso = full_since.strftime("%Y-%m-%dT%H:%M:%SZ")
    recent_since_iso = recent_since.strftime("%Y-%m-%dT%H:%M:%SZ")

    for raw in queries:
        q = str(raw).strip()
        if not q:
            continue

        query = f"({q}) (has:videos OR has:images) -is:retweet lang:en"
        call_args = {
            "query": query,
            "max_results": max(10, min(per_query, 100)),
            "tweet_fields": ["created_at", "author_id", "attachments"],
            "expansions": ["attachments.media_keys", "author_id"],
            "media_fields": ["url", "preview_image_url", "type"],
        }

        resp = None
        if days > 7 and hasattr(client, "search_all_tweets"):
            try:
                resp = client.search_all_tweets(start_time=full_since_iso, **call_args)
            except Exception as exc:
                message = str(exc).lower()
                if "403" not in message and "not authorized" not in message and "forbidden" not in message:
                    raise

        if resp is None:
            # search_recent_tweets accepts only the recent window (typically ~7 days).
            resp = client.search_recent_tweets(start_time=recent_since_iso, **call_args)

        for e in _tweet_entries(resp):
            if e["link"] in seen:
                continue
            seen.add(e["link"])
            entries.append(e)

    return entries


def _creator_timelines(client: Any, creators: List[str], per_creator: int, days: int) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    seen = set()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    for raw in creators:
        username = str(raw).strip().lstrip("@")
        if not username:
            continue

        user_resp = client.get_user(username=username, user_fields=["id", "username"])
        user = getattr(user_resp, "data", None)
        if not user:
            continue

        resp = client.get_users_tweets(
            id=user.id,
            max_results=max(5, min(per_creator, 100)),
            exclude=["retweets", "replies"],
            tweet_fields=["created_at", "author_id", "attachments"],
            expansions=["attachments.media_keys", "author_id"],
            media_fields=["url", "preview_image_url", "type"],
        )

        for e in _tweet_entries(resp):
            # Guard the lookback again in case API ignores cutoff due endpoint behavior.
            try:
                pub = datetime.fromisoformat(e["published"].replace("Z", "+00:00"))
                if pub < cutoff:
                    continue
            except Exception:
                pass

            if e["link"] in seen:
                continue
            seen.add(e["link"])
            entries.append(e)

    return entries


def fetch_source(src: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch creator media from X keyword search and/or creator timelines."""
    params = src.get("params", {}) or {}
    mode = str(params.get("mode", "keyword_discovery")).strip().lower()
    max_results = max(1, min(int(params.get("max_results", 60)), 400))
    per_seed = max(5, min(int(params.get("per_seed", 20)), 100))
    days = max(1, int(params.get("published_after_days", 60)))

    queries = [q for q in (params.get("queries", []) or []) if str(q).strip()]
    creators = [c for c in (params.get("creators", []) or []) if str(c).strip()]

    if mode == "keyword_discovery" and not queries:
        queries = [
            '"made with ai"',
            '"ai generated video"',
            '"midjourney"',
            '"kling ai"',
        ]

    client = _build_client()
    entries: List[Dict[str, Any]] = []

    try:
        if mode in {"keyword_discovery", "mixed"}:
            entries.extend(_search_keywords(client, queries, per_seed, days))

        if mode in {"creator_list", "mixed"} and creators:
            entries.extend(_creator_timelines(client, creators, per_seed, days))
    except Exception as exc:
        message = str(exc)
        if "429" in message or "rate" in message.lower():
            status = "rate_limited"
        elif "timed out" in message.lower() or "timeout" in message.lower():
            status = "timeout"
        else:
            status = "failed"
        return {
            "url": src.get("url", ""),
            "entry_count": 0,
            "entries": [],
            "fetch_status": status,
            "fetch_method": "connector:x",
            "error": message,
        }

    # Deduplicate by link while preserving order.
    seen_links = set()
    deduped: List[Dict[str, Any]] = []
    for e in entries:
        link = e.get("link", "")
        if not link or link in seen_links:
            continue
        seen_links.add(link)
        deduped.append(e)
        if len(deduped) >= max_results:
            break

    return {
        "url": src.get("url", ""),
        "entry_count": len(deduped),
        "entries": deduped,
        "fetch_status": "success",
        "fetch_method": "connector:x",
    }
