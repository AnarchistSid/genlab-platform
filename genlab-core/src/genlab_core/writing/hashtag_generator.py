"""Dynamic hashtag generation — topic-aware + niche base tags.

Extracts topic hashtags from story content and combines with
niche-specific base tags. Trending tag integration is optional
(requires Google Trends data).
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Base hashtags per niche — always included (2 per post).
#
# Task #627 (2026-07-09, roadmap E4): this dict is the fallback
# when the per-platform × per-niche pool config file is missing OR
# doesn't specify a cell. See ``_load_platform_pools()`` for the
# preferred lookup path. Keeping this hard-coded fallback preserves
# behavior for any environment without the config file.
_NICHE_BASE: dict[str, list[str]] = {
    "gaming": ["#Gaming", "#GamingClips"],
    "sports": ["#Sports", "#Clutch"],
    "movies": ["#Movies", "#Cinema"],
    "anime": ["#Anime", "#Manga"],
    "ai_creators": ["#AI", "#Tech"],
}


_PLATFORM_POOLS_YAML = (
    Path(__file__).resolve().parents[3] / "config" / "platform_hashtag_pools.yaml"
)


@lru_cache(maxsize=1)
def _load_platform_pools() -> dict[str, dict[str, list[str]]]:
    """Read ``config/platform_hashtag_pools.yaml`` at first call.

    Returns ``{platform: {niche_id: [tags]}}``. Fail-open: any read
    or parse error yields ``{}``, and downstream lookups fall back
    to ``_NICHE_BASE`` (platform-agnostic behavior — same as pre-
    #627).

    LRU-cached because this is called on every hashtag generation
    (once per post) and the yaml is static. Test suites that need to
    swap the pool call ``_load_platform_pools.cache_clear()``.
    """
    try:
        import yaml  # local import — yaml is optional in some dev envs

        text = _PLATFORM_POOLS_YAML.read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
        platforms = data.get("platforms") or {}
        # Coerce to the exact shape callers expect: str keys, list values.
        result: dict[str, dict[str, list[str]]] = {}
        for plat, niches in platforms.items():
            if not isinstance(niches, dict):
                continue
            result[str(plat)] = {
                str(nid): [str(t) for t in (tags or [])]
                for nid, tags in niches.items()
                if isinstance(tags, list)
            }
        return result
    except FileNotFoundError:
        # Legit: config file may not be present in bare test envs.
        return {}
    except Exception as exc:
        logger.warning("[hashtag_generator] platform pool load failed: %s", exc)
        return {}


def _resolve_niche_base(niche_id: str, platform: str) -> list[str]:
    """Look up base tags for (niche, platform), with fallback.

    Precedence:
      1. Per-platform × per-niche entry in
         ``platform_hashtag_pools.yaml``
      2. Legacy per-niche entry in ``_NICHE_BASE``
      3. Generic ``["#Content"]``
    """
    pools = _load_platform_pools()
    plat_map = pools.get(platform, {})
    if niche_id in plat_map and plat_map[niche_id]:
        return plat_map[niche_id]
    if niche_id in _NICHE_BASE:
        return _NICHE_BASE[niche_id]
    return ["#Content"]


# Topic keyword → hashtag mapping for common topics
_TOPIC_HASHTAGS: dict[str, str] = {
    # Gaming
    "minecraft": "#Minecraft",
    "fortnite": "#Fortnite",
    "valorant": "#VALORANT",
    "gta": "#GTA6",
    "elden ring": "#EldenRing",
    "call of duty": "#CallOfDuty",
    "playstation": "#PlayStation",
    "xbox": "#Xbox",
    "nintendo": "#Nintendo",
    "steam": "#Steam",
    "esports": "#Esports",
    "battle royale": "#BattleRoyale",
    # Sports
    "nba": "#NBA",
    "nfl": "#NFL",
    "ipl": "#IPL",
    "cricket": "#Cricket",
    "football": "#Football",
    "soccer": "#Soccer",
    "ufc": "#UFC",
    "tennis": "#Tennis",
    "f1": "#F1",
    "baseball": "#MLB",
    "basketball": "#Basketball",
    "premier league": "#PremierLeague",
    # Movies
    "marvel": "#Marvel",
    "dc": "#DC",
    "disney": "#Disney",
    "netflix": "#Netflix",
    "oscar": "#Oscars",
    "horror": "#Horror",
    "thriller": "#Thriller",
    "sci-fi": "#SciFi",
    "star wars": "#StarWars",
    "trailer": "#Trailer",
    "box office": "#BoxOffice",
    # Anime
    "one piece": "#OnePiece",
    "naruto": "#Naruto",
    "dragon ball": "#DragonBall",
    "jujutsu kaisen": "#JJK",
    "demon slayer": "#DemonSlayer",
    "attack on titan": "#AOT",
    "my hero academia": "#MHA",
    "crunchyroll": "#Crunchyroll",
    "isekai": "#Isekai",
    "shonen": "#Shonen",
    # AI
    "chatgpt": "#ChatGPT",
    "openai": "#OpenAI",
    "midjourney": "#Midjourney",
    "sora": "#Sora",
    "claude": "#Claude",
    "stable diffusion": "#StableDiffusion",
    "llm": "#LLM",
    "gpt": "#GPT",
    "deep learning": "#DeepLearning",
}

# Platform hashtag count limits
_PLATFORM_LIMITS: dict[str, int] = {
    # 2026-07-08 (task #582): reduced 5→3 to align with post-2024 IG
    # ranking. Meta's algorithm updates through 2024 progressively
    # down-weighted posts using >3 hashtags — 3 is now the sweet spot
    # for reach on Reels (source: Meta creator research + industry
    # A/B tests). Prompt drift audit round 2 confirmed the 5-tag
    # setting was pre-2024 optimal but currently hurts reach.
    "instagram": 3,
    "youtube": 0,  # YouTube uses tags in description, not hashtags
    "twitter": 2,  # Less is more on X
    "facebook": 3,  # Moderate
    "threads": 3,  # Similar to IG but fewer
    "tiktok": 5,  # TikTok algo still rewards 3-5 tags per 2026 testing
}


def _extract_topic_hashtags(story: dict[str, Any], niche_id: str, max_tags: int = 3) -> list[str]:
    """Extract topic-specific hashtags from story title and summary."""
    text = (
        (story.get("title", "") or "")
        + " "
        + (story.get("summary", "") or "")
        + " "
        + (story.get("hook", "") or story.get("hook_text", "") or "")
    ).lower()

    found: list[str] = []
    for keyword, hashtag in _TOPIC_HASHTAGS.items():
        if keyword in text and hashtag not in found:
            found.append(hashtag)
            if len(found) >= max_tags:
                break

    # If no topic hashtags found, extract proper nouns from title as hashtags
    if not found:
        title = story.get("title", "") or ""
        # Extract capitalized words (likely proper nouns) — 3+ chars, not common words
        # 2026-07-14 audit — expanded from 12 to ~50 stopwords + explicit
        # contraction-stem block. Live shipped content contained
        # ``#Anime #Manga #Nina #Couldn #Believe`` — "Couldn't Believe"
        # was tokenized as [Couldn, Believe] before the apostrophe, so
        # the regex captured "Couldn" and emitted `#Couldn` as a
        # hashtag. Also `#Believe` and `#Did` shipped as hashtags from
        # generic English verbs. Neither adds discoverability; both
        # look like a bot-generated mistake.
        common = {
            # articles / conjunctions / prepositions
            "the",
            "and",
            "for",
            "with",
            "from",
            "into",
            "onto",
            "upon",
            # pronouns
            "this",
            "that",
            "these",
            "those",
            "them",
            "their",
            "they",
            # generic verbs / auxiliaries
            "just",
            "will",
            "would",
            "should",
            "could",
            "might",
            "must",
            "have",
            "has",
            "had",
            "was",
            "were",
            "been",
            "being",
            "did",
            "does",
            "get",
            "got",
            "make",
            "made",
            "take",
            "took",
            "go",
            "goes",
            "went",
            "come",
            "came",
            "see",
            "saw",
            "seen",
            "know",
            "knew",
            "think",
            "thought",
            "said",
            "say",
            # question words / determiners
            "how",
            "why",
            "what",
            "when",
            "where",
            "which",
            "who",
            "some",
            "any",
            "many",
            "much",
            "most",
            "more",
            "less",
            # generic + banned adjectives / adverbs
            "new",
            "old",
            "big",
            "small",
            "good",
            "bad",
            "best",
            "worst",
            "very",
            "really",
            "actually",
            "literally",
            "basically",
            # contraction stems (from `Couldn't` → `Couldn`, etc.)
            "aren",
            "can",
            "couldn",
            "didn",
            "doesn",
            "don",
            "hadn",
            "hasn",
            "haven",
            "isn",
            "shouldn",
            "wasn",
            "weren",
            "won",
            "wouldn",
        }
        words = re.findall(r"\b[A-Z][a-z]{2,}\b", title)
        for word in words:
            if word.lower() not in common and len(found) < max_tags:
                tag = f"#{word}"
                if tag not in found:
                    found.append(tag)

    return found[:max_tags]


def generate_hashtags(
    story: dict[str, Any],
    niche_id: str,
    platform: str = "instagram",
    trending_keywords: list[str] | None = None,
) -> list[str]:
    """Generate dynamic hashtags for a post.

    Combines: niche base tags + topic-specific tags + optional trending tag.

    Args:
        story: Story dict with title, summary, hook.
        niche_id: Niche identifier.
        platform: Target platform (affects count limits).
        trending_keywords: Optional list of currently trending keywords.

    Returns:
        List of hashtag strings (e.g., ["#Gaming", "#Minecraft", "#Trending"]).
    """
    limit = _PLATFORM_LIMITS.get(platform, 5)
    if limit == 0:
        return []

    tags: list[str] = []

    # 1. Niche base tags. Task #627: prefer the per-platform ×
    # per-niche pool if config supplies one; fall through to the
    # legacy per-niche defaults.
    #
    # 2026-08-12 (F-QB-0708 pt 3): sample 2 tags deterministically
    # from the pool rather than always taking the first two.
    # Motivating audit: 29% of recent captions had `#{Niche}Reels`
    # (position #2 in the old pool) creating a template signature
    # YouTube's inauthentic-content detection flags. Deterministic
    # per story_id so a retry produces identical tags (no churn)
    # but different stories get different pairs (variety).
    #
    # Fall back to `base[:2]` (deterministic first-two) when the
    # pool is small (<= 2 tags — the sample would degenerate to
    # the same result anyway) or when no story_id is available.
    base = _resolve_niche_base(niche_id, platform)
    if len(base) <= 2:
        tags.extend(base[:2])
    else:
        story_id = str(story.get("story_id") or story.get("id") or "").strip()
        if story_id:
            import hashlib
            import random as _random

            seed_bytes = hashlib.sha256(
                f"{story_id}|{niche_id}|{platform}".encode()
            ).digest()
            rng = _random.Random(int.from_bytes(seed_bytes[:8], "big"))
            tags.extend(rng.sample(base, 2))
        else:
            # No story_id → fall back to first-two (matches pre-fix
            # behavior for tests / one-off calls without a story).
            tags.extend(base[:2])

    # 2. Topic-specific tags from story content
    topic_tags = _extract_topic_hashtags(story, niche_id, max_tags=limit - len(tags))
    tags.extend(topic_tags)

    # 3. Trending tag (if available and we have room)
    if trending_keywords and len(tags) < limit:
        for kw in trending_keywords[:3]:
            tag = f"#{kw.replace(' ', '').title()}"
            if tag not in tags and len(tags) < limit:
                tags.append(tag)
                break  # Only add 1 trending tag

    return tags[:limit]


def generate_youtube_tags(
    story: dict[str, Any],
    niche_id: str,
    trending_keywords: list[str] | None = None,
) -> list[str]:
    """Generate YouTube tags (no # prefix, comma-separated in description).

    YouTube uses tags differently — more keywords, no # prefix.
    """
    title = story.get("title", "") or ""
    story.get("hook", "") or story.get("hook_text", "") or ""

    tags: list[str] = []

    # Niche tags
    niche_tags = {
        "gaming": ["gaming", "video games", "gameplay", "gaming clips"],
        "sports": ["sports", "highlights", "sports news", "clutch moments"],
        "movies": ["movies", "film", "cinema", "movie review", "trailer"],
        "anime": ["anime", "manga", "anime clips", "anime highlights"],
        "ai_creators": ["AI", "artificial intelligence", "tech", "AI tools"],
    }
    tags.extend(niche_tags.get(niche_id, ["content"])[:4])

    # Extract topic keywords from title
    common = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "just",
        "new",
        "how",
        "a",
        "an",
        "in",
        "on",
        "of",
        "is",
        "to",
    }
    words = [w.strip(".,!?:;'\"") for w in title.split() if len(w) > 2 and w.lower() not in common]
    tags.extend(words[:6])

    # Trending keywords
    if trending_keywords:
        tags.extend(trending_keywords[:2])

    # Dedupe
    seen: set[str] = set()
    unique: list[str] = []
    for t in tags:
        low = t.lower()
        if low not in seen:
            seen.add(low)
            unique.append(t)

    return unique[:15]
