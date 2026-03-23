"""Write platform-specific content around a trending video.

This replaces the text-story-based content writer for video-first channels.
Input: a TrendingVideo dict with title, channel, stats, description.
Output: hook, instagram_caption, twitter_content, youtube_content, facebook_content.

All content references what's actually IN the video — not generic templates.
"""

from __future__ import annotations

import json
import logging
import random
import re
from typing import Any

from genlab_core.writing.llm_hook_generator import _BANNED_PHRASES

logger = logging.getLogger(__name__)

# Channel handles — keyed by niche_id. Overridable via niche_config.channel_handle.
_NICHE_HANDLES: dict[str, str] = {
    "gaming": "@CriticalRush",
    "sports": "@ClutchWire",
    "movies": "@SpliceReel",
    "anime": "@FrameDrift",
    "ai_creators": "@BlackboxBrief",
}

NICHE_VOICE: dict[str, dict[str, Any]] = {
    "gaming": {
        "account": _NICHE_HANDLES["gaming"],
        "style": (
            "You sound like a Twitch streamer reacting live. High energy, "
            "niche slang (goated, no cap, W, L, clutch, broken). React to "
            "the moment FIRST, explain second. Never sound like a journalist."
        ),
        "audience": "gamers aged 16-30",
        "ctas": [
            "drop your take below 👇", "who else caught this?",
            "tag someone who mains this", "thoughts? 💀",
            "agree or disagree?", "name a better play 🎮",
        ],
        "hashtags": ["#Gaming", "#Gamer", "#GamingClips", "#VideoGames"],
    },
    "sports": {
        "account": _NICHE_HANDLES["sports"],
        "style": (
            "You sound like the most passionate sports fan in the group chat. "
            "Short, punchy, trash-talk energy. React to the moment, use specific "
            "player names and stats. Never write a headline — write a reaction."
        ),
        "audience": "sports fans aged 18-35",
        "ctas": [
            "Comment your hot take 👇", "Did you see this live?",
            "Who's your pick?", "Rate this play 1-10",
            "Agree or nah?", "Would you start them?",
        ],
        "hashtags": ["#Sports", "#SportsHighlights", "#Clutch"],
    },
    "movies": {
        "account": _NICHE_HANDLES["movies"],
        "style": (
            "You sound like a film-obsessed friend texting at midnight. "
            "Hot takes, genuine excitement or outrage, reference specific "
            "scenes/actors/directors. Never write a review — write a reaction."
        ),
        "audience": "movie fans aged 18-40",
        "ctas": [
            "Have you seen this yet?", "Watch or skip?",
            "Best film of the year?", "What do you think 👇",
            "Rate this trailer 1-10", "Overhyped or underrated?",
        ],
        "hashtags": ["#Movies", "#Film", "#Cinema", "#Trailer"],
    },
    "anime": {
        "account": _NICHE_HANDLES["anime"],
        "style": (
            "You sound like the most invested person in the anime Discord. "
            "Peak/mid/goated vocabulary, emotional reaction first. Debate "
            "energy. Reference specific shows, characters, studios. "
            "Never write a press release — write a fan reaction."
        ),
        "audience": "anime fans aged 16-30",
        "ctas": [
            "Are you watching this?", "W or L take? 👇",
            "Peak or mid?", "Rate this season so far",
            "Caught up yet?", "Sub or dub?",
        ],
        "hashtags": ["#Anime", "#Manga", "#Otaku", "#AnimeFan"],
    },
    "ai_creators": {
        "account": _NICHE_HANDLES["ai_creators"],
        "style": (
            "You sound like a tech-savvy person genuinely shocked by what AI "
            "can do. Accessible, slightly conspiratorial, urgent. Reference "
            "specific tools, demos, and capabilities. Never write a press release."
        ),
        "audience": "tech-curious people aged 22-45",
        "ctas": [
            "What do you think?", "Would you use this?",
            "Better than the original?", "Share your results 👀",
            "Try this yourself", "Thoughts? 👇",
        ],
        "hashtags": ["#AI", "#ArtificialIntelligence", "#Tech", "#MachineLearning"],
    },
}


