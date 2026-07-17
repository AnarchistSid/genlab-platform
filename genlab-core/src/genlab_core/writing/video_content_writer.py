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

# Text fields that ship to platforms and must read as sentence case (R-50).
# instagram_caption is included: to_sentence_case only touches sentence-initial
# letters, so the trailing CTA (already capitalized) and "#hashtags" are
# unaffected.
_SENTENCE_CASE_FIELDS = (
    "hook",
    "instagram_caption",
    "twitter_content",
    "youtube_content",
    "facebook_content",
    "threads_content",
)


# Prefixes that indicate the LLM refused the task and returned its
# preamble as the answer. Two forms observed in prod (2026-07-05):
#
#   anime      "I need to stop here and flag a critical issue. The..."
#   ai_creators "I need the Story title and Summary to write a hook for."
#
# Also covered: Anthropic's classic safety refusals ("I cannot", "I can't
# help", "I'm sorry", "I apologize", "I am unable") and context-request
# preambles ("I need the", "I don't have enough").
#
# Matched case-insensitively as PREFIXES only — a legitimate hook that
# happens to contain the phrase mid-sentence ("...why I cannot stop
# thinking about it") is not a refusal. Task #526 pin covers this.
# 2026-07-14: synced to rendering/pre_render_quality.py's superset.
# Prior drift: 5 entries only in rendering ("i need to flag", "i need
# more", "i don't have the", "i'm unable", "i'm afraid") meant the
# writer could emit a hook the render gate would then reject —
# wasting the LLM budget. Now the writer catches early. Parity is
# enforced by test_pre_render_quality_refusal_parity.py.
_LLM_REFUSAL_PREFIXES: tuple[str, ...] = (
    "i need to stop",
    "i need to flag",
    "i need to pause",  # 2026-07-17: 4 archived blueprints leaked past pre-gate
    "i need the",
    "i need more",
    "i don't have enough",
    "i don't have the",
    "i cannot",
    "i can't help",
    "i can't provide",
    "i can't write",
    "i can't access",  # 2026-07-17: movies blueprint 6e943894 leaked
    "i am unable",
    "i'm unable",
    "i'm sorry",
    "i apologize",
    "i'm afraid",
)


def _is_llm_refusal(text: str) -> bool:
    """True if ``text`` starts with a known LLM-refusal preamble.

    See :data:`_LLM_REFUSAL_PREFIXES`. Called from ``write_video_content``
    (hook validation) and downstream anywhere hook-shaped text needs a
    trust check.
    """
    if not text or not isinstance(text, str):
        return False
    lowered = text.strip().lower()
    return any(lowered.startswith(prefix) for prefix in _LLM_REFUSAL_PREFIXES)


def _apply_sentence_case(content: dict[str, Any]) -> None:
    """In-place sentence-case the shipped text fields of a content dict (R-50)."""
    from genlab_core.writing.text_case import to_sentence_case

    for field_name in _SENTENCE_CASE_FIELDS:
        val = content.get(field_name)
        if isinstance(val, str) and val:
            content[field_name] = to_sentence_case(val)


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
            "Drop your take below 👇",
            "Who else caught this?",
            "Tag someone who mains this",
            "Thoughts? 💀",
            "Agree or disagree?",
            "Name a better play 🎮",
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
            "Comment your hot take 👇",
            "Did you see this live?",
            "Who's your pick?",
            "Rate this play 1-10",
            "Agree or nah?",
            "Would you start them?",
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
            "Have you seen this yet?",
            "Watch or skip?",
            "Best film of the year?",
            "What do you think 👇",
            "Rate this trailer 1-10",
            "Overhyped or underrated?",
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
            "Are you watching this?",
            "W or L take? 👇",
            "Peak or mid?",
            "Rate this season so far",
            "Caught up yet?",
            "Sub or dub?",
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
            "Follow for daily AI drops",
            "Save this before it blows up",
            "Tag someone who needs to see this",
            "Drop your hot take below",
            "Which tool are you switching to?",
            "Try this and report back",
            "Name a better demo — we'll wait",
            "This changes everything and nobody's talking about it",
            "The future just got weird",
            "Share your results in the comments",
        ],
        "hashtags": ["#AI", "#ArtificialIntelligence", "#Tech", "#MachineLearning"],
    },
}


