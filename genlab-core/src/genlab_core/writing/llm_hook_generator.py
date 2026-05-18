"""Generate story-specific hooks via Claude Haiku.

Called by niche hook strategies when ANTHROPIC_API_KEY is set.
Returns None on any failure — callers fall back to template-based hooks.

Bandit-driven style hint (2026-05-17):
  When ``bandit_arms`` contains rows like ``style:question`` for a
  niche, ``pick_hook_style`` Thompson-samples among them and the
  chosen style is injected into the LLM system prompt as a one-line
  hint. The story's ``hook_style`` field records which arm was used
  so the feedback loop can attribute reward (extension to multi-arm
  updates in metric_collector is tracked separately).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)


# Regex patterns banned in addition to ``_BANNED_PHRASES`` exact-match list.
#
# The "X just <verb>" template — 80-90% saturated across published hooks
# in 4 of 5 niches (audit 2026-05-18).  CLAUDE.md flagged this template
# pattern as forbidden ("No generic templates") but the exact-phrase
# banned list only catches specific suffixes ("nobody saw it coming"
# etc.).  This pattern catches the lead-in itself so the LLM is forced
# to find a different opening structure.
#
# Match condition: "just" appears within the first 4 words, followed by
# any word — i.e. the hook OPENS with the "<entity> just <action>"
# template.  A hook with "just" later in the sentence is fine.
_BANNED_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^(\S+\s+){0,3}just\s+\w+", re.IGNORECASE),
]


# Hook style taxonomy. Each style maps to a one-line instruction that
# becomes part of the LLM system prompt. Adding a style here is a
# breaking change for the bandit posterior (new arms need to be
# seeded) — keep the set stable across deploys.
_HOOK_STYLES: dict[str, str] = {
    "question": (
        "Phrase the hook as a direct question to the viewer. Make them "
        "want to click for the answer."
    ),
    "bold_claim": (
        "Start with a confident, declarative statement. Use a "
        "superlative or absolute (most, never, only)."
    ),
    "controversy": (
        "Frame the hook around a tension, dispute, or opinion that "
        "splits viewers. Avoid neutral language."
    ),
    "revelation": (
        "Frame the hook as if revealing a hidden truth or behind-the-"
        "scenes detail. Use 'why', 'how', or 'what nobody told you'."
    ),
    "comparison": (
        "Frame the hook as a direct comparison or contrast — X vs Y, "
        "before vs after, expected vs actual."
    ),
}


def pick_hook_style(niche_id: str) -> str | None:
    """Thompson-sample a hook style for ``niche_id``.

    Reads ``bandit_arms`` rows whose ``arm_id`` starts with ``style:``
    and draws Beta(alpha, beta) for each. Returns the style with the
    highest sample, stripped of the ``style:`` prefix.

    Returns None if:
      - No style arms exist for this niche (cold-start scenario).
      - BacklogClient creation fails.
      - Any unexpected error occurs.

    None is the well-defined "no bandit influence" signal — the caller
    proceeds with a vanilla LLM prompt.
    """
    try:
        from genlab_core.http.backlog_client import BacklogClient
        from genlab_core.learning.arm_loader import load_all_arms
    except ImportError:
        return None

    try:
        client = BacklogClient()
    except Exception:
        return None

    proxy = getattr(client, "bandit_arms", None)
    if proxy is None:
        return None

    arms = load_all_arms(proxy, niche_id)
    # arm_id format is "style:{niche}:{name}" — bandit_arms has
    # UNIQUE(arm_id) so the niche segment is required even though it
    # duplicates the niche_id column. Strip both prefix levels.
    style_arms: dict[str, tuple[float, float]] = {}
    legacy_prefix = "style:"  # for backwards-compat with the simpler form
    niche_prefix = f"style:{niche_id}:"
    for arm_id, (alpha, beta) in arms.items():
        if arm_id.startswith(niche_prefix):
            name = arm_id[len(niche_prefix):]
        elif arm_id.startswith(legacy_prefix) and ":" not in arm_id[len(legacy_prefix):]:
            # Legacy "style:question" form (single-niche deployment)
            name = arm_id[len(legacy_prefix):]
        else:
            continue
        style_arms[name] = (alpha, beta)
    if not style_arms:
        return None

    import random
    best_sample = -1.0
    best_style: str | None = None
    for name, (alpha, beta) in style_arms.items():
        a = alpha if alpha > 0 else 1.0
        b = beta if beta > 0 else 1.0
        try:
            sample = random.betavariate(a, b)
        except (ValueError, OverflowError):
            sample = 0.5
        if sample > best_sample:
            best_sample = sample
            best_style = name

    if best_style not in _HOOK_STYLES:
        # Bandit sampled an unrecognized arm name. Don't inject a
        # malformed hint; treat as cold-start.
        return None
    return best_style

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
    return_style: bool = False,
) -> str | None | tuple[str | None, str | None]:
    """Generate a story-specific hook via Claude Haiku.

    Returns None if:
    - ANTHROPIC_API_KEY not set
    - anthropic not installed
    - API call fails
    - Generated hook is in used_hooks

    Args:
        return_style: When True, returns ``(hook, style_name)`` so the
            caller can record which bandit arm was used. ``style_name``
            is None when no bandit influence was applied (cold-start,
            no arms, error). Default False preserves the legacy
            ``str | None`` return for existing callers.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return (None, None) if return_style else None

    try:
        import anthropic
    except ImportError:
        logger.debug("anthropic not installed — skipping LLM hook generation")
        return (None, None) if return_style else None

    style = NICHE_STYLE.get(niche_id, NICHE_STYLE["gaming"])
    title = story.get("title", "")
    summary = (story.get("summary", "") or "")[:300]

    if not title:
        return (None, None) if return_style else None

    # Bandit-driven style hint — only takes effect when style:* arms
    # have been seeded for this niche. Cold-start (no arms) silently
    # falls through to the vanilla prompt.
    chosen_style = pick_hook_style(niche_id)
    style_hint = ""
    if chosen_style and chosen_style in _HOOK_STYLES:
        style_hint = (
            f"\n\nSTYLE TARGET: {_HOOK_STYLES[chosen_style]}"
        )

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
        "BANNED OPENING TEMPLATE: Do NOT start the hook with "
        "'<entity> just <verb>' (e.g. 'Lakers just got punched', "
        "'SGA just won MVP', 'Anthropic just dropped a model'). "
        "Find a different opening — question, comparison, revelation, "
        "or contextual setup.\n\n"
        "No news language: BREAKING:, JUST IN:, announces, reveals\n"
        "No markdown. No quotes around your answer.\n"
        "Write ONE hook. Nothing else."
        f"{style_hint}"
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
        return (None, chosen_style) if return_style else None

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
            # Also reject the "X just <verb>" lead-in template
            if any(pat.search(hook) for pat in _BANNED_PATTERNS):
                continue
            candidates.append(hook)
        except Exception as exc:
            logger.debug("Hook candidate generation failed: %s", exc)

    if not candidates:
        return (None, chosen_style) if return_style else None

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

    logger.info(
        "[%s] LLM hook: %s (style=%s)",
        niche_id, best, chosen_style or "none",
    )
    if return_style:
        return (best, chosen_style)
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