def write_video_content(
    video: dict,
    niche_id: str,
    llm_client: Any,
    existing_hooks: list[str] | None = None,
    extra_instructions: str = "",
) -> dict:
    """Generate platform-specific content for a trending video.

    Args:
        video: TrendingVideo.to_dict() or equivalent with title, channel_name,
               view_count, view_velocity, description_snippet, tags, video_id
        niche_id: gaming, sports, movies, anime, ai_creators
        llm_client: Object with .complete(system, user, max_tokens, temperature)
        existing_hooks: Already-used hooks to avoid duplicates
        extra_instructions: Optional niche-specific instructions (banned phrases,
            tone guidance, examples) appended to the system prompt.

    Returns:
        Dict with: hook, instagram_caption, twitter_content,
                   youtube_content, facebook_content, threads_content
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
        "STRICT CHARACTER LIMITS (enforced — content will be truncated if exceeded):\n"
        "- hook: ≤60 characters. Story-specific, creates curiosity. NO generic phrases.\n"
        "- instagram_caption: EXACTLY 150-180 characters of body text, then a line break,\n"
        "  then a CTA (e.g. 'Follow for daily sports'), then a line break,\n"
        "  then EXACTLY 3-5 relevant hashtags. Total must be under 200 chars.\n"
        "- twitter_content: ≤280 chars. Punchy, conversational. NO external links.\n"
        "- youtube_content: Question format, ≤40 characters total.\n"
        "- facebook_content: 200-300 chars. Ask an engaging question.\n"
        "- threads_content: 150-300 chars. Text-first, conversational, opinion-forward.\n"
        "  No hashtags needed. Write like a hot take in a group chat.\n\n"
        + (
            "These hooks are already used — DO NOT duplicate:\n"
            f"{existing_hooks_text}\n\n"
            if existing_hooks_text else ""
        )
        + (f"{extra_instructions}\n\n" if extra_instructions else "")
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
        "hook, instagram_caption, twitter_content, youtube_content, facebook_content, threads_content"
    )

    try:
        response = llm_client.complete(
            system=system,
            user=user,
            max_tokens=800,
            temperature=0.8,
        )

        # Strip markdown code fences if present
        clean = re.sub(r"```(?:json)?|```", "", response).strip()
        content = json.loads(clean)

        # Normalize smart quotes to ASCII equivalents (prevents FFmpeg drawtext issues)
        hook = content.get("hook", "")
        hook = hook.replace("\u2019", "'").replace("\u2018", "'")
        hook = hook.replace("\u201c", '"').replace("\u201d", '"')
        content["hook"] = hook

        # Validate hook length
        if len(hook) > 60:
            hook = hook[:57].rsplit(" ", 1)[0] + "..."
            content["hook"] = hook

        # Reject hooks containing banned generic phrases
        hook_lower = hook.lower()
        if any(phrase in hook_lower for phrase in _BANNED_PHRASES):
            logger.warning(
                "[%s] Rejected banned hook: %s", niche_id, hook[:60],
            )
            content["hook"] = ""

        # ── Enforce Instagram caption standards ──────────────
        ig = content.get("instagram_caption", "")
        if ig:
            # Split caption body from hashtags
            ig_parts = ig.split("\n\n")
            body_parts = [p for p in ig_parts if not p.strip().startswith("#")]
            hash_parts = [p for p in ig_parts if p.strip().startswith("#")]
            body = "\n\n".join(body_parts).strip()

            # Enforce 3-5 hashtags (extract before body truncation)
            all_tags = re.findall(r"#\w+", " ".join(hash_parts) + " " + body)
            body = re.sub(r"\s*#\w+", "", body).strip()
            if len(all_tags) < 3:
                all_tags = list(voice["hashtags"][:4])
            elif len(all_tags) > 5:
                all_tags = all_tags[:5]

            # Ensure CTA
            ctas = voice.get("ctas", [])
            has_cta = any(cta.lower() in body.lower() for cta in ctas)
            cta_text = "" if has_cta else random.choice(ctas[:3]).capitalize() if ctas else ""

            # Calculate space budget: 200 total - hashtags - CTA - newlines
            tags_str = " ".join(all_tags)
            overhead = len(tags_str) + len(cta_text) + 4  # 4 = two "\n\n" separators
            body_budget = max(20, 200 - overhead)
            if len(body) > body_budget:
                body = body[:body_budget - 3].rsplit(" ", 1)[0] + "..."

            # Reassemble: body + CTA + hashtags (total ≤200 chars)
            parts = [body]
            if cta_text:
                parts.append(cta_text)
            parts.append(tags_str)
            ig = "\n\n".join(parts)
            content["instagram_caption"] = ig

        # ── Enforce Twitter ≤280 chars ───────────────────────
        tw = content.get("twitter_content", "")
        if tw and len(tw) > 280:
            tw = tw[:277].rsplit(" ", 1)[0] + "..."
            content["twitter_content"] = tw

        # ── Enforce YouTube title ≤40 chars + question format ─
        yt = content.get("youtube_content", "")
        if yt and len(yt) > 40:
            yt = yt[:37].rsplit(" ", 1)[0] + "?"
            content["youtube_content"] = yt

        # ── Enforce Facebook 200-300 chars ───────────────────
        fb = content.get("facebook_content", "")
        if fb and len(fb) > 300:
            fb = fb[:297].rsplit(" ", 1)[0] + "..."
            content["facebook_content"] = fb

        # ── Enforce Threads 150-300 chars ──────────────────
        th = content.get("threads_content", "")
        if th and len(th) > 300:
            th = th[:297].rsplit(" ", 1)[0] + "..."
            content["threads_content"] = th

        # ── Fill missing fields from fallback ────────────────
        title = video.get("title", "")
        if not content.get("instagram_caption"):
            content["instagram_caption"] = f"{title[:150]}\n\n{voice['hashtags'][0]} {voice['hashtags'][1]}"
        if not content.get("twitter_content"):
            content["twitter_content"] = title[:280]
        if not content.get("youtube_content"):
            content["youtube_content"] = (title[:37] + "?") if len(title) > 37 else title
        if not content.get("facebook_content"):
            content["facebook_content"] = f"{title[:200]} What do you think?"
        if not content.get("threads_content"):
            content["threads_content"] = (title[:297] + "...") if len(title) > 300 else title

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
            "threads_content": title[:300],
        }
