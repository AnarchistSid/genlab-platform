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
            "Drop your take below 👇", "Who else caught this?",
            "Tag someone who mains this", "Thoughts? 💀",
            "Agree or disagree?", "Name a better play 🎮",
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


def _complete_and_parse_json(
    llm_client: Any,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
    niche_id: str,
) -> dict:
    """Call the LLM and parse its JSON response, retrying once on parse failure.

    Claude occasionally emits malformed JSON (unescaped quotes/colons inside
    string values). A single retry with an explicit reminder of the prior
    parse error fixes the vast majority of these. On a second failure we let
    the JSONDecodeError propagate so ``write_video_content`` can return its
    title-derived fallback content.
    """
    last_err: Exception | None = None
    for attempt in range(2):
        retry_user = user
        if attempt > 0 and last_err is not None:
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
            return json.loads(clean)
        except json.JSONDecodeError as exc:
            last_err = exc
            if attempt == 0:
                logger.info(
                    "[%s] LLM JSON parse failed (attempt 1/2): %s — retrying",
                    niche_id, exc,
                )
                continue
            raise


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
    from genlab_core.writing.llm_hook_generator import _HOOK_STYLES, pick_hook_style
    chosen_style = pick_hook_style(niche_id)
    style_hint = ""
    if chosen_style and chosen_style in _HOOK_STYLES:
        style_hint = f"\nSTYLE TARGET: {_HOOK_STYLES[chosen_style]}\n"

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
        "STRICT CHARACTER LIMITS (content will be truncated if exceeded):\n"
        "- hook: ≤60 characters, single line, no trailing punctuation unless ?\n"
        "- instagram_caption: EXACTLY 150-170 chars body + blank line + CTA +\n"
        "  blank line + 3-5 hashtags. TOTAL ≤ 200 chars including hashtags.\n"
        "- twitter_content: ≤280 chars. Punchy, conversational. NO links.\n"
        "- youtube_content: Question format, ≤40 characters total.\n"
        "- facebook_content: 200-300 chars ending in an engaging question.\n"
        "- threads_content: 150-300 chars. Text-first, opinion-forward,\n"
        "  conversational hot-take energy — but proper sentence case (not\n"
        "  all-lowercase). No hashtags.\n"
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
            if existing_hooks_text else ""
        )
        + (f"{extra_instructions}\n\n" if extra_instructions else "")
        + style_hint
        + "Respond ONLY with valid JSON with these exact keys: "
        "hook, instagram_caption, twitter_content, youtube_content, "
        "facebook_content, threads_content. No markdown, no explanation."
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
            max_tokens=800,
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
                "[%s] Rejected banned hook (phrase): %s", niche_id, hook[:60],
            )
            content["hook"] = ""
        elif any(pat.search(hook) for pat in _BANNED_PATTERNS):
            logger.warning(
                "[%s] Rejected banned hook (pattern): %s", niche_id, hook[:60],
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
        for field_name in ("hook", "instagram_caption", "twitter_content",
                           "youtube_content", "facebook_content", "threads_content"):
            val = content.get(field_name, "")
            if not isinstance(val, str) or not val:
                continue
            hits = check_for_injection(val)
            if hits:
                logger.warning(
                    "[%s] LLM output tripped injection heuristic in %s: %s",
                    niche_id, field_name, hits,
                )
                content[field_name] = ""
                continue
            # Reject raw URLs in LLM output — the writer should never
            # emit URLs unprompted. If it does, it's either a hallucination
            # or an attacker-controlled instruction bleeding through.
            if _url_re.search(val):
                logger.warning(
                    "[%s] LLM output contains unexpected URL in %s — dropping",
                    niche_id, field_name,
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
                    {"title": video.get("title", ""), "summary": video.get("description_snippet", "")},
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
        # LLM sometimes returns {"tweet_text": "..."} instead of plain string
        if isinstance(tw, dict):
            tw = tw.get("tweet_text") or tw.get("text") or tw.get("tweet") or str(list(tw.values())[0]) if tw else ""
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
            yt_desc = content["youtube_content"].get("description", "") if isinstance(content.get("youtube_content"), dict) else ""
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
            content["instagram_caption"] = f"{title[:150]}\n\n{voice['hashtags'][0]} {voice['hashtags'][1]}"
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
            content["youtube_attribution"] = f"Curated and produced by {channel_name} | Original commentary and analysis"

        # Mark as LLM-written for hook strategy dedup
        content["written_by"] = "llm"

        # Record bandit-picked hook style so push_to_backlog can persist it
        # and metric_collector can later credit the style:{niche}:{name} arm.
        # Only record when the hook itself survived banned-phrase / pattern /
        # injection filters; a rejected hook means the style didn't actually
        # ship, so attributing reward to that arm would skew the posterior.
        if chosen_style and content.get("hook"):
            content["hook_style"] = chosen_style

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
        return {
            "hook": title[:57] + "..." if len(title) > 60 else title,
            "instagram_caption": (
                f"{title}\n\nVia {channel}\n\n{' '.join(fallback_tags)}"
            ),
            "twitter_content": title[:280],
            "youtube_content": title[:40],
            "facebook_content": f"{title} — what do you think?",
            "threads_content": title[:300],
        }