# Fields the writer must always populate. Used by
# _complete_and_parse_json to detect "valid JSON but missing required
# keys" responses and trigger a retry. See 2026-06-17 audit: anime/
# movies/sports had ~75% rate of LLM omitting instagram_caption +
# threads_content (the two most-constrained fields). PR #273 added
# downstream fallback; this set drives the upstream retry so we
# rarely need the fallback.
_REQUIRED_LLM_FIELDS: frozenset[str] = frozenset(
    {
        "hook",
        "instagram_caption",
        "twitter_content",
        "youtube_content",
        "facebook_content",
        "threads_content",
    }
)


def _complete_and_parse_json(
    llm_client: Any,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
    niche_id: str,
) -> dict:
    """Call the LLM and parse its JSON response, retrying once on parse
    failure OR on missing-required-field.

    Two recoverable failure modes:
    1. **Malformed JSON** — Claude occasionally emits unescaped
       quotes/colons inside string values. A retry with an explicit
       reminder of the parse error fixes the vast majority.
    2. **Valid JSON but missing required keys** — added 2026-06-17 to
       close the cascading-empty-IG bug. Anime/movies/sports LLM
       responses sometimes drop ``instagram_caption`` and/or
       ``threads_content`` when the model can't satisfy the tight
       length constraints. We re-prompt naming the specific missing
       fields rather than letting the empty values cascade through
       _adapt_instagram + inject_cta.

    On a second failure of either kind we let the error propagate so
    ``write_video_content`` falls through to its title-derived
    fallback content (a degraded but safe outcome).
    """
    last_err: Exception | None = None
    last_missing: list[str] = []
    for attempt in range(2):
        retry_user = user
        if attempt > 0:
            if last_missing:
                retry_user = (
                    f"{user}\n\n"
                    f"IMPORTANT: your previous response was valid JSON but "
                    f"OMITTED these required fields: {', '.join(last_missing)}.\n"
                    "These fields are REQUIRED and must have non-empty string\n"
                    "values. Re-generate with ALL required keys present, even\n"
                    "if the length targets are hard to satisfy exactly."
                )
            elif last_err is not None:
                retry_user = (
                    f"{user}\n\n"
                    f"IMPORTANT: your previous response was not valid JSON "
                    f"({type(last_err).__name__}: {last_err}). "
                    "Return ONLY a valid JSON object. Escape any quotes or colons "
                    "inside string values. No markdown, no prose, no code fences."
                )
        response = llm_client.complete(
            system=system,
            user=retry_user,
            max_tokens=max_tokens,
            temperature=temperature if attempt == 0 else max(0.2, temperature - 0.3),
        )
        clean = re.sub(r"```(?:json)?|```", "", response).strip()
        try:
            parsed = json.loads(clean)
        except json.JSONDecodeError as exc:
            last_err = exc
            last_missing = []
            if attempt == 0:
                logger.info(
                    "[%s] LLM JSON parse failed (attempt 1/2): %s — retrying",
                    niche_id,
                    exc,
                )
                continue
            raise

        # JSON parsed cleanly; verify required fields are present + non-empty.
        # Missing/empty fields trigger a re-prompt rather than silently
        # cascading into downstream fallbacks.
        missing = sorted(k for k in _REQUIRED_LLM_FIELDS if not str(parsed.get(k, "")).strip())
        if missing and attempt == 0:
            last_missing = missing
            last_err = None
            logger.info(
                "[%s] LLM omitted required fields %s (attempt 1/2) — retrying",
                niche_id,
                missing,
            )
            continue
        if missing:
            # Second attempt also missing fields — log WARN so we can
            # measure prompt regression over time, then return what we
            # have so the downstream fallback (base_writing PR #273)
            # produces something rather than crashing the pipeline.
            logger.warning(
                "[%s] LLM omitted required fields %s after retry — "
                "downstream fallback will fill from related fields",
                niche_id,
                missing,
            )
        return parsed


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

    # Bandit-driven hook style pick. Lives here (not in BaseHookStrategy)
    # because production runs the writing stage first: it produces the LLM
    # hook directly, then BaseHookStrategy short-circuits on
    # ``written_by == 'llm'`` and never invokes its own style picker. Without
    # this call, the ``style:*`` bandit arms never receive any signal — see
    # 2026-05-20 root cause analysis. None means cold-start / no arms seeded.
    from genlab_core.writing.llm_hook_generator import (
        _HOOK_STYLE_EXEMPLARS,
        _HOOK_STYLES,
        pick_hook_style,
    )

    chosen_style = pick_hook_style(niche_id)
    style_hint = ""
    if chosen_style and chosen_style in _HOOK_STYLES:
        # PR Intervention-3: strengthen from "STYLE TARGET" (hint) to
        # "STYLE MANDATE" (constraint) + 3 verbal exemplars. Turns the
        # writer's arm choice from a suggestion the LLM might drift from
        # into a specific verbal pattern it can mimic. Enables the
        # writer→bandit feedback loop: bandit's reward on `style:X`
        # posterior now reflects actual style-X hook performance rather
        # than "whatever the LLM felt like writing today."
        exemplars = _HOOK_STYLE_EXEMPLARS.get(chosen_style, [])
        exemplar_lines = "\n".join(f"    - {ex!r}" for ex in exemplars[:3])
        style_hint = (
            f"\nSTYLE MANDATE ({chosen_style}): {_HOOK_STYLES[chosen_style]}\n"
            f"  Verbal exemplars (mimic this pattern):\n{exemplar_lines}\n"
            f"  Your hook MUST embody this style — if you can't fit the\n"
            f"  story into this pattern naturally, return an empty hook\n"
            f"  to signal skip. Do NOT force a different style.\n"
        )

    # Task #628 (2026-07-09, roadmap E2): content-angle preference
    # hint. Symmetric to the ``STYLE MANDATE`` above but for the
    # content_type dimension. Soft steering — the writer keeps
    # editorial control if the story doesn't naturally fit the
    # bandit-preferred angle. Fail-open on any error (import,
    # backlog, empty niche) — the writer works exactly as before
    # when this returns None.
    content_angle_hint = ""
    try:
        from genlab_core.writing.content_type_hint import (
            format_content_angle_prompt,
            pick_content_type_hint,
        )

        picked = pick_content_type_hint(niche_id)
        if picked:
            content_angle_hint = format_content_angle_prompt(picked)
    except Exception as exc:
        logger.debug("[%s] content-angle hint injection skipped: %s", niche_id, exc)

    # PR Strategist-3: append operator-approved learning findings (top 5)
    # into the system prompt so the writer leans on causal patterns the
    # operator has explicitly validated. Fail-closed: if strategy_phase
    # can't be loaded, we just skip the injection — the writer works
    # exactly as before.
    findings_hint = ""
    try:
        from genlab_core.scheduling.strategy_phase import get_phase_config

        phase_cfg = get_phase_config(niche_id)
        if phase_cfg.active_findings:
            findings_text = "\n".join(f"  - {f}" for f in phase_cfg.active_findings[:5])
            findings_hint = f"\nOPERATOR-VALIDATED LEARNINGS (lean on these):\n{findings_text}\n"
    except Exception as exc:
        logger.debug("[%s] Strategist findings injection skipped: %s", niche_id, exc)

    # Layer 3 S2 (2026-07-17): series context injection. If the source
    # video title indicates part-of-a-series ("Part 3", "Episode 22",
    # "S03E11"), inject a SERIES CONTEXT section so the writer crafts
    # a hook that references the arc rather than treating the clip as
    # standalone. YT algorithm's #1 subscribe trigger per audit round 4.
    # Same fail-open pattern as content_angle_hint above.
    series_context_hint = ""
    try:
        from genlab_core.writing.series_detector import (
            detect_series,
            format_series_prompt_section,
        )

        series_info = detect_series(video)
        if series_info is not None:
            series_context_hint = format_series_prompt_section(series_info)
            logger.info(
                "[%s] series detected: %s part=%d/%d pattern=%s",
                niche_id,
                series_info.series_title,
                series_info.part_number,
                series_info.total_parts,
                series_info.detection_pattern,
            )
    except Exception as exc:
        logger.debug("[%s] series detection skipped: %s", niche_id, exc)

    age_hours = video.get("age_hours", 1)
    if not age_hours:
        # Compute from view_count / view_velocity if available
        vel = video.get("view_velocity", 1)
        age_hours = video.get("view_count", 0) / vel if vel else 1

    # Pick 3 CTAs from the niche's rotation and SHOW them to the LLM so it
    # uses natural ones instead of pattern-matching on whatever example we
    # put in the prompt. The three choices here become the LLM's menu.
    cta_menu = random.sample(voice["ctas"], min(3, len(voice["ctas"])))
    cta_menu_text = "\n".join(f'    - "{c}"' for c in cta_menu)

    system = (
        f"You write viral short-form social media content for {voice['account']}.\n"
        f"Voice: {voice['style']}\n"
        f"Audience: {voice['audience']}\n\n"
        "CORE PRINCIPLE: you are writing for a video that's already going viral.\n"
        "Your job is to make someone stop scrolling. Every word earns its place.\n"
        "Reference what's actually IN the video — specific tools, names, claims.\n"
        "If the video isn't relevant to the channel's niche, return an empty\n"
        "hook to signal skip. Do NOT force irrelevant content.\n"
        "\n"
        "HOOK RULES (≤60 chars, the single most important field):\n"
        "  - Create curiosity — don't resolve it. A hook makes someone NEED\n"
        "    to watch. A headline SUMMARIZES what happened.\n"
        "  - ✅ GOOD: 'Anthropic built a Claude so dangerous they won't release it'\n"
        "  - ✅ GOOD: 'The AI company everyone feared just got scared of itself'\n"
        "  - ✅ GOOD: 'This tool just made 3 million designers redundant'\n"
        "  - ❌ BAD:  'Claude just found a loophole' (resolves curiosity)\n"
        "  - ❌ BAD:  'New AI model released today' (generic headline)\n"
        "  - ❌ BAD:  'Google deleted them but footage lives forever' (too explained)\n"
        "  - Use concrete nouns from the source. NO vague phrases like\n"
        "    'something big', 'this could change everything', 'you won't believe'.\n"
        "  - NEVER start a hook with '[Company/Person] just [did something]'.\n"
        "    That's a headline, not a hook. Instead use one of these patterns:\n"
        "    • PARADOX: 'The AI company everyone feared just got scared of itself'\n"
        "    • QUESTION: 'Why did Anthropic lock their own AI in a vault?'\n"
        "    • STAKES: 'One tool just made 3 million designers redundant'\n"
        "    • REVEAL: 'The phone LG built, finished, and then buried'\n"
        "    • CONTRAST: 'Open source is beating the $100B labs at their own game'\n"
        "  - If your hook could be a CNN headline, rewrite it as a\n"
        "    knowledgeable fan's reaction — opinion-forward, specific, but\n"
        "    still in proper sentence case (not all-lowercase Twitter style).\n"
        "\n"
        "CAPTION VOICE (Instagram):\n"
        "  - Opinion-forward, specific, conversational — but written with\n"
        "    proper sentence case (capitalize the first word of each sentence\n"
        "    and all proper nouns). Sentence case + casual tone = comparable\n"
        "    accounts like House of Highlights, ScreenRant, MKBHD.\n"
        "  - ✅ GOOD: 'Wait, so Claude is smarter than the people training it??'\n"
        "  - ✅ GOOD: 'LG had the rollable phone working. And shelved it. 😭'\n"
        "  - ❌ BAD:  'Anthropic's new Claude model literally found ways to...'\n"
        "    (reads like Reuters — too dry)\n"
        "  - ❌ BAD:  'wait so claude is smarter' (all-lowercase reads as Twitter\n"
        "    shitpost, undercuts the niche's editorial credibility)\n"
        "  - Short sentences. Emoji where it fits (1-2 max, not every sentence).\n"
        "    Strong opinion in the first 6 words.\n"
        "  - Body 150-170 chars. Do NOT describe the video like a news summary.\n"
        "\n"
        "CTA — pick ONE verbatim from this list. Do NOT invent new CTAs:\n"
        f"{cta_menu_text}\n"
        "\n"
        "HASHTAGS: exactly 3-5 tags. Each tag must be a full word (no '#AI #Cl').\n"
        "Pick tags that are actually searched for — niche-specific over generic.\n"
        "\n"
        "CHARACTER LIMITS — TARGETS, not strict requirements (content will be\n"
        "truncated if exceeded, but better to be SLIGHTLY off-target than to\n"
        "omit a field entirely):\n"
        "- hook: ≤60 characters, single line, no trailing punctuation unless ?\n"
        "- instagram_caption: ~150-200 chars body + blank line + CTA +\n"
        "  blank line + 3-5 hashtags. Aim for the target; do not skip if exact\n"
        "  length is hard to satisfy.\n"
        "- twitter_content: ≤280 chars. Punchy, conversational. NO links.\n"
        "- youtube_content: Question format, ≤40 characters total.\n"
        "- facebook_content: 200-300 chars ending in an engaging question.\n"
        "- threads_content: 150-300 chars. Text-first, opinion-forward,\n"
        "  conversational hot-take energy — but proper sentence case (not\n"
        "  all-lowercase). No hashtags.\n"
        "\n"
        "CREATOR ATTRIBUTION (post-Markanimation, 2026-07-10):\n"
        "  The channel handle shown in the user prompt is the SOURCE — where\n"
        "  we found the clip — not necessarily the on-screen creator or the\n"
        "  content's original author. NEVER claim in your caption that the\n"
        "  video was 'made by', 'created by', 'belongs to', or 'shot by' a\n"
        "  specific person. Focus on the CONTENT (what happens, why it's\n"
        "  interesting). Source credit is appended AFTER your output by the\n"
        "  pipeline — don't try to duplicate it.\n"
        "\n"
        "BANNED PHRASES (never use — these are the #1 'AI-generated' tells):\n"
        "  - 'something big happened'\n"
        "  - 'you won't believe'\n"
        "  - 'this could change everything'\n"
        "  - 'here's why' (as a closer)\n"
        "  - 'the community is going wild'\n"
        "  - 'players are saying'\n"
        "  - 'what do you think' — the CTA above is your engagement prompt\n"
        "  - 'let us know in the comments'\n"
        "  - 'don't miss out'\n"
        "  - 'the future is here'\n"
        "  - 'game changer' / 'game-changer'\n"
        "  - 'literally' (overused by LLMs)\n"
        "  - ANY sentence starting with 'Imagine'\n"
        "  - Generic superlatives: 'insane', 'crazy', 'mind-blowing' (use a\n"
        "    SPECIFIC reaction instead)\n"
        "\n"
        + (
            "HOOK DEDUP — these have been used recently, produce a DIFFERENT "
            "angle (not just synonyms):\n"
            f"{existing_hooks_text}\n"
            "\n"
            if existing_hooks_text
            else ""
        )
        + (f"{extra_instructions}\n\n" if extra_instructions else "")
        + style_hint
        + content_angle_hint
        + findings_hint
        + series_context_hint
        + "OUTPUT FORMAT — strictly enforced:\n"
        "Respond ONLY with valid JSON. ALL SIX KEYS ARE REQUIRED and must\n"
        "have non-empty string values:\n"
        "  - hook\n"
        "  - instagram_caption  ← REQUIRED, never empty, never omit\n"
        "  - twitter_content\n"
        "  - youtube_content\n"
        "  - facebook_content\n"
        "  - threads_content    ← REQUIRED, never empty, never omit\n"
        "\n"
        "If you can't satisfy a length target exactly, produce content close\n"
        "to the target — DO NOT omit the field. A field that's slightly\n"
        "off-length is acceptable; a missing field breaks the publish.\n"
        "\n"
        "No markdown, no explanation, no code fences."
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
        content = _complete_and_parse_json(
            llm_client=llm_client,
            system=system,
            user=user,
            max_tokens=1200,
            temperature=0.65,
            niche_id=niche_id,
        )

        # Normalize smart quotes to ASCII equivalents (prevents FFmpeg drawtext issues)
        hook = content.get("hook", "")
        hook = hook.replace("\u2019", "'").replace("\u2018", "'")
        hook = hook.replace("\u201c", '"').replace("\u201d", '"')
        content["hook"] = hook

        # Validate hook length
        if len(hook) > 60:
            hook = hook[:57].rsplit(" ", 1)[0] + "..."
            content["hook"] = hook

        # Reject hooks containing banned generic phrases OR matching the
        # banned "X just <verb>" lead-in template.
        from genlab_core.writing.llm_hook_generator import _BANNED_PATTERNS

        hook_lower = hook.lower()
        if any(phrase in hook_lower for phrase in _BANNED_PHRASES):
            logger.warning(
                "[%s] Rejected banned hook (phrase): %s",
                niche_id,
                hook[:60],
            )
            content["hook"] = ""
        elif any(pat.search(hook) for pat in _BANNED_PATTERNS):
            logger.warning(
                "[%s] Rejected banned hook (pattern): %s",
                niche_id,
                hook[:60],
            )
            content["hook"] = ""
        elif _is_llm_refusal(hook):
            # Task #526 (2026-07-06): Claude occasionally refuses to
            # write a hook (safety-triggered, missing context, or
            # empty story) and returns its preamble as if it were the
            # hook. Persisting that ships "I need to stop here and
            # flag a critical issue..." as the reel's on-screen
            # opener — a 100% funnel leak. Reject at generation time
            # so downstream stages skip the story rather than render
            # a broken reel. Mirrors the filter shipped in PR #702
            # (nightly_schedule_top_per_niche.py) that catches
            # already-persisted refusals; this is the upstream fix.
            logger.warning(
                "[%s] Rejected LLM-refusal hook: %s",
                niche_id,
                hook[:80],
            )
            content["hook"] = ""

        # Defense-in-depth: check LLM OUTPUT for injection patterns and
        # suspicious URLs. A well-crafted input might slip past the input
        # sanitizer in base_writing, and if the LLM then reproduces the
        # attacker's instructions in the hook/caption, we should drop it
        # rather than render it onto a video that ships to 5 channels.
        import re as _re

        from genlab_core.cache.text_sanitizer import check_for_injection

        _url_re = _re.compile(r"https?://|www\.|bit\.ly|tinyurl|goo\.gl")
        for field_name in (
            "hook",
            "instagram_caption",
            "twitter_content",
            "youtube_content",
            "facebook_content",
            "threads_content",
        ):
            val = content.get(field_name, "")
            if not isinstance(val, str) or not val:
                continue
            # Post-2026-07-13 audit follow-up (G4): the LLM refusal check
            # previously ran only on ``hook``. Today's movies fire showed
            # a caption with "I need the Story Summary to write a hook for
            # Moana. The..." shipping to a live audience because that
            # field wasn't in the refusal-check loop. Refusal preambles
            # can slip into ANY generated text field — checking here
            # closes the class. On any refusal detected in any field,
            # blank the whole content dict so downstream fallback fills
            # from safer defaults — a partial-refusal caption paired
            # with a good hook still exposes the failure to users.
            if _is_llm_refusal(val):
                logger.warning(
                    "[%s] LLM-refusal preamble in %s — rejecting entire content: %s",
                    niche_id,
                    field_name,
                    val[:80],
                )
                # Blank every text field so the exception fallback path
                # (line ~865) takes over cleanly. Leaving good fields
                # alongside bad ones is the failure mode Moana exposed.
                for f in (
                    "hook",
                    "instagram_caption",
                    "twitter_content",
                    "youtube_content",
                    "facebook_content",
                    "threads_content",
                ):
                    content[f] = ""
                break
            hits = check_for_injection(val)
            if hits:
                logger.warning(
                    "[%s] LLM output tripped injection heuristic in %s: %s",
                    niche_id,
                    field_name,
                    hits,
                )
                content[field_name] = ""
                continue
            # Reject raw URLs in LLM output — the writer should never
            # emit URLs unprompted. If it does, it's either a hallucination
            # or an attacker-controlled instruction bleeding through.
            if _url_re.search(val):
                logger.warning(
                    "[%s] LLM output contains unexpected URL in %s — dropping",
                    niche_id,
                    field_name,
                )
                content[field_name] = ""

        # ── Enforce Instagram caption standards ──────────────
        ig = content.get("instagram_caption", "")
        # LLM sometimes returns a dict instead of plain string
        if isinstance(ig, dict):
            ig = ig.get("caption") or ig.get("text") or str(list(ig.values())[0]) if ig else ""
            content["instagram_caption"] = ig
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
                # Dynamic topic-aware hashtags instead of static niche list
                from genlab_core.writing.hashtag_generator import generate_hashtags

                all_tags = generate_hashtags(
                    {
                        "title": video.get("title", ""),
                        "summary": video.get("description_snippet", ""),
                    },
                    niche_id,
                    platform="instagram",
                )
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
                body = body[: body_budget - 3].rsplit(" ", 1)[0] + "..."

            # Reassemble: body + CTA + hashtags (total ≤200 chars)
            parts = [body]
            if cta_text:
                parts.append(cta_text)
            parts.append(tags_str)
            ig = "\n\n".join(parts)
            content["instagram_caption"] = ig

        # ── Enforce Twitter ≤280 chars ───────────────────────
        tw = content.get("twitter_content", "")
        # LLM sometimes returns {"tweet_text": "..."} instead of plain string
        if isinstance(tw, dict):
            tw = (
                tw.get("tweet_text")
                or tw.get("text")
                or tw.get("tweet")
                or str(list(tw.values())[0])
                if tw
                else ""
            )
            content["twitter_content"] = tw
        if tw and len(tw) > 280:
            tw = tw[:277].rsplit(" ", 1)[0] + "..."
            content["twitter_content"] = tw

        # ── Enforce YouTube title ≤40 chars + question format ─
        yt = content.get("youtube_content", "")
        # LLM sometimes returns {"title": "...", "description": "..."} instead of plain title
        if isinstance(yt, dict):
            yt = yt.get("title") or str(list(yt.values())[0]) if yt else ""
            # Store description separately for YouTube description field
            yt_desc = (
                content["youtube_content"].get("description", "")
                if isinstance(content.get("youtube_content"), dict)
                else ""
            )
            if yt_desc:
                content["youtube_description"] = yt_desc
            content["youtube_content"] = yt
        if yt and len(yt) > 40:
            yt = yt[:37].rsplit(" ", 1)[0] + "?"
            content["youtube_content"] = yt

        # ── Enforce Facebook 200-300 chars ───────────────────
        fb = content.get("facebook_content", "")
        if isinstance(fb, dict):
            fb = fb.get("text") or fb.get("post") or str(list(fb.values())[0]) if fb else ""
            content["facebook_content"] = fb
        if fb and len(fb) > 300:
            fb = fb[:297].rsplit(" ", 1)[0] + "..."
            content["facebook_content"] = fb

        # ── Enforce Threads 150-300 chars ──────────────────
        th = content.get("threads_content", "")
        if isinstance(th, dict):
            th = th.get("text") or th.get("post") or str(list(th.values())[0]) if th else ""
            content["threads_content"] = th
        if th and len(th) > 300:
            th = th[:297].rsplit(" ", 1)[0] + "..."
            content["threads_content"] = th

        # ── Fill missing fields from fallback ────────────────
        title = video.get("title", "")
        if not content.get("instagram_caption"):
            content["instagram_caption"] = (
                f"{title[:150]}\n\n{voice['hashtags'][0]} {voice['hashtags'][1]}"
            )
        if not content.get("twitter_content"):
            content["twitter_content"] = title[:280]
        if not content.get("youtube_content"):
            content["youtube_content"] = (title[:37] + "?") if len(title) > 37 else title
        if not content.get("facebook_content"):
            content["facebook_content"] = f"{title[:200]} What do you think?"
        if not content.get("threads_content"):
            content["threads_content"] = (title[:297] + "...") if len(title) > 300 else title

        # ── Generate aligned narration opening ─────────────────
        from genlab_core.writing.hook_alignment import build_narration_opening

        hook = content.get("hook", "")
        if hook:
            content["narration_opening"] = build_narration_opening(hook, title, niche_id)

        # ── Add editorial attribution to YouTube description ───
        yt_raw = content.get("youtube_content", "")
        if isinstance(yt_raw, str):
            channel_name = voice.get("channel_name", niche_id.replace("_", " ").title())
            content["youtube_attribution"] = (
                f"Curated and produced by {channel_name} | Original commentary and analysis"
            )

        # ── Source-creator credit for ALL platform captions ────
        # PR #A (2026-07-10, Mark James Magbata / Markanimation
        # incident): the youtube_attribution above credits the
        # producing channel (self-attribution for fair use). It does
        # NOT credit the original video's uploader. push_to_backlog
        # reads this key and appends it to each platform's caption
        # so the source creator is visibly credited on FB, IG,
        # Threads, and the YT description — not just implicit via the
        # Content ID text-classifier line.
        from genlab_core.compliance.copyright_safety import (
            format_source_attribution,
        )

        # 2026-07-14 writer wire fix: pass ``video_url`` alongside
        # (video_id, source, channel_name) so format_source_attribution's
        # URL-fallback branch fires for non-YouTube sources (twitch,
        # scorebat, tmdb_trailer, RSS) where derive_source_url returns
        # None. Prior to this fix, non-YT stories shipped with EMPTY
        # source_attribution → 0/6 recent posts had credit lines →
        # Layer 5 attribution health cratered to 0.0%. Also removed
        # the "youtube_trending" default on ``source`` — passing the
        # actual source (or empty) is more honest and lets the URL
        # fallback catch the empty case.
        content["source_attribution"] = format_source_attribution(
            {
                "video_id": video.get("video_id", ""),
                "source": video.get("source", ""),
                "source_channel_title": video.get("channel_name", ""),
                "video_url": video.get("video_url", ""),
            }
        )

        # Mark as LLM-written for hook strategy dedup
        content["written_by"] = "llm"

        # Record bandit-picked hook style so push_to_backlog can persist it
        # and metric_collector can later credit the style:{niche}:{name} arm.
        # Only record when the hook itself survived banned-phrase / pattern /
        # injection filters; a rejected hook means the style didn't actually
        # ship, so attributing reward to that arm would skew the posterior.
        if chosen_style and content.get("hook"):
            content["hook_style"] = chosen_style

        # Intelligent transformation wire (PR 15 orchestrator consumer):
        # After the hook is finalized, generate structured caption segments
        # so the transformation pipeline's caption_animator has content to
        # burn onto the video. Segments are stored as JSON-friendly
        # ``list[dict]`` so ``blueprint_context.get('caption_segments')``
        # round-trips through DB storage.
        #
        # Fail-open: generate_caption_segments returns None on missing
        # ANTHROPIC_API_KEY / API error / unparseable output — orchestrator
        # then skips caption_style stage cleanly. Writer's own hook +
        # caption fields still ship regardless.
        hook_for_segments = content.get("hook") or ""
        if hook_for_segments:
            try:
                from genlab_core.writing.caption_segments import (
                    generate_caption_segments,
                )

                seg_result = generate_caption_segments(
                    {
                        "title": video.get("title", ""),
                        "summary": video.get("description_snippet", ""),
                    },
                    niche_id,
                    hook_for_segments,
                )
                if seg_result is not None and seg_result.is_usable():
                    content["caption_segments"] = [
                        {
                            "text": s.text,
                            "emphasis_words": list(s.emphasis_words),
                        }
                        for s in seg_result.segments
                    ]
            except Exception as exc:
                logger.debug(
                    "[%s] caption_segments generation skipped: %s",
                    niche_id,
                    exc,
                )

        # ── R-50: enforce sentence case on every shipped text field ──────
        # The prompt asks for sentence case but nothing enforced it, so
        # all-lowercase "shitpost" output slipped through. to_sentence_case is
        # additive-only (proper nouns / acronyms preserved) and idempotent.
        _apply_sentence_case(content)

        return content

    except Exception as e:
        logger.error("[%s] Content generation failed: %s", niche_id, e)
        # Fallback using video title — NOT a generic template
        title = video.get("title", "")
        channel = video.get("channel_name", "")
        from genlab_core.writing.hashtag_generator import generate_hashtags

        fallback_tags = generate_hashtags(
            {"title": title, "summary": video.get("description_snippet", "")},
            niche_id,
            platform="instagram",
        )

        # Post-2026-07-13 audit follow-up (G5): degraded fallback caption
        # must still carry a credit line. Today's gaming fire shipped
        # ``Grand Theft Auto V\n\nVia twitch_trending\n\n#Gaming...`` —
        # the "Via {channel}" prefix reads as attribution but isn't a
        # recognised credit marker, so Layer 4 rejects it AND real
        # audiences see no explicit source. Prepending the standard
        # 🎬 Original: marker here means the fallback path satisfies
        # both Layer 4 validation AND audience expectations.
        from genlab_core.compliance.copyright_safety import (
            format_source_attribution,
        )

        _credit_line = format_source_attribution(
            {
                "video_id": video.get("video_id", ""),
                "source": video.get("source", ""),
                "source_channel_title": channel,
                "video_url": video.get("video_url", ""),
            }
        )

        def _with_credit(body: str) -> str:
            """Append credit line if present + not already there."""
            if not _credit_line:
                return body
            if _credit_line in body:
                return body
            return f"{body}\n\n{_credit_line}" if body else _credit_line

        fallback = {
            "hook": title[:57] + "..." if len(title) > 60 else title,
            "instagram_caption": _with_credit(
                f"{title}\n\nVia {channel}\n\n{' '.join(fallback_tags)}"
            ),
            "twitter_content": _with_credit(title[:200])[:280],
            "youtube_content": title[:40],  # YT title format — no credit here
            "facebook_content": _with_credit(f"{title} — what do you think?"),
            "threads_content": _with_credit(title[:200])[:300],
        }
        # The degraded path ships too — sentence-case it (R-50) so a lowercase
        # source title doesn't read as a shitpost.
        _apply_sentence_case(fallback)
        return fallback
