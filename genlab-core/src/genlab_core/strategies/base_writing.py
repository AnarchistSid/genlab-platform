"""Base writing strategy with shared LLM + template-fallback content generation.

All non-BB channels share nearly identical writing logic:

1. Load ``templates.yaml`` + optional ``writing.yaml`` from niche config dir.
2. For each story, try LLM via ``write_video_content()`` first.
3. Fall back to template-based caption if LLM is unavailable or fails.
4. Populate per-platform content dicts (instagram, youtube, x_twitter, etc.).

Subclasses must provide:
- ``niche_id``  — channel identifier (used for model routing + logging)
- ``niche_root`` — ``Path`` to the channel root directory (config lives there)

Subclasses may override:
- ``_story_to_video_dict()`` — customize how story dicts map to the video format
- ``_build_caption()`` — customize template-based caption assembly
- ``_model_route_key()`` — the key used to pick the LLM model via ``get_model()``
"""

from __future__ import annotations

import logging
import os
import random
import re
from pathlib import Path
from typing import Any

import yaml

from genlab_core.cache.text_sanitizer import (
    check_for_injection,
    sanitize_text,
)

from .interfaces import WritingStrategy

logger = logging.getLogger(__name__)


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _build_extra_instructions(writing_cfg: dict) -> str:
    """Build extra_instructions string from writing.yaml config.

    Reads banned_phrases, hook_examples, caption_examples, tone_notes.
    Each section is stitched together with double-newline separators so
    the LLM sees them as distinct blocks.
    """
    parts: list[str] = []
    banned = writing_cfg.get("banned_phrases", [])
    if banned:
        parts.append(
            "NICHE-SPECIFIC BANNED PHRASES (in addition to the universal list above):\n"
            + "\n".join(f"  - {p}" for p in banned)
        )
    examples = writing_cfg.get("hook_examples", [])
    if examples:
        parts.append(
            "NICHE HOOK EXAMPLES (style reference — match the voice, don't copy):\n"
            + "\n".join(f"  - {e}" for e in examples)
        )
    caption_examples = writing_cfg.get("caption_examples", [])
    if caption_examples:
        parts.append(
            "NICHE CAPTION EXAMPLES (reaction voice — match the tone + brevity):\n"
            + "\n".join(f"  - {e}" for e in caption_examples)
        )
    tone = writing_cfg.get("tone_notes", "")
    if tone:
        parts.append(f"NICHE TONE: {tone.strip()}")
    return "\n\n".join(parts)


