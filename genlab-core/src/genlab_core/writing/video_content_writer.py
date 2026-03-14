"""Write platform-specific content around a trending video.

This replaces the text-story-based content writer for video-first channels.
Input: a TrendingVideo dict with title, channel, stats, description.
Output: hook, instagram_caption, twitter_content, youtube_content, facebook_content.

All content references what's actually IN the video — not generic templates.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

NICHE_VOICE: Dict[str, Dict[str, Any]] = {
    "gaming": {
        "account": "@CriticalRush",
        "style": "hype, community-focused, uses gaming slang naturally (goated, no cap, W)",
        "audience": "gamers aged 16-30",
        "ctas": [
            "follow for daily gaming", "drop your take below",
            "who else saw this?", "tag a gamer who needs to see this",
        ],
        "hashtags": ["#Gaming", "#Gamer", "#Games", "#VideoGames", "#GamingClips"],
    },
    "sports": {
        "account": "@ClutchWire",
        "style": "electrifying, fan-energy, conversational, stats-aware",
        "audience": "sports fans aged 18-35",
        "ctas": [
            "follow for daily sports", "comment your hot take",
            "who saw this coming?", "tag a sports fan",
        ],
        "hashtags": ["#Sports", "#SportsHighlights", "#Athlete", "#SportsClips"],
    },
    "movies": {
        "account": "@SpliceReel",
        "style": "cinephile but accessible, enthusiastic, references film culture",
        "audience": "movie fans aged 18-40",
        "ctas": [
            "follow for daily cinema", "have you seen this yet?",
            "watch or skip?", "save this for your watchlist",
        ],
        "hashtags": ["#Movies", "#Film", "#Cinema", "#FilmTwitter", "#Trailer"],
    },
    "anime": {
        "account": "@FrameDrift",
        "style": "passionate otaku energy, references anime culture, emotional reactions",
        "audience": "anime fans aged 16-30",
        "ctas": [
            "follow for daily anime", "are you watching this?",
            "tag your anime friend", "save this for later",
        ],
        "hashtags": ["#Anime", "#Manga", "#Otaku", "#AnimeClips", "#AnimeFan"],
    },
    "ai_news": {
        "account": "@BlackboxBrief",
        "style": "informed but accessible, explains tech simply, thought-provoking",
        "audience": "tech-curious people aged 22-45",
        "ctas": [
            "follow for daily AI", "what do you think?",
            "save for later", "follow for more AI updates",
        ],
        "hashtags": ["#AI", "#ArtificialIntelligence", "#Tech", "#MachineLearning"],
    },
}


def write_video_content(
    video: dict,
    niche_id: str,
    llm_client: Any,
    existing_hooks: Optional[list[str]] = None,
) -> dict:
    """Generate platform-specific content for a trending video.

    Args:
        video: TrendingVideo.to_dict() or equivalent with title, channel_name,
               view_count, view_velocity, description_snippet, tags, video_id
        niche_id: gaming, sports, movies, anime, ai_news
        llm_client: Object with .complete(system, user, max_tokens, temperature)
        existing_hooks: Already-used hooks to avoid duplicates

    Returns:
        Dict with: hook, instagram_caption, twitter_content,
                   youtube_content, facebook_content
    """
    voice = NICHE_VOICE.get(niche_id, NICHE_VOICE["gaming"])
    existing_hooks_text = "\n".join(f"  - {h}" for h in (existing_hooks or [])[-5:])

    age_hours = video.get("age_hours", 1)
    if not age_hours:
        # Compute from view_count / view_velocity if available
        vel = video.get("view_velocity", 1)
        age_hours = video.get("view_count", 0) / vel if vel else 1

    system = (
        f"You write viral short-form social media content for {voice['account']}.\n"
        f"Style: {voice['style']}\n"
        f"Audience: {voice['audience']}\n\n"
        "You are writing content FOR a video that's already going viral.\n"
        "Reference what's actually happening in the video — be specific.\n"
        "Never use generic templates like \"something big happened\".\n\n"
        "RULES:\n"
        "- Hook: ≤60 characters, story-specific, creates curiosity\n"
        "- Instagram: 150-200 chars + 3-5 relevant hashtags + CTA\n"
        "- Twitter: ≤280 chars, NO external links\n"
        "- YouTube title: question format, ≤40 characters\n"
        "- Facebook: 200-300 chars, engaging question\n\n"
        + (
            "These hooks are already used — DO NOT duplicate:\n"
            f"{existing_hooks_text}\n\n"
            if existing_hooks_text else ""
        )
        + "Respond ONLY with valid JSON. No markdown, no explanation."
    )

    user = (
        f"Video title: {video.get('title', '')}\n"
        f"Channel: {video.get('channel_name', '')}\n"
        f"Views: {video.get('view_count', 0):,} in ~{age_hours:.0f}h "
        f"({video.get('view_velocity', 0):.0f} views/hr)\n"
        f"Tags: {', '.join(video.get('tags', [])[:8])}\n"
        f"Description: {video.get('description_snippet', '')}\n\n"
        "Write content for this specific video. Return JSON with keys:\n"
        "hook, instagram_caption, twitter_content, youtube_content, facebook_content"
    )

    try:
        response = llm_client.complete(
            system=system,
            user=user,
            max_tokens=600,
            temperature=0.8,
        )

        # Strip markdown code fences if present
        clean = re.sub(r"```(?:json)?|```", "", response).strip()
        content = json.loads(clean)

        # Validate hook length
        hook = content.get("hook", "")
        if len(hook) > 60:
            hook = hook[:57].rsplit(" ", 1)[0] + "..."
            content["hook"] = hook

        # Ensure hashtags in instagram caption
        ig = content.get("instagram_caption", "")
        if ig and "#" not in ig:
            ig += f"\n\n{' '.join(voice['hashtags'][:4])}"
            content["instagram_caption"] = ig

        return content

    except Exception as e:
        logger.error("[%s] Content generation failed: %s", niche_id, e)
        # Fallback using video title — NOT a generic template
        title = video.get("title", "")
        channel = video.get("channel_name", "")
        return {
            "hook": title[:57] + "..." if len(title) > 60 else title,
            "instagram_caption": (
                f"{title}\n\nVia {channel}\n\n{' '.join(voice['hashtags'][:3])}"
            ),
            "twitter_content": title[:280],
            "youtube_content": title[:40],
            "facebook_content": f"{title} — what do you think?",
        }
