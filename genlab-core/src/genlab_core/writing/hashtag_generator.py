"""Dynamic hashtag generation — topic-aware + niche base tags.

Extracts topic hashtags from story content and combines with
niche-specific base tags. Trending tag integration is optional
(requires Google Trends data).
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Base hashtags per niche — always included (2 per post)
_NICHE_BASE: dict[str, list[str]] = {
    "gaming": ["#Gaming", "#GamingClips"],
    "sports": ["#Sports", "#Clutch"],
    "movies": ["#Movies", "#Cinema"],
    "anime": ["#Anime", "#Manga"],
    "ai_creators": ["#AI", "#Tech"],
}

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
    "instagram": 5,  # 2 base + 3 topic
    "youtube": 0,  # YouTube uses tags in description, not hashtags
    "twitter": 2,  # Less is more on X
    "facebook": 3,  # Moderate
    "threads": 3,  # Similar to IG but fewer
    "tiktok": 5,  # Similar to IG
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
            "why",
            "what",
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

    # 1. Niche base tags (always first)
    base = _NICHE_BASE.get(niche_id, ["#Content"])
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