class BaseWritingStrategy(WritingStrategy):
    """Shared writing logic for all video-first channels.

    Parameters
    ----------
    niche_id:
        Channel identifier (``"sports"``, ``"movies"``, ``"anime"``, etc.).
    niche_root:
        Path to the channel root directory containing ``config/``.
    """

    def __init__(self, niche_id: str, niche_root: Path) -> None:
        self._niche_id = niche_id
        self._niche_root = niche_root
        self._templates: dict | None = None
        self._writing_cfg: dict | None = None
        logger.info("[%s] %s initialized", self._niche_id, type(self).__name__)

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    def _ensure_config(self) -> None:
        if self._templates is not None:
            return
        self._templates = _load_yaml(self._niche_root / "config" / "templates.yaml")
        writing_path = self._niche_root / "config" / "writing.yaml"
        self._writing_cfg = _load_yaml(writing_path) if writing_path.exists() else {}

    def _model_route_key(self) -> str:
        """Return the model-router key for ``get_model()``. Override per niche."""
        return f"write_{self._niche_id}_content"

    # ------------------------------------------------------------------
    # Story-to-video dict conversion (override for niche-specific fields)
    # ------------------------------------------------------------------

    def _story_to_video_dict(
        self, story: dict, clip_index: dict | None = None
    ) -> dict:
        """Convert a pipeline story dict to the video dict expected by write_video_content.

        External text (title, summary, tags, channel name) is sanitized before
        leaving this boundary. Scraped YouTube/RSS content is treated as
        untrusted — adversarial creators could craft titles containing
        "ignore previous instructions" to hijack the LLM prompt. See
        .claude/rules/security.md for the full rule set.
        """
        story_id = story.get("story_id", "")
        clip_info = {}
        if clip_index:
            clip_info = clip_index.get("clips", {}).get(story_id, {})

        raw_title = story.get("title", "")
        raw_summary = story.get("summary", "")
        raw_channel = story.get("source", "")
        raw_tags = story.get("tags", []) or []

        # Sanitize: strip HTML, collapse whitespace, drop control chars
        clean_title = sanitize_text(raw_title, max_length=500)
        clean_summary = sanitize_text(raw_summary, max_length=1000)
        clean_channel = sanitize_text(raw_channel, max_length=200)
        clean_tags = [sanitize_text(t, max_length=60) for t in raw_tags[:16] if t]

        # Injection detection: if any field trips the heuristics, log and
        # drop THAT field's value. We don't raise because a single injection
        # pattern shouldn't kill the whole pipeline — the LLM prompt will
        # just miss one field, and the downstream hook generator has its
        # own safeguards (HookValidator).
        for field_name, value in (
            ("title", clean_title),
            ("summary", clean_summary),
            ("channel_name", clean_channel),
        ):
            hits = check_for_injection(value)
            if hits:
                logger.warning(
                    "[%s] Injection heuristic hit in %s for story %s: %s",
                    self._niche_id, field_name, story_id[:16], hits,
                )
                if field_name == "title":
                    clean_title = ""
                elif field_name == "summary":
                    clean_summary = ""
                elif field_name == "channel_name":
                    clean_channel = ""

        return {
            "video_id": clip_info.get("video_id", story.get("video_id", story_id)),
            "title": clean_title,
            "channel_name": clean_channel,
            "view_count": story.get("view_count", 0),
            "view_velocity": story.get("view_velocity", 0),
            "age_hours": story.get("age_hours", 1),
            "description_snippet": clean_summary[:300],
            "tags": clean_tags,
        }

    # ------------------------------------------------------------------
    # Template-based caption (override for niche-specific caption logic)
    # ------------------------------------------------------------------

    def _build_caption(self, story: dict) -> str:
        """Build a caption from story data using templates.yaml config."""
        self._ensure_config()

        title = story.get("title", "")
        summary = story.get("summary", "")
        hook = story.get("content", {}).get("hook", "")

        captions_config = (self._templates or {}).get("captions", {})
        cta_library = captions_config.get("cta_library", [])
        hashtag_pool = captions_config.get("hashtag_pool", [])
        hashtags_per_post = captions_config.get("hashtags_per_post", 4)

        parts: list[str] = []
        if hook:
            parts.append(hook)
        elif title:
            parts.append(title)

        if summary:
            parts.append(summary[:200])

        if cta_library:
            parts.append(random.choice(cta_library))

        if hashtag_pool:
            selected = random.sample(
                hashtag_pool, min(hashtags_per_post, len(hashtag_pool))
            )
            parts.append(" ".join(selected))

        target_length = captions_config.get("target_length", 300)
        caption = "\n\n".join(parts)
        return caption[:target_length] if len(caption) > target_length else caption

    # ------------------------------------------------------------------
    # Template-based story writing
    # ------------------------------------------------------------------

    def _write_story_template(self, story: dict) -> dict:
        """Generate content for a single story using template config."""
        content = story.get("content", {})
        caption = self._build_caption(story)
        content["caption"] = caption
        content["written"] = True

        title = story.get("title", "")
        hook = content.get("hook", "")
        hashtags = re.findall(r"#\w+", caption)
        content["instagram"] = {"caption": caption, "hashtags": hashtags}
        # Use hook as YouTube title (more engaging than raw headline)
        yt_title = hook if hook else title[:40]
        content["youtube"] = {"title": yt_title, "description": caption}
        content["x_twitter"] = {"tweet": caption[:280]}
        content["facebook"] = {"caption": caption[:300]}
        content["tiktok"] = {"caption": caption[:2200]}
        content["threads"] = {"caption": caption[:500]}

        story["content"] = content
        return story

    def _write_story(self, story: dict) -> dict:
        """Backward-compatible alias for ``_write_story_template()``."""
        return self._write_story_template(story)

    # ------------------------------------------------------------------
    # LLM-based story writing
    # ------------------------------------------------------------------

    def _write_story_llm(
        self,
        story: dict,
        llm_client: Any,
        extra_instructions: str,
        existing_hooks: list[str],
        clip_index: dict | None = None,
    ) -> dict:
        """Generate content for a single story via LLM."""
        from genlab_core.writing.video_content_writer import write_video_content

        video = self._story_to_video_dict(story, clip_index)
        result = write_video_content(
            video=video,
            niche_id=self._niche_id,
            llm_client=llm_client,
            existing_hooks=existing_hooks,
            extra_instructions=extra_instructions,
        )

        content = story.setdefault("content", {})
        content["hook"] = result.get("hook", "")
        content["caption"] = result.get("instagram_caption", "")
        content["written"] = True
        content["written_by"] = "llm"

        # Carry the bandit-picked hook style up to story level so
        # push_to_backlog can promote it into the blueprint's extra
        # JSONB. publish_all_platforms reads ``fields["hook_style"]``
        # to populate ``bandit_context["extra_arms"]`` so the 48h
        # reward can credit ``style:{niche}:{name}`` alongside the
        # content_type arm. Without this propagation the style:* arms
        # never accumulate plays (2026-05-20 root cause).
        if result.get("hook_style"):
            story["hook_style"] = result["hook_style"]
            content["hook_style"] = result["hook_style"]

        ig_caption = result.get("instagram_caption", "")
        hashtags = re.findall(r"#\w+", ig_caption)
        content["instagram"] = {"caption": ig_caption, "hashtags": hashtags}
        # Use hook as YouTube title (more engaging than raw headline)
        hook = result.get("hook", "")
        yt_title = hook if hook else result.get("youtube_content", "")[:40]
        content["youtube"] = {
            "title": yt_title,
            "description": ig_caption,
        }
        content["x_twitter"] = {"tweet": result.get("twitter_content", "")[:280]}
        content["facebook"] = {"caption": result.get("facebook_content", "")[:300]}
        # Use LLM-generated TikTok/Threads content if available, else fall back to IG
        tk_content = result.get("tiktok_content", "") or ig_caption
        th_content = result.get("threads_content", "") or ig_caption
        tk_hashtags = re.findall(r"#\w+", tk_content)
        content["tiktok"] = {"caption": tk_content[:2200], "hashtags": tk_hashtags}
        content["threads"] = {"caption": th_content[:500]}

        story["content"] = content
        return story

    # ------------------------------------------------------------------
    # execute() — shared pipeline entry point
    # ------------------------------------------------------------------

    def execute(self, context: Any) -> Any:
        """Generate captions and platform content for all stories."""
        self._ensure_config()

        stories = context.get("stories", [])
        if not stories:
            logger.info("[%s] WritingStrategy: no stories to write", self._niche_id)
            context.setdefault("run_stats", {})["content_writing"] = {
                "status": "no_stories",
                "written_count": 0,
            }
            return context

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        use_llm = bool(api_key)

        if not use_llm:
            logger.info(
                "[%s] No ANTHROPIC_API_KEY — using template-based writing",
                self._niche_id,
            )

        llm_client = None
        extra_instructions = ""
        if use_llm:
            from genlab_core.cost.model_router import get_model
            from genlab_core.writing.llm_client import AnthropicLLMClient

            model = get_model(self._model_route_key())
            llm_client = AnthropicLLMClient(api_key=api_key, model=model)
            extra_instructions = _build_extra_instructions(self._writing_cfg or {})
            logger.info(
                "[%s] Using LLM writing with model=%s", self._niche_id, model
            )

        clip_index = context.get("clip_index")
        existing_hooks: list[str] = []
        written_count = 0
        failed_count = 0
        llm_count = 0

        for story in stories:
            # Skip clipless stories — VideoGate marked them, no video = no reel
            if story.get("_skip_llm"):
                logger.debug(
                    "[%s] Skipping writing for clipless story: %s",
                    self._niche_id,
                    story.get("title", "")[:40],
                )
                continue

            try:
                if use_llm and llm_client is not None:
                    self._write_story_llm(
                        story,
                        llm_client,
                        extra_instructions,
                        existing_hooks,
                        clip_index,
                    )
                    hook = story.get("content", {}).get("hook", "")
                    if not hook:
                        # LLM returned empty hook — story is off-topic or unwritable.
                        # Mark for skip so downstream stages ignore it.
                        story["_skip_llm"] = True
                        logger.info(
                            "[%s] LLM returned empty hook, marking skip: %s",
                            self._niche_id,
                            story.get("title", "")[:60],
                        )
                        continue
                    existing_hooks.append(hook)
                    llm_count += 1
                else:
                    self._write_story_template(story)
                written_count += 1
            except Exception:
                logger.exception(
                    "[%s] Failed to write story: %s",
                    self._niche_id,
                    story.get("title", "?"),
                )
                # Fall back to template on LLM failure
                if use_llm:
                    try:
                        self._write_story_template(story)
                        written_count += 1
                        continue
                    except Exception:
                        logger.warning("Template fallback also failed", exc_info=True)
                failed_count += 1

        status = "llm" if llm_count > 0 else "template_based"
        context.setdefault("run_stats", {})["content_writing"] = {
            "status": status,
            "written_count": written_count,
            "failed_count": failed_count,
            "llm_count": llm_count,
        }

        logger.info(
            "[%s] WritingStrategy: wrote %d stories (%d LLM, %d failed)",
            self._niche_id,
            written_count,
            llm_count,
            failed_count,
        )
        return context
