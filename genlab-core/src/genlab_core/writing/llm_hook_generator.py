"""Generate story-specific hooks via Claude Haiku.

Called by niche hook strategies when ANTHROPIC_API_KEY is set.
Returns None on any failure — callers fall back to template-based hooks.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_BANNED_PHRASES = [
    "this changes everything",
    "nobody saw this coming",
    "the community is going wild",
    "players need to see this",
    "making waves right now",
    "something big happened",
    "everyone is talking about",
    "you won't believe",
    "cinema is back",
    "no more excuses",
    "must watch",
    "this is what clutch looks like",
    "the internet is losing it",
    "fans are not ready",
    "just broke the internet",
]

NICHE_STYLE = {
    "sports": {
        "account": "ClutchWire",
        "voice": "electrifying sports fan energy, stats-aware, conversational",
        "audience": "sports fans aged 18-35",
        "example_good": "Jokic just dropped 40 in a must-win Game 7",
        "example_bad": "This player just changed everything",
    },
    "movies": {
        "account": "SpliceReel",
        "voice": "cinephile but accessible, enthusiastic about craft and storytelling",
        "audience": "movie fans aged 18-40",
        "example_good": "The Brutalist earned every minute of its 3-hour runtime",
        "example_bad": "Cinema is back and this film is proof",
    },
    "anime": {
        "account": "FrameDrift",
        "voice": "passionate otaku energy, references anime culture, emotional reactions",
        "audience": "anime fans aged 16-30",
        "example_good": "Gojo's return in JJK S3 just broke Crunchyroll",
        "example_bad": "This anime is about to blow up",
    },
    "gaming": {
        "account": "CriticalRush",
        "voice": "hype gamer energy, community-insider, uses gaming slang naturally",
        "audience": "gamers aged 16-30",
        "example_good": "Elden Ring Nightreign sold 5M copies in 72 hours",
        "example_bad": "Players need to see this",
    },
}


def generate_hook(
    story: dict[str, Any],
    niche_id: str,
    used_hooks: set[str] | None = None,
) -> str | None:
    """Generate a story-specific hook via Claude Haiku.

    Returns None if:
    - ANTHROPIC_API_KEY not set
    - anthropic not installed
    - API call fails
    - Generated hook is in used_hooks
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    try:
        import anthropic
    except ImportError:
        logger.debug("anthropic not installed — skipping LLM hook generation")
        return None

    style = NICHE_STYLE.get(niche_id, NICHE_STYLE["gaming"])
    title = story.get("title", "")
    summary = (story.get("summary", "") or "")[:300]

    if not title:
        return None

    banned_text = "\n".join(f"  - {p}" for p in _BANNED_PHRASES)
    used_text = ""
    if used_hooks:
        used_text = (
            "\n\nThese hooks are ALREADY USED — write something different:\n"
            + "\n".join(f"  - {h}" for h in list(used_hooks)[-5:])
        )

    system = (
        f"You write viral hooks for {style['account']}, a short-form video brand.\n"
        f"Voice: {style['voice']}\n"
        f"Audience: {style['audience']}\n\n"
        "A hook is the text overlay on a trending video reel. It must:\n"
        "- Be 20-60 characters (aim for 40-50)\n"
        "- Reference something SPECIFIC from the story (a name, team, title, event)\n"
        "- Create curiosity or emotional reaction\n"
        "- Contain at least one proper noun from the story\n\n"
        f"GOOD example: \"{style['example_good']}\"\n"
        f"BAD example: \"{style['example_bad']}\"\n\n"
        "BANNED phrases (never use these or variations):\n"
        f"{banned_text}\n\n"
        "No news language: BREAKING:, JUST IN:, announces, reveals\n"
        "No markdown. No quotes around your answer.\n"
        "Write ONE hook. Nothing else."
    )

    user = f"Story: {title}\nSummary: {summary}" + used_text

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=80,
            temperature=0.9,
            messages=[
                {"role": "user", "content": user},
            ],
            system=system,
        )

        hook = response.content[0].text.strip().strip('"').strip("'")

        # Normalize smart quotes to ASCII equivalents (prevents FFmpeg drawtext issues)
        hook = hook.replace("\u2019", "'").replace("\u2018", "'")
        hook = hook.replace("\u201c", '"').replace("\u201d", '"')

        # Enforce length
        if len(hook) > 60:
            hook = hook[:57].rsplit(" ", 1)[0] + "..."

        # Check banned phrases
        hook_lower = hook.lower()
        for banned in _BANNED_PHRASES:
            if banned in hook_lower:
                logger.debug("LLM hook contained banned phrase: %s", hook)
                return None

        # Check dedup
        if used_hooks and hook_lower in used_hooks:
            logger.debug("LLM hook already used: %s", hook)
            return None

        logger.info("[%s] LLM hook: %s", niche_id, hook)
        return hook

    except Exception as exc:
        logger.warning("[%s] LLM hook generation failed: %s", niche_id, exc)
        return None
