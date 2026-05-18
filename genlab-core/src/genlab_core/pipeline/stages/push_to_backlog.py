"""Pipeline stage: Push stories and blueprints to SharePoint backlog.

Shared implementation for all niches. Reads ``niche_id`` from the
pipeline context instead of a hardcoded constant, eliminating the
need for per-niche copies of this stage.

Usage:
    stage = PushToBacklog()
    context = stage.execute(context)   # context["niche_id"] must be set
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from genlab_core.cache.stable_ids import generate_candidate_id, generate_story_id
from genlab_core.http.backlog_client import BacklogClient
from genlab_core.settings import settings
from genlab_core.utils.text_sanitizer import sanitize_for_graph_api

logger = logging.getLogger(__name__)

# Default arm mapping per niche — maps content signals to bandit arm IDs.
# These must match the arm_id values in the bandit_arms table.
_NICHE_ARM_DEFAULTS: dict[str, str] = {
    "gaming": "gameplay_clip",
    "sports": "highlight_play",
    "movies": "trailer_drop",
    "anime": "episode_moment",
    "ai_creators": "tool_demo",
}

_ARM_KEYWORDS: dict[str, list[tuple[str, list[str]]]] = {
    "gaming": [
        ("esports_highlight", ["esports", "tournament", "championship", "league", "competitive"]),
        ("trailer_reaction", ["trailer", "reveal", "announcement", "launch", "release"]),
        ("patch_news", ["patch", "update", "nerf", "buff", "season", "hotfix"]),
    ],
    "sports": [
        ("breaking_trade", ["trade", "transfer", "sign", "contract", "deal", "free agent"]),
        ("record_milestone", ["record", "milestone", "history", "first ever", "youngest", "oldest"]),
        ("upset_reaction", ["upset", "shock", "underdog", "eliminated", "comeback"]),
    ],
    "movies": [
        ("scene_reaction", ["scene", "moment", "clip", "reaction", "watch"]),
        ("box_office_update", ["box office", "opening weekend", "gross", "million", "billion"]),
        ("cast_reveal", ["cast", "role", "playing", "starring", "joins", "reveal"]),
    ],
    "anime": [
        ("season_announcement", ["season", "announced", "confirmed", "adaptation", "premiere"]),
        ("fight_scene", ["fight", "battle", "vs", "clash", "power", "epic"]),
        ("adaptation_news", ["manga", "light novel", "adaptation", "studio", "animated"]),
    ],
    "ai_creators": [
        ("model_release", ["release", "launched", "model", "gpt", "claude", "gemini", "llm"]),
        ("creative_showcase", ["created", "made", "generated", "art", "music", "video", "film"]),
        ("comparison_test", ["vs", "compared", "better", "benchmark", "test", "which"]),
    ],
}


# Statuses that actively block re-creation of a blueprint. Any row in one of
# these states represents content we've already committed to producing (or
# published) — emitting a second blueprint for the same source would create
# a duplicate.
#
# Conversely, rows in ARCHIVED (whether rejected by a user or auto-archived
# because rendered media went missing) or PUBLISH_FAILED must NOT block
# re-creation: the prior attempt didn't reach an audience, and retrying is
# the whole point of the pipeline. This set is the single source of truth
# for dedup decisions in push_to_backlog.
_BLOCKING_STATUSES: frozenset[str] = frozenset({
    "PUBLISHED",
    "PUBLISHING",
    "VISUAL_READY",
    "DRAFTED",
    "SCORED",
})


def _is_blocking(row: dict) -> bool:
    """True if an existing blueprint row should block re-creation of the same content."""
    fields = row.get("fields", row)
    return fields.get("status", "") in _BLOCKING_STATUSES


_TOPIC_MAP = {
    # Normalize raw source names to clean categories
    "anilist": "anilist",
    "jikan_promos": "jikan",
    "rss_anime_news_network": "anime_news",
    "rss_screen_rant": "screen_rant",
    "rss_ign_movies": "ign",
    "rss_ign": "ign",
    "rss_indiewire": "indiewire",
    "rss_deadline_hollywood": "deadline",
    "rss_variety": "variety",
    "rss_hollywood_reporter": "hollywood_reporter",
    "rss_collider": "collider",
    "rss_espn": "espn",
    "espn_news": "espn_news",
    "espn_scoreboard": "espn_live",
    "tmdb_trailer": "tmdb_trailer",
    "youtube_trending": "youtube_trending",
    "youtube_rss": "youtube_channel",
    "youtube_content": "youtube_content",
    "twitch_trending": "twitch_trending",
    "twitch_clip": "twitch_clip",
    "scorebat": "scorebat",
    "steam_trailer": "steam",
    "rss_fallback": "rss_fallback",
    "channel_subscription": "youtube_channel",
    "rss": "rss",
}


def _normalize_topic(raw_source: str) -> str:
    """Normalize raw source identifiers to clean topic categories."""
    if not raw_source:
        return "unknown"
    low = raw_source.lower().strip()
    # Check direct mapping
    if low in _TOPIC_MAP:
        return _TOPIC_MAP[low]
    # Check prefix matching
    for prefix, topic in _TOPIC_MAP.items():
        if low.startswith(prefix):
            return topic
    # If it's a long string (raw video title), truncate to a label
    if len(raw_source) > 30:
        return "youtube_content"
    return low


def _get_engagement_arm_boost(client, niche_id: str) -> dict[str, float]:
    """Legacy: boost multipliers derived from historical priority_score.

    Kept as a fallback when bandit posteriors are unavailable. Arms with
    above-average historical priority_score get up to 1.3x, below-average
    down to 0.8x.
    """
    try:
        records = client.blueprints.all(
            formula=f"AND({{niche_id}}='{niche_id}',{{status}}='PUBLISHED')",
            max_records=50,
        )
        arm_scores: dict[str, list[float]] = {}
        for rec in records:
            fields = rec.get("fields", rec)
            arm = fields.get("arm_id") or "default"
            score = float(fields.get("priority_score", 0) or 0)
            if score > 0:
                arm_scores.setdefault(arm, []).append(score)

        if not arm_scores:
            return {}

        arm_avgs = {arm: sum(scores) / len(scores) for arm, scores in arm_scores.items()}
        overall_avg = sum(arm_avgs.values()) / len(arm_avgs) if arm_avgs else 0.5

        boosts: dict[str, float] = {}
        for arm, avg in arm_avgs.items():
            if overall_avg > 0:
                ratio = avg / overall_avg
                boosts[arm] = max(0.8, min(1.3, ratio))
            else:
                boosts[arm] = 1.0
        return boosts
    except Exception:
        return {}


# Multiplier range applied to priority_score. A sample of 0.0 produces
# the floor (under-performing arms deprioritized); a sample of 1.0
# produces the ceiling (over-performing arms boosted). Mirrors the
# legacy engagement-boost range for behavioural continuity.
_BANDIT_BOOST_FLOOR = 0.7
_BANDIT_BOOST_CEIL = 1.3


def _get_bandit_arm_boost(client, niche_id: str) -> dict[str, float]:
    """Thompson-sampled boost multipliers from bandit_arms posteriors.

    For each arm in the niche, draws one Beta(alpha, beta) sample and
    maps it into the multiplier range [_BANDIT_BOOST_FLOOR,
    _BANDIT_BOOST_CEIL]. The randomness IS the exploration mechanism —
    a fresh arm at alpha=beta=1 has uniform posterior so its sample
    varies over the full range each call; a well-trained arm
    concentrates samples near its posterior mean.

    Falls back to {} on any error so the caller can degrade to the
    engagement-history boost or neutral multipliers.
    """
    try:
        from genlab_core.learning.arm_loader import load_all_arms
    except ImportError:
        return {}

    proxy = getattr(client, "bandit_arms", None)
    if proxy is None:
        return {}

    arms = load_all_arms(proxy, niche_id)
    if not arms:
        return {}

    import random
    spread = _BANDIT_BOOST_CEIL - _BANDIT_BOOST_FLOOR
    boosts: dict[str, float] = {}
    for arm_id, (alpha, beta) in arms.items():
        # Guard against pathological alpha/beta — Beta requires both > 0.
        a = alpha if alpha > 0 else 1.0
        b = beta if beta > 0 else 1.0
        try:
            sample = random.betavariate(a, b)
        except (ValueError, OverflowError):
            sample = 0.5
        boosts[arm_id] = round(_BANDIT_BOOST_FLOOR + spread * sample, 4)
    return boosts


# Backwards-compatible alias for any external import.
_get_arm_boost = _get_bandit_arm_boost


def _apply_engagement_boost(base_score: float, arm_id: str, boosts: dict[str, float]) -> float:
    """Apply engagement-based boost to priority score."""
    multiplier = boosts.get(arm_id, 1.0)
    return round(base_score * multiplier, 4)


def _classify_arm(niche_id: str, story: dict, content: dict) -> str:
    """Classify content into a bandit arm_id based on keyword matching."""
    text = f"{story.get('title', '')} {content.get('hook', '')} {story.get('summary', '')}".lower()

    for arm_id, keywords in _ARM_KEYWORDS.get(niche_id, []):
        if any(kw in text for kw in keywords):
            return arm_id

    return _NICHE_ARM_DEFAULTS.get(niche_id, "default")


class PushToBacklog:
    """Push stories and blueprints to the shared SharePoint backlog.

    Niche-agnostic: ``niche_id`` is read from ``context["niche_id"]``
    at execution time. Raises ``ValueError`` if the key is missing.
    """

    def __init__(self) -> None:
        self._client: BacklogClient | None = None

    def _get_client(self, context: dict[str, Any]) -> BacklogClient:
        """Lazy-initialize BacklogClient.

        Config path resolution (in priority order):
          1. context["backlog_config_path"] — explicit override
          2. context["niche_root"] / config / lists_config.yaml — niche dir
          3. Fall through to BacklogClient() defaults (BACKLOG_CONFIG_PATH
             env var → CWD walk)
        """
        if self._client is None:
            config_path = context.get("backlog_config_path")

            if not config_path:
                niche_root = context.get("niche_root")
                if niche_root:
                    candidate = Path(niche_root) / "config" / "lists_config.yaml"
                    if candidate.exists():
                        config_path = str(candidate)
                        logger.debug(
                            "[PUSH] Resolved backlog config from niche_root: %s",
                            config_path,
                        )

            if config_path:
                self._client = BacklogClient(config_path=config_path)
            else:
                self._client = BacklogClient()
        return self._client

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:  # noqa: C901
        niche_id = context.get("niche_id")
        if not niche_id:
            raise ValueError(
                "PushToBacklog requires context['niche_id'] to be set. "
                "Ensure the pipeline runner populates niche_id before this stage."
            )

        stories = context.get("stories", [])
        if not stories:
            logger.info("[PUSH] No stories to push")
            return context

        import os
        _use_postgres = os.getenv("GENLAB_USE_POSTGRES", "").lower() == "true"
        if not _use_postgres and not all([
            settings.azure_tenant_id,
            settings.azure_client_id,
            settings.azure_client_secret,
            settings.sharepoint_site_id,
        ]):
            logger.warning("[PUSH] Azure/SharePoint credentials missing, skipping backlog push")
            context.setdefault("run_stats", {})["backlog_push"] = {
                "stories_pushed": 0,
                "blueprints_pushed": 0,
                "status": "skipped_no_credentials",
            }
            return context

        try:
            client = self._get_client(context)
        except Exception as e:
            logger.error("[PUSH] Failed to initialize BacklogClient: %s", e)
            context.setdefault("run_stats", {})["backlog_push"] = {
                "stories_pushed": 0,
                "blueprints_pushed": 0,
                "status": f"error_init: {e}",
            }
            return context

        stories_pushed = 0
        blueprints_pushed = 0
        video_dedup_skipped = 0
        errors: list[str] = []

        # Load recent hooks for this niche to prevent cross-run duplicates.
        # Time-windowed to prevent hook starvation as history grows.
        #
        # Only blueprints in an *active* state (see _BLOCKING_STATUSES) count
        # as existing hooks. Rows in ARCHIVED (whether rejected by the user
        # or auto-archived by health_monitor.check_missing_media) don't
        # represent content we're committed to — they're free to re-emit.
        # Including them silently poisoned the dedup set for every re-run.
        existing_hooks: set[str] = set()
        non_blocking_skipped = 0
        recent_bps: list = []
        try:
            from datetime import datetime, timedelta, timezone as _tz2
            _hook_cutoff = datetime.now(_tz2.utc) - timedelta(days=30)
            recent_bps = client.blueprints.all(
                formula=f"{{niche_id}}='{niche_id}'",
                max_records=2000,
            )
            for bp in recent_bps:
                fields = bp.get("fields", bp)
                if not _is_blocking(bp):
                    non_blocking_skipped += 1
                    continue
                _bp_created = fields.get("created_at")
                if isinstance(_bp_created, str):
                    try:
                        _bp_created = datetime.fromisoformat(_bp_created.replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        _bp_created = None
                if _bp_created and _bp_created < _hook_cutoff:
                    continue
                h = (fields.get("hook") or "").strip().lower()
                if h:
                    existing_hooks.add(h)
            if existing_hooks or non_blocking_skipped:
                logger.info(
                    "[PUSH] Loaded %d existing hooks for dedup (skipped %d non-blocking)",
                    len(existing_hooks), non_blocking_skipped,
                )
        except Exception as e:
            logger.debug("[PUSH] Could not load existing hooks: %s", e)
        context["existing_hooks"] = existing_hooks

        # Bandit-driven arm boosts: Thompson-sample each arm's posterior
        # and convert the draw into a priority_score multiplier. Falls
        # back to engagement-history boosts (legacy) when bandit data is
        # unavailable, then to {} (neutral) as the final degradation.
        arm_boosts = _get_bandit_arm_boost(client, niche_id)
        boost_source = "bandit"
        if not arm_boosts:
            arm_boosts = _get_engagement_arm_boost(client, niche_id)
            boost_source = "engagement_history"
        if arm_boosts:
            logger.info(
                "[PUSH] Loaded %s boosts for %d arms: %s",
                boost_source, len(arm_boosts),
                {k: f"{v:.2f}" for k, v in arm_boosts.items()},
            )

        # Load content_memory hashes + active-blueprint URLs for cross-run dedup.
        #
        # We deliberately seed `seen_urls` from NON-REJECTED blueprints instead
        # of the stories table. Why: rejecting a blueprint shouldn't prevent
        # the next run from re-producing content for the same URL (e.g. the
        # same YouTube trending clip is still trending today). A stories-only
        # seed was blocking every re-run after a manual reject because stories
        # for rejected blueprints stayed in the dedup set forever.
        #
        # The tradeoff: stories that were ingested but never blueprinted (e.g.
        # writing failed) are now re-ingestable, which is what we want.
        seen_urls: set[str] = set()
        _existing_stories_for_titles: list = []
        _cm_records_for_titles: list = []
        try:
            from hashlib import sha256 as _sha256
            from datetime import datetime, timedelta, timezone as _tz
            _dedup_days = context.get("niche_config", {}).get(
                "pipeline", {}
            ).get("dedup_window_days", 14)
            _dedup_cutoff = datetime.now(_tz.utc) - timedelta(days=_dedup_days)
            # Historical note: an earlier version of formula_sql had a parameter
            # ordering bug with mixed > and = operators, forcing Python-side
            # date filtering. That bug is fixed — tests verify mixed-operator
            # queries return correctly ordered params. The Python-side filter
            # below is kept because it's simple and the story count is small.

            # Seed URL hashes from blueprints in blocking states only.
            active_bps = [bp for bp in recent_bps if _is_blocking(bp)]
            url_hashes_from_bps = 0
            for bp in active_bps:
                fields = bp.get("fields", bp)
                url = (fields.get("video_url") or "").strip()
                if url:
                    seen_urls.add(_sha256(url.encode()).hexdigest()[:16])
                    url_hashes_from_bps += 1
            logger.info(
                "[PUSH] Loaded %d URL hashes from %d active blueprints",
                url_hashes_from_bps, len(active_bps),
            )

            # Still load stories for title-level dedup (Layer 4.5 below).
            existing_stories = client.stories.all(
                formula=f"{{niche_id}}='{niche_id}'",
                max_records=2000,
            )
            _recent_stories = []
            for s in existing_stories:
                fields = s.get("fields", s)
                created = fields.get("created_at")
                if created:
                    if isinstance(created, str):
                        try:
                            created = datetime.fromisoformat(created.replace("Z", "+00:00"))
                        except (ValueError, TypeError):
                            created = None
                    if created and created >= _dedup_cutoff:
                        _recent_stories.append(s)
                else:
                    _recent_stories.append(s)
            _existing_stories_for_titles = _recent_stories
        except Exception as e:
            logger.debug("[PUSH] Could not load existing story URLs: %s", e)

        # content_memory used to be a second URL-level dedup layer, seeded
        # from every blueprint ever created. But content_memory entries are
        # never removed when a blueprint is rejected or archived, so the
        # same leak that affected the stories-based seed was hitting seen_urls
        # via content_memory too. We keep loading records (for title dedup)
        # but no longer inject their content_hash into seen_urls — the
        # blueprint-based URL seed above is now the single source of truth.
        try:
            cm_proxy = getattr(client, "content_memory", None)
            if cm_proxy:
                cm_records = cm_proxy.all(
                    formula=f"{{niche_id}}='{niche_id}'",
                    max_records=2000,
                )
                _cm_records_for_titles = cm_records
                logger.info(
                    "[PUSH] Total URL dedup hashes: %d (from %d active blueprints)",
                    len(seen_urls), len(active_bps),
                )
        except Exception as e:
            logger.debug("[PUSH] content_memory load failed (non-fatal): %s", e)

        # Collect titles for title-level dedup (Layer 4.5)
        # Seeded only from blueprints in blocking states — stories and
        # content_memory rows are NOT considered. Including them used to
        # drop today's stories against their own archived copies (symptom:
        # "Title near-dupe: 'X' ≈ 'x'" where X was a rejected row from
        # earlier in the day).
        _TITLE_DEDUP_DAYS = 7
        _title_cutoff = datetime.now(UTC) - timedelta(days=_TITLE_DEDUP_DAYS)
        existing_titles: set[str] = set()
        for bp in active_bps:
            f = bp.get("fields", bp)
            t = (f.get("title") or "").strip().lower()
            if t and len(t) > 10:
                _created = f.get("created_at") or f.get("published_at") or ""
                if _created:
                    try:
                        _dt = datetime.fromisoformat(str(_created).replace("Z", "+00:00"))
                        if _dt < _title_cutoff:
                            continue
                    except (ValueError, TypeError):
                        pass
                existing_titles.add(t)

        # Load titles from content_pool claimed by this niche (Layer 5.5)
        try:
            db_url = os.environ.get("DATABASE_URL")
            if db_url:
                import psycopg as _psycopg
                with _psycopg.connect(db_url) as _conn:
                    with _conn.cursor() as _cur:
                        _cur.execute(
                            "SELECT title FROM content_pool WHERE claimed_by = %s AND claimed_at > NOW() - INTERVAL '48 hours'",
                            (niche_id,),
                        )
                        for row in _cur.fetchall():
                            if row[0]:
                                existing_titles.add(row[0].strip().lower())
                logger.info("[PUSH] Loaded %d titles for cross-dedup", len(existing_titles))
        except Exception as exc:
            logger.debug("[PUSH] Failed to load titles for cross-dedup: %s", exc)
        context["existing_titles"] = existing_titles

        existing_titles = context.get("existing_titles", set())

        for story in stories:
            title = sanitize_for_graph_api(story.get("title", "Unknown"))
            source_url = story.get("source_url", "")
            published_at = story.get("published_at", datetime.now(UTC).isoformat())

            # URL-only dedup FIRST — catches recurring sources (AniList, Jikan)
            # that return the same URL with different timestamps each fetch.
            # seen_urls is seeded from active_bps only (see loader above),
            # so matches here represent content we're actively producing or
            # have already published — legitimately skippable.
            from hashlib import sha256
            url_hash = sha256(source_url.encode()).hexdigest()[:16] if source_url else ""
            if url_hash and url_hash in seen_urls:
                logger.info(
                    "[PUSH] URL dedup: skipping '%s' (URL already in active blueprint set)",
                    title[:60],
                )
                continue

            story_id = generate_story_id(source_url, published_at)

            # Story_id dedup — kept for tracking consistency. seen_urls only
            # contains blueprint-derived URL hashes after the content_memory
            # fix, so this will no longer match archived story_ids.
            if story_id in seen_urls:
                logger.info(
                    "[PUSH] story_id dedup: skipping '%s' (story_id collision in active set)",
                    title[:60],
                )
                continue

            # Title similarity dedup (Layer 4.5)
            title_lower = title.lower().strip()
            title_is_dupe = False
            if len(title_lower) > 10 and existing_titles:
                title_words = set(title_lower.split())
                for existing in existing_titles:
                    existing_words = set(existing.split())
                    if len(title_words) > 3 and len(existing_words) > 3:
                        intersection = len(title_words & existing_words)
                        union = len(title_words | existing_words)
                        if union > 0 and intersection / union > 0.65:
                            logger.info("[PUSH] Title near-dupe: '%s' ≈ '%s'", title[:40], existing[:40])
                            title_is_dupe = True
                            break
            if title_is_dupe:
                continue
            existing_titles.add(title_lower)

            # Track URL hash so future stories with same URL are skipped
            if url_hash:
                seen_urls.add(url_hash)

            # Upsert story
            try:
                existing = client.find_story_by_story_id(story_id)
                if existing:
                    client.stories.update(existing["id"], {"niche_id": niche_id})
                    story_record = existing
                    logger.debug("[PUSH] Story '%s' already exists, updated niche_id", title)
                else:
                    record = client.stories.create({
                        "story_id": story_id,
                        "title": title,
                        "url": source_url,
                        "source": story.get("source", niche_id),
                        "published_at": published_at,
                        "summary": (story.get("summary") or "")[:255],
                        "priority": (
                            story.get("final_score") if story.get("final_score") is not None
                            else story.get("composite_score") if story.get("composite_score") is not None
                            else story.get("score", 0.5)
                        ),
                        "status": "INTAKE",
                        "niche_id": niche_id,
                    })
                    # Postgres create() returns UUID string; SharePoint returns dict
                    if isinstance(record, str):
                        story_record = {"id": record}
                    else:
                        story_record = record
                    stories_pushed += 1
                    logger.info("[PUSH] Created story '%s' (id=%s)", title, story_id)
            except Exception as e:
                logger.warning("[PUSH] Story '%s' failed: %s", title, e)
                errors.append(f"story:{title}: {e}")
                continue

            # Freshness gate: skip stories older than 7 days
            story_published = story.get("published_at", "")
            if story_published:
                try:
                    pub_dt = datetime.fromisoformat(story_published)
                    if pub_dt.tzinfo is None:
                        pub_dt = pub_dt.replace(tzinfo=UTC)
                    age_days = (datetime.now(UTC) - pub_dt).days
                    if age_days > 7:
                        logger.info("[PUSH] Story too old (%d days), skipping blueprint: %s", age_days, title[:40])
                        continue
                except (ValueError, TypeError):
                    pass  # unparseable date — allow through

            # Create blueprint from content
            content = story.get("content", {})
            if not content:
                logger.info("[PUSH] No content for '%s' — skipping blueprint (story has no written content)", title[:60])
                continue
            # Content may be a JSON string from the writing stage
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except (json.JSONDecodeError, TypeError):
                    logger.warning("[PUSH] Blueprint '%s' has unparseable content string", title)
                    continue

            # Extract video_id for dedup — available from FetchTrendingVideos or DownloadTopVideos
            video_id = story.get("video_id", "")
            if not video_id:
                # Try clip_index lookup
                clip_index = context.get("clip_index", {})
                clip_entry = clip_index.get("clips", {}).get(story_id, {})
                source_url_for_vid = clip_entry.get("source_url", "")
                if "youtube" in source_url_for_vid:
                    video_id = source_url_for_vid.split("v=")[-1].split("&")[0]

            # Video-level dedup: same clip must not create multiple blueprints.
            # Only rows in a blocking state count — see _BLOCKING_STATUSES.
            if video_id:
                try:
                    existing_by_video = client.blueprints.all(
                        formula=f"{{video_id}}='{video_id}'",
                        niche_id=niche_id,
                        max_records=5,
                    )
                    active_dupes = [bp for bp in existing_by_video if _is_blocking(bp)]
                    if active_dupes:
                        logger.info(
                            "[PUSH] Video already blueprinted: video_id=%s niche=%s — skipping",
                            video_id[:20], niche_id,
                        )
                        video_dedup_skipped += 1
                        continue
                except Exception as e:
                    logger.warning("[PUSH] Video dedup check failed: %s — allowing through", e)

            candidate_id = generate_candidate_id(
                story_id, niche_id, video_id or title,
            )
            story["candidate_id"] = candidate_id
            hook = content.get("hook", "")

            # Classify content into a bandit arm_id for the learning loop
            arm_id = _classify_arm(niche_id, story, content)

            # Cross-run hook dedup: exact + fuzzy (Jaccard similarity > 0.6)
            if hook:
                hook_lower = hook.strip().lower()
                hook_words = set(hook_lower.split())

                # Exact match
                if hook_lower in existing_hooks:
                    logger.info("[PUSH] Exact hook dupe, skipping: '%s'", hook[:60])
                    continue

                # Fuzzy match — Jaccard similarity against recent hooks
                is_near_dupe = False
                for existing in existing_hooks:
                    existing_words = set(existing.split())
                    if len(hook_words) > 2 and len(existing_words) > 2:
                        intersection = len(hook_words & existing_words)
                        union = len(hook_words | existing_words)
                        if union > 0 and intersection / union > 0.6:
                            logger.info(
                                "[PUSH] Near-dupe hook (%.0f%% similar), skipping: '%s' ≈ '%s'",
                                100 * intersection / union, hook[:40], existing[:40],
                            )
                            is_near_dupe = True
                            break
                if is_near_dupe:
                    continue

                existing_hooks.add(hook_lower)
            ig = content.get("instagram", {})
            yt = content.get("youtube", {})
            tw = content.get("x_twitter", {})
            fb = content.get("facebook", {})

            rendered_path = (story.get("media") or {}).get("rendered_path", "")

            try:
                existing_bp_raw = client.blueprints.all(
                    formula=f"{{candidate_id}}='{candidate_id}'",
                    max_records=5,
                )
                # Partition existing blueprints by blocking state.
                blocking_match = next(
                    (bp for bp in existing_bp_raw if _is_blocking(bp)), None,
                )
                non_blocking_match = next(
                    (bp for bp in existing_bp_raw if not _is_blocking(bp)), None,
                )
                if blocking_match:
                    existing_status = blocking_match.get("fields", blocking_match).get("status", "")
                    logger.info(
                        "[PUSH] Blueprint '%s' already %s — skipping re-create",
                        title, existing_status,
                    )
                    continue

                # Build the full fields dict — reused by both the revive
                # (update archived row in place) and the fresh-create branch.
                # Note: candidate_id is deliberately omitted on revive because
                # it's the PK and would trigger a UNIQUE-constraint error.
                if True:
                    story_record_id = story_record["id"]
                    fields: dict[str, Any] = {
                        "candidate_id": candidate_id,
                        "story": [story_record_id],
                        "story_id": story_id,
                        "video_id": video_id,
                        "video_url": story.get("source_url", ""),
                        "hook": hook,
                        "hook_text": hook,
                        "title": title,
                        "caption": ig.get("caption", ""),
                        "hashtags": " ".join(ig.get("hashtags", []) or re.findall(r"#\w+", ig.get("caption", ""))),
                        # YT publisher uses the `hook` column for the Shorts
                        # title and this field for the description. Stored as a
                        # plain string so the CTA engine's URL-prepend +
                        # disclosure-append produces a well-formed value, not a
                        # mangled JSON document.
                        "youtube_content": (
                            yt.get("description", "")
                            + ("\n\n" + content.get("youtube_attribution", "")
                               if content.get("youtube_attribution") else "")
                        ).strip(),
                        "twitter_content": json.dumps({"tweet_text": tw.get("tweet", tw.get("tweet_text", "")), "routing": tw.get("routing", "single")}),
                        "facebook_content": fb.get("caption", ""),
                        "threads_content": content.get("threads", {}).get("caption", ""),
                        "priority_score": _apply_engagement_boost(
                            story.get("final_score") if story.get("final_score") is not None
                            else story.get("composite_score") if story.get("composite_score") is not None
                            else story.get("score", 0.5),
                            arm_id, arm_boosts,
                        ),
                        "status": "VISUAL_READY" if rendered_path else "DRAFTED",
                        "format": "reel",
                        "niche_id": niche_id,
                        "arm_id": arm_id,
                        "topic": _normalize_topic(story.get("source", niche_id)),
                        "angle": (story.get("summary") or title)[:200],
                    }

                    # Hook style picked by the bandit at writing time
                    # (2026-05-17). Recorded so the eventual multi-arm
                    # update in metric_collector can attribute reward
                    # to style:{name} alongside the content_type arm.
                    if story.get("hook_style"):
                        fields["hook_style"] = story["hook_style"]

                    # LinUCB context fields — store for publish-time context building
                    for ctx_key in ("duration_seconds", "view_velocity", "source_type", "relevance_score"):
                        if story.get(ctx_key) is not None:
                            fields[ctx_key] = story[ctx_key]

                    # Persist urgency classification for express lane publishing
                    urgency = story.get("urgency_classification", {})
                    if urgency:
                        fields["urgency_classification"] = json.dumps(urgency)

                    # Affiliate fields (if matched by AffiliateMatch stage)
                    for af_key in (
                        "affiliate_product", "affiliate_url", "affiliate_network",
                        "affiliate_commission_pct", "affiliate_cta",
                    ):
                        if story.get(af_key):
                            fields[af_key] = story[af_key]

                    # Inject platform-specific CTAs into captions
                    if story.get("affiliate_product"):
                        from genlab_core.monetization.cta_engine import inject_cta
                        fields = inject_cta(fields, story)

                    if rendered_path:
                        fields["visual_paths"] = json.dumps([rendered_path])
                        # Auto-schedule for next available 06:30 UTC = 12:00 IST
                        # publish window.  Use today's slot if it hasn't passed yet,
                        # otherwise fall back to tomorrow.
                        now_utc = datetime.now(UTC)
                        today_slot = datetime(
                            now_utc.year, now_utc.month, now_utc.day,
                            6, 30, tzinfo=UTC,
                        )
                        if today_slot > now_utc:
                            publish_time = today_slot
                        else:
                            next_day = now_utc.date() + timedelta(days=1)
                            publish_time = datetime(
                                next_day.year, next_day.month, next_day.day,
                                6, 30, tzinfo=UTC,
                            )
                        fields["scheduled_for"] = publish_time.isoformat()
                    # Store thumbnail_url for dashboard previews when local
                    # renders are unavailable (e.g. on cloud dashboard server).
                    thumb = story.get("thumbnail_url", "")
                    if not thumb:
                        thumb = story.get("cover_url", "")
                    if not thumb and video_id:
                        thumb = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                    if not thumb:
                        # Steam header image fallback
                        app_id = story.get("steam_app_id")
                        if app_id:
                            thumb = f"https://cdn.akamai.steamstatic.com/steam/apps/{app_id}/header.jpg"
                    if thumb:
                        fields["thumbnail_url"] = thumb

                    if non_blocking_match is not None:
                        # Revive the archived/failed row instead of inserting.
                        # Strip candidate_id from update payload — it's the
                        # PK and Postgres rejects overwriting a unique key.
                        revive_fields = {
                            k: v for k, v in fields.items() if k != "candidate_id"
                        }
                        # Clear the old action_taken so reviewers see it fresh
                        revive_fields["action_taken"] = None
                        client.blueprints.update(non_blocking_match["id"], revive_fields)
                        blueprints_pushed += 1
                        prior_status = non_blocking_match.get("fields", non_blocking_match).get("status", "?")
                        logger.info(
                            "[PUSH] Revived blueprint '%s' (was %s → %s)",
                            title, prior_status, fields["status"],
                        )
                    else:
                        client.blueprints.create(fields, typecast=True)
                        blueprints_pushed += 1
                        logger.info(
                            "[PUSH] Created blueprint '%s' (status=%s)",
                            title, fields["status"],
                        )
                    # Record in content_memory for persistent URL-level dedup
                    try:
                        cm = getattr(client, "content_memory", None)
                        if cm and story_id not in seen_urls:
                            cm.create({
                                "content_hash": story_id,
                                "title": title[:200],
                                "url": source_url,
                                "niche_id": niche_id,
                                "first_seen": datetime.now(UTC).isoformat(),
                                "last_seen": datetime.now(UTC).isoformat(),
                            })
                            seen_urls.add(story_id)
                    except Exception as exc:
                        pass  # non-critical — other dedup layers cover it
            except Exception as e:
                logger.warning("[PUSH] Blueprint '%s' failed: %s", title, e)
                errors.append(f"blueprint:{title}: {e}")

        context.setdefault("run_stats", {})["backlog_push"] = {
            "stories_pushed": stories_pushed,
            "blueprints_pushed": blueprints_pushed,
            "video_dedup_skipped": video_dedup_skipped,
            "errors": errors[:5],
            "status": "ok" if not errors else f"partial ({len(errors)} errors)",
        }

        logger.info(
            "[PUSH] %d stories, %d blueprints pushed to backlog (%d video-dedup skipped, %d errors)",
            stories_pushed, blueprints_pushed, video_dedup_skipped, len(errors),
        )
        return context
