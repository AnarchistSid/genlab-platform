"""Pipeline stage: Write multi-platform content for gaming stories.

Calls Anthropic API to generate hook, caption, and platform adaptations
for each story. Reads templates.yaml for structural constraints.

Usage:
    stage = WriteGamingContent()
    context = stage.execute(context)
"""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import Any

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore[assignment]

import yaml
from genlab_core.cost.model_router import get_model
from genlab_core.http.retry import retry as http_retry
from genlab_core.platforms.rules import enforce_platform_rules
from genlab_core.settings import settings
from genlab_core.strategies import WritingStrategy
from niches.gaming.hooks.hook_validator import GamingHookValidator
from niches.gaming.tools.schema_validator import validate_against_schema

# HookPredictor is optional — returns None when model not fitted, so
# the default hook generation path always works as fallback.
_hook_predictor = None
try:
    from niches.gaming.learning.hook_predictor import HookPredictor

    _hook_predictor = HookPredictor()
except Exception:
    pass

# Minimum prediction score below which a retry is triggered (if retries remain).
_HOOK_QUALITY_THRESHOLD = 0.3

# Transient Anthropic errors that are safe to retry
_LLM_RETRYABLE = (
    (
        anthropic.APIConnectionError,
        anthropic.APITimeoutError,
        anthropic.InternalServerError,
        anthropic.RateLimitError,
    )
    if anthropic is not None
    else (Exception,)
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

PROMPT_TEMPLATE = """You are writing content for CriticalRush, a gaming social media brand with an energetic, community-insider voice.

Game: {game_title}
Story angle: {story_summary}
Brand voice: energetic, emotional, community-insider, never formal news language
Forbidden words: boring, simply, just, easy, basic
Forbidden hook styles: BREAKING:, JUST IN:, announces, reveals

Hook formulas to choose from (pick the best fit, you can adapt slightly):
{hook_formulas}

CRITICAL hook style rules:
- NO markdown formatting (no **bold**, *italic*, __underline__, # headers)
- NO Reddit references (no r/gaming, /u/username, TIL, ELI5)
- NO news-anchor openers (no BREAKING:, JUST IN:, ALERT:)
- NO formal language (no "We are pleased", "It has been announced")
- NO weak openers (no "So", "Well", "Basically", "Honestly")
- Keep hook between 20–60 characters, max 8 words, emotionally charged
- Write like a passionate gamer talking to friends, not a news anchor
{retry_feedback}
Write content for all six platforms. Respond ONLY with valid JSON, no markdown fences, no preamble:

{{
  "hook": "the scroll-stopping first line (max 8 words, max 60 chars, emotional)",
  "instagram": {{
    "caption": "full caption body (150-300 chars) ending with: {cta}",
    "hashtags": ["pick exactly 4 from the pool: {hashtag_pool}"]
  }},
  "youtube": {{
    "title": "question format, max 40 chars",
    "description": "2-3 sentences, include game name"
  }},
  "x_twitter": {{
    "tweet": "max 240 chars (leave room for link in reply)",
    "hashtags": ["2 hashtags from pool"]
  }},
  "facebook": {{
    "caption": "200-400 chars with engagement question at end"
  }},
  "tiktok": {{
    "caption": "hook must be in first 150 chars, total max 2200, emotionally punchy, assume sound-on",
    "hashtags": ["3-5 hashtags, mix niche (#gaming) and trending (#fyp, #foryou)"]
  }},
  "threads": {{
    "caption": "150-300 chars, text-first, conversational, opinion-forward, max 500 chars"
  }}
}}"""


def _load_templates() -> dict[str, Any]:
    """Load templates.yaml from niches/gaming/config/."""
    path = PROJECT_ROOT / "niches" / "gaming" / "config" / "templates.yaml"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


_FALLBACK_HOOKS = [
    "This changes everything for {title}",
    "No one expected this from {title}",
    "{title} just dropped a bombshell",
    "The {title} update no one saw coming",
    "Why everyone is talking about {title}",
    "{title} fans are not ready for this",
]

_FALLBACK_IG_CAPTIONS = [
    "{summary}\n\nThis is one of the biggest shifts we've seen — what do you think?\n\n{cta}",
    "If you play {title}, you need to know about this.\n\n{summary}\n\n{cta}",
    "{summary}\n\nDrop a comment with your take.\n\n{cta}",
]

_FALLBACK_FB_CAPTIONS = [
    "{title} just shook things up.\n\n{summary}\n\nWhat's your reaction?",
    "Big news for {title} — {summary}\n\nAre you hyped or skeptical?",
    "Here's what you need to know about {title}:\n\n{summary}\n\nThoughts?",
]

_FALLBACK_TWEETS = [
    "{title} just changed the game — {summary_short}",
    "Massive update for {title}: {summary_short}",
    "If you care about {title}, you need to see this",
]


def _build_fallback(
    game_title: str, templates: dict[str, Any], story_summary: str = ""
) -> dict[str, Any]:
    """Generate varied fallback content when LLM fails.

    Uses multiple templates rotated by hash of the game title to ensure
    different stories get different text. Never uses banned template phrases.
    """
    ctas = templates.get("captions", {}).get("cta_library", ["follow for daily gaming content"])
    hashtags = templates.get("captions", {}).get(
        "hashtag_pool", ["#gaming", "#gamer", "#games", "#videogames"]
    )
    cta = random.choice(ctas)

    # Deterministic rotation based on title so same story gets same fallback
    idx = hash(game_title) % len(_FALLBACK_HOOKS)
    summary = story_summary or game_title
    summary_short = summary[:80] if len(summary) > 80 else summary

    return {
        "hook": _FALLBACK_HOOKS[idx].format(title=game_title)[:60],
        "instagram": {
            "caption": _FALLBACK_IG_CAPTIONS[idx % len(_FALLBACK_IG_CAPTIONS)].format(
                title=game_title,
                summary=summary,
                cta=cta,
            ),
            "hashtags": hashtags[:4],
        },
        "youtube": {
            "title": f"What just happened with {game_title}?"[:40],
            "description": f"{summary}\n\nWatch for the full breakdown.",
        },
        "x_twitter": {
            "tweet": _FALLBACK_TWEETS[idx % len(_FALLBACK_TWEETS)].format(
                title=game_title,
                summary_short=summary_short,
            )[:240],
            "hashtags": hashtags[:2],
        },
        "facebook": {
            "caption": _FALLBACK_FB_CAPTIONS[idx % len(_FALLBACK_FB_CAPTIONS)].format(
                title=game_title,
                summary=summary,
            ),
        },
        "tiktok": {
            "caption": f"{_FALLBACK_HOOKS[idx].format(title=game_title)} — {summary}",
            "hashtags": ["#gaming", "#gamer", "#fyp", "#foryou"],
        },
        "threads": {
            "caption": f"{summary}\n\nWhat's your take on this?",
        },
    }


def _validate_content(content: dict[str, Any], templates: dict[str, Any]) -> list[str]:
    """Validate LLM output against template constraints. Returns list of issues."""
    issues = []

    if not content.get("hook"):
        issues.append("missing hook")

    ig = content.get("instagram", {})
    if not ig.get("caption"):
        issues.append("missing instagram caption")

    yt = content.get("youtube", {})
    yt_title = yt.get("title", "")
    yt_max = templates.get("youtube", {}).get("title_max_length", 40)
    if len(yt_title) > yt_max:
        # Auto-fix: truncate
        content["youtube"]["title"] = yt_title[: yt_max - 1] + "?"
        issues.append(f"youtube title truncated from {len(yt_title)} to {yt_max} chars")

    if yt_title and not yt_title.rstrip().endswith("?"):
        issues.append("youtube title not a question")

    return issues


def _apply_platform_rules(content: dict[str, Any], story: dict[str, Any]) -> None:
    """Enforce universal platform rules on each platform's generated content."""
    source_url = story.get("source_url")

    # Instagram
    ig = content.get("instagram", {})
    if ig.get("caption"):
        adapted = enforce_platform_rules(
            platform="instagram",
            title=content.get("hook", ""),
            caption=ig["caption"],
            hashtags=ig.get("hashtags", []),
            url=source_url,
        )
        ig["caption"] = adapted.caption
        if adapted.hashtags:
            ig["hashtags"] = adapted.hashtags

    # YouTube
    yt = content.get("youtube", {})
    if yt.get("title"):
        adapted = enforce_platform_rules(
            platform="youtube",
            title=yt["title"],
            caption=yt.get("description", ""),
        )
        yt["title"] = adapted.title
        yt["description"] = adapted.caption

    # X/Twitter
    tw = content.get("x_twitter", {})
    if tw.get("tweet"):
        adapted = enforce_platform_rules(
            platform="twitter",
            title="",
            caption=tw["tweet"],
            url=source_url,
        )
        tw["tweet"] = adapted.caption
        if adapted.first_comment:
            tw["link_reply"] = adapted.first_comment

    # Facebook
    fb = content.get("facebook", {})
    if fb.get("caption"):
        adapted = enforce_platform_rules(
            platform="facebook",
            title="",
            caption=fb["caption"],
        )
        fb["caption"] = adapted.caption


class WriteGamingContent(WritingStrategy):
    """Write multi-platform content for each story via Anthropic API."""

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        api_key = settings.anthropic_api_key
        if not api_key:
            logger.warning("[WRITE] ANTHROPIC_API_KEY not set, skipping content writing")
            context.setdefault("run_stats", {})["content_writing"] = {
                "written_count": 0,
                "failed_count": 0,
                "status": "skipped_no_api_key",
            }
            return context

        client = anthropic.Anthropic(api_key=api_key)

        templates = _load_templates()
        stories = context.get("stories", [])

        hook_formulas = "\n".join(
            f"  - {f}" for f in templates.get("hooks", {}).get("formulas", [])
        )
        ctas = templates.get("captions", {}).get("cta_library", [])
        hashtag_pool = templates.get("captions", {}).get("hashtag_pool", [])

        written_count = 0
        failed_count = 0
        validation_failures = 0
        used_hooks: set[str] = set()

        for story in stories:
            game_title = story.get("title", "Unknown Game")
            story_summary = story.get("summary", "") or game_title

            cta = random.choice(ctas) if ctas else "follow for daily gaming content"
            pool_str = ", ".join(hashtag_pool[:10])

            prompt = PROMPT_TEMPLATE.format(
                game_title=game_title,
                story_summary=story_summary,
                hook_formulas=hook_formulas,
                cta=cta,
                hashtag_pool=pool_str,
                retry_feedback="",
            )

            try:
                gaming_validator = GamingHookValidator()
                max_hook_retries = 2
                content = None
                current_prompt = prompt

                for attempt in range(1 + max_hook_retries):

                    @http_retry(
                        max_attempts=3,
                        initial_delay=5.0,
                        backoff=2.0,
                        exceptions=_LLM_RETRYABLE,
                    )
                    def _call_llm(p=current_prompt):
                        return client.messages.create(
                            model=get_model("write_gaming_content"),
                            max_tokens=1000,
                            messages=[{"role": "user", "content": p}],
                        )

                    response = _call_llm()

                    # Cost tracking
                    from genlab_core.intelligence.cost_accumulator import get_accumulator

                    acc = get_accumulator()
                    if acc and hasattr(response, "usage") and response.usage:
                        acc.record_llm(
                            get_model("write_gaming_content"),
                            response.usage.input_tokens,
                            response.usage.output_tokens,
                        )

                    raw = response.content[0].text

                    # Strip markdown fences if present
                    raw = raw.strip()
                    if raw.startswith("```"):
                        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                    if raw.endswith("```"):
                        raw = raw[:-3]
                    raw = raw.strip()

                    content = json.loads(raw)

                    # JSON schema validation (advisory — log only, don't block)
                    schema_ok, schema_err = validate_against_schema(
                        content,
                        "written_content.schema.json",
                    )
                    if not schema_ok:
                        logger.warning(
                            "[WRITE] Schema validation failed for '%s': %s",
                            game_title,
                            schema_err,
                        )

                    # Validate structural constraints
                    issues = _validate_content(content, templates)
                    if issues:
                        validation_failures += 1
                        logger.warning(
                            "[WRITE] Validation issues for '%s': %s",
                            game_title,
                            "; ".join(issues),
                        )

                    # Validate hook with gaming-specific rules
                    hook_text = content.get("hook", "")
                    if hook_text:
                        hook_result = gaming_validator.validate(hook_text)
                        if hook_result.passed:
                            # Score hook with predictor (advisory — None if model not fitted)
                            hook_score = None
                            if _hook_predictor is not None:
                                try:
                                    hook_score = _hook_predictor.predict(hook_text)
                                except Exception as e:
                                    logger.debug("[WRITE] HookPredictor error: %s", e)

                            if hook_score is not None:
                                content["hook_prediction_score"] = round(hook_score, 4)
                                if (
                                    hook_score < _HOOK_QUALITY_THRESHOLD
                                    and attempt < max_hook_retries
                                ):
                                    logger.info(
                                        "[WRITE] Hook scored %.2f (below %.2f) for '%s', retrying",
                                        hook_score,
                                        _HOOK_QUALITY_THRESHOLD,
                                        game_title,
                                    )
                                    current_prompt = PROMPT_TEMPLATE.format(
                                        game_title=game_title,
                                        story_summary=story_summary,
                                        hook_formulas=hook_formulas,
                                        cta=cta,
                                        hashtag_pool=pool_str,
                                        retry_feedback=(
                                            f"\nYour previous hook scored low on engagement prediction. "
                                            f'The hook was: "{hook_text}". '
                                            f"Write a more emotionally charged, scroll-stopping hook.\n"
                                        ),
                                    )
                                    continue  # Retry with quality feedback
                            break  # Hook is good (or predictor not available)

                        hook_issues = hook_result.all_issues
                        logger.warning(
                            "[WRITE] Hook failed validation for '%s' (attempt %d/%d): %s",
                            game_title,
                            attempt + 1,
                            1 + max_hook_retries,
                            hook_issues,
                        )

                        if attempt < max_hook_retries:
                            # Retry with feedback about what went wrong
                            feedback = (
                                f"\nYour previous hook was rejected. Issues: {', '.join(hook_issues)}. "
                                f'The rejected hook was: "{hook_text}". '
                                f"Write a completely different hook that avoids these issues.\n"
                            )
                            current_prompt = PROMPT_TEMPLATE.format(
                                game_title=game_title,
                                story_summary=story_summary,
                                hook_formulas=hook_formulas,
                                cta=cta,
                                hashtag_pool=pool_str,
                                retry_feedback=feedback,
                            )
                        else:
                            # All retries exhausted — clean the hook as best-effort
                            cleaned = gaming_validator.clean(hook_text)
                            if cleaned and len(cleaned) >= 10:
                                content["hook"] = cleaned
                                logger.info(
                                    "[WRITE] Using cleaned hook for '%s': %s",
                                    game_title,
                                    cleaned,
                                )
                            # else keep original — better than nothing
                    else:
                        break  # No hook to validate

                # Enforce 60-char cap on hook
                hook_text = content.get("hook", "")
                if len(hook_text) > 60:
                    hook_text = hook_text[:57].rsplit(" ", 1)[0] + "..."
                    content["hook"] = hook_text

                # Cross-story dedup: if this hook was already used, generate a varied alternative
                if hook_text.lower() in used_hooks:
                    dedup_idx = len(used_hooks) % len(_FALLBACK_HOOKS)
                    hook_text = _FALLBACK_HOOKS[dedup_idx].format(title=game_title)
                    if len(hook_text) > 60:
                        hook_text = hook_text[:57].rsplit(" ", 1)[0] + "..."
                    content["hook"] = hook_text
                used_hooks.add(content["hook"].lower())

                # Enforce universal platform rules on each platform's content
                _apply_platform_rules(content, story)

                story["content"] = content
                written_count += 1
                logger.info("[WRITE] Wrote content for '%s'", game_title)

            except (json.JSONDecodeError, KeyError, IndexError) as e:
                logger.warning(
                    "[WRITE] JSON parse failed for '%s': %s — using fallback",
                    game_title,
                    e,
                )
                story["content"] = _build_fallback(game_title, templates, story_summary)
                failed_count += 1

            except _LLM_RETRYABLE as e:
                logger.error(
                    "[WRITE] LLM call failed after 3 attempts for '%s': %s — using fallback",
                    game_title,
                    e,
                )
                story["content"] = _build_fallback(game_title, templates, story_summary)
                failed_count += 1

            except Exception as e:
                logger.warning(
                    "[WRITE] API call failed for '%s': %s — using fallback",
                    game_title,
                    e,
                )
                story["content"] = _build_fallback(game_title, templates, story_summary)
                failed_count += 1

        context.setdefault("run_stats", {})["content_writing"] = {
            "written_count": written_count,
            "failed_count": failed_count,
            "validation_failures": validation_failures,
        }

        logger.info(
            "[WRITE] %d stories written, %d used fallback, %d validation issues",
            written_count,
            failed_count,
            validation_failures,
        )
        return context
