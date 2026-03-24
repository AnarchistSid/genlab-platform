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
    # Generic hype
    "this changes everything",
    "changed everything",
    "nobody saw this coming",
    "nobody expected",
    "nobody saw them",
    "the community is going wild",
    "community is losing it",
    "players need to see this",
    "making waves right now",
    "something big happened",
    "everyone is talking about",
    "you won't believe",
    "no more excuses",
    "must watch",
    "the internet is losing it",
    "fans are not ready",
    "just broke the internet",
    "we just witnessed",
    "about to blow up",
    # Sports-specific
    "this is what clutch looks like",
    "the moment this player",
    "the rivalry continues",
    "the trade that changes",
    "a record that might never",
    "ice in their veins",
    "this player just changed",
    # Movies-specific
    "cinema is back",
    "how did they even film",
    # AI-specific
    "someone just made this",
    "this is an ai video",
    "ai just changed",
    # Anime-specific
    "the anime community",
    "anime fans aren't ready",
    # Over-used patterns
    "chaos erupted",
    "went nuclear",
    "absolutely unhinged",
    "are losing it rn",
    "just became unmissable",
    "hit different",
]

NICHE_STYLE = {
    "sports": {
        "account": "ClutchWire",
        "voice": "electrifying sports fan energy, stats-aware, conversational",
        "audience": "sports fans aged 18-35",
        "example_good": "Jokic just dropped 40 in a must-win Game 7",
        "example_bad": "This player just changed everything",
        "top_hooks": [
            "Seahawks betting BIG on their young stars",
            "Rohit just did something CSK didn't see coming",
            "Nicol just went OFF about that missed pen call",
            "Buzelis just dropped 29 on Memphis",
            "Nationals just flipped the infield switch",
        ],
    },
    "movies": {
        "account": "SpliceReel",
        "voice": "cinephile but accessible, enthusiastic about craft and storytelling",
        "audience": "movie fans aged 18-40",
        "example_good": "The Brutalist earned every minute of its 3-hour runtime",
        "example_bad": "Cinema is back and this film is proof",
        "top_hooks": [
            "New spy thriller just dropped and it's giving Atomic Blonde",
            "Cillian Murphy's back and Shelby family chaos just hit",
            "Brain-dead patient suddenly communicates",
            "Pink Floyd's 1972 Pompeii film just got the 4K treatment",
            "Harriet's spy network just went viral",
        ],
    },
    "anime": {
        "account": "FrameDrift",
        "voice": "passionate otaku energy, references anime culture, emotional reactions",
        "audience": "anime fans aged 16-30",
        "example_good": "Gojo's return in JJK S3 just broke Crunchyroll",
        "example_bad": "This anime is about to blow up",
        "top_hooks": [
            "Subaru just broke in ways we didn't expect",
            "Witch Hat Atelier just got animated and it's GORGEOUS",
            "Tenshi-sama Season 2 just hit different",
            "Needy Girl Overdose anime adaptation just got real",
            "Subaru's suffering just entered a new dimension",
        ],
    },
    "gaming": {
        "account": "CriticalRush",
        "voice": "hype gamer energy, community-insider, uses gaming slang naturally",
        "audience": "gamers aged 16-30",
        "example_good": "Elden Ring Nightreign sold 5M copies in 72 hours",
        "example_bad": "Players need to see this",
        "top_hooks": [
            "VALORANT players are losing it rn",
            "Minecraft Dungeons 2 is actually happening",
            "Crimson Desert hit top 3 instantly",
            "Minecraft's going physical in 2027",
            "These clips broke the internet today",
        ],
    },
    "ai_creators": {
        "account": "BlackboxBrief",
        "voice": "tech-savvy creator shocked by what AI can do, accessible urgency",
        "audience": "tech-curious people aged 22-45",
        "example_good": "Sora just generated a full Fox News broadcast",
        "example_bad": "Someone just made this with AI",
        "top_hooks": [
            "AI video generation just hit a wall we didn't know existed",
            "AI just made a full cinematic film nobody expected",
            "AI just replaced my entire management layer",
            "This guy built his entire Product Hunt launch in AI",
            "A short AI-generated sci-fi demo - interactive film",
        ],
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
            "\n\nThese hooks are ALREADY USED — write something COMPLETELY different "
            "(different structure, different words, different angle):\n"
            + "\n".join(f"  - {h}" for h in list(used_hooks)[-15:])
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

    # Add few-shot examples from top performers
    top_hooks = style.get("top_hooks", [])
    if top_hooks:
        examples_text = "\n".join(f"  {i+1}. \"{h}\"" for i, h in enumerate(top_hooks))
        system += (
            "\n\nTOP PERFORMING HOOKS (match this energy and specificity):\n"
            f"{examples_text}\n"
        )

    user = f"Story: {title}\nSummary: {summary}" + used_text

    # Generate 3 candidates and pick the best
    used_lower = {h.lower() for h in used_hooks} if used_hooks else set()
    candidates = []
    try:
        client = anthropic.Anthropic(api_key=api_key)
    except Exception as exc:
        logger.warning("[%s] LLM client init failed: %s", niche_id, exc)
        return None

    for _ in range(3):
        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=80,
                temperature=0.7,
                messages=[{"role": "user", "content": user}],
                system=system,
            )
            hook = response.content[0].text.strip().strip('"').strip("'")
            hook = hook.replace("\u2019", "'").replace("\u2018", "'")
            hook = hook.replace("\u201c", '"').replace("\u201d", '"')
            if len(hook) > 60:
                hook = hook[:57].rsplit(" ", 1)[0] + "..."
            if len(hook) < 15:
                continue
            if used_lower and hook.lower() in used_lower:
                continue
            if any(bp.lower() in hook.lower() for bp in _BANNED_PHRASES):
                continue
            candidates.append(hook)
        except Exception as exc:
            logger.debug("Hook candidate generation failed: %s", exc)

    if not candidates:
        return None

    # Score candidates and pick the best
    if len(candidates) == 1:
        best = candidates[0]
    else:
        try:
            from genlab_core.learning.hook_features import build_feature_vector

            scored = []
            for h in candidates:
                feats = build_feature_vector(h)
                # Simple scoring: prefer longer hooks with questions, numbers, and superlatives
                score = (
                    feats.get("word_count", 0) * 0.1
                    + feats.get("has_question", 0) * 2.0
                    + feats.get("has_number", 0) * 1.5
                    + feats.get("has_superlative", 0) * 1.0
                    + feats.get("unique_word_ratio", 0) * 1.0
                )
                scored.append((h, score))
            scored.sort(key=lambda x: x[1], reverse=True)
            best = scored[0][0]
        except Exception:
            # If scoring fails, return the first candidate
            best = candidates[0]

    logger.info("[%s] LLM hook: %s", niche_id, best)
    return best


def generate_platform_hooks(
    story: dict[str, Any],
    niche_id: str,
    base_hook: str,
    used_hooks: set[str] | None = None,
) -> dict[str, str]:
    """Generate platform-specific hook variants from a base hook.

    Returns {"instagram": "...", "youtube": "...", "twitter": "...", "facebook": "..."}.
    Falls back to base_hook for all platforms if LLM call fails.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {p: base_hook for p in ("instagram", "youtube", "twitter", "facebook")}

    try:
        import anthropic
    except ImportError:
        return {p: base_hook for p in ("instagram", "youtube", "twitter", "facebook")}

    title = story.get("title", "")

    system = (
        "Adapt this hook for different social media platforms.\n"
        "Each variant must reference the same story but match the platform's style:\n"
        "- Instagram: emotional, scroll-stopping, 40-50 chars\n"
        "- YouTube: question that creates curiosity, \u226440 chars (this is the video title)\n"
        "- Twitter/X: hot take, conversational, provocative, \u226460 chars\n"
        "- Facebook: shareable, asks a question or makes a bold claim, \u226460 chars\n\n"
        "Respond EXACTLY in this format (one per line):\n"
        "instagram: <hook>\n"
        "youtube: <hook>\n"
        "twitter: <hook>\n"
        "facebook: <hook>"
    )

    user = f"Base hook: {base_hook}\nStory: {title}"

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            temperature=0.6,
            messages=[{"role": "user", "content": user}],
            system=system,
        )

        text = response.content[0].text.strip()
        result = {}
        for line in text.split("\n"):
            line = line.strip()
            for platform in ("instagram", "youtube", "twitter", "facebook"):
                if line.lower().startswith(f"{platform}:"):
                    hook = line.split(":", 1)[1].strip().strip('"').strip("'")
                    if 10 <= len(hook) <= 60:
                        result[platform] = hook

        # Fill missing platforms with base_hook
        for p in ("instagram", "youtube", "twitter", "facebook"):
            result.setdefault(p, base_hook)

        return result
    except Exception as exc:
        logger.debug("Platform hook generation failed: %s", exc)
        return {p: base_hook for p in ("instagram", "youtube", "twitter", "facebook")}
