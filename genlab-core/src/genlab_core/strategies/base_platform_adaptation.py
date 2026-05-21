"""Base platform adaptation strategy with full rule enforcement.

Applies all 8 universal platform rules from ``genlab_core.platforms.rules``
to every story's content dict:

- YouTube: question-format title, <=40 chars
- Instagram: strip URLs, enforce 3-5 hashtags, append CTA
- X/Twitter: move URLs to first_reply
- Facebook: strip URLs, remove competitor hashtags, cap hashtags, engagement Q
- TikTok: strip URLs, enforce hook-in-first-150-chars, 3-5 hashtags, <=2200 chars
- Threads: strip URLs, enforce <=500 chars
- Universal: US date format conversion

Subclasses can override individual ``_adapt_*`` methods for niche-specific
behavior while keeping the full rule enforcement chain.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from genlab_core.platforms.rules import enforce_platform_rules

from .interfaces import PlatformAdaptationStrategy

logger = logging.getLogger(__name__)

_URL_PATTERN = re.compile(r"https?://\S+")


class BasePlatformAdaptationStrategy(PlatformAdaptationStrategy):
    """Shared platform adaptation logic with full rule enforcement.

    Parameters
    ----------
    niche_id:
        Unique identifier for the channel (e.g. ``"sports"``, ``"anime"``).
    niche_root:
        Path to the niche root directory (for loading config).
        If not provided, only universal rules are applied (no hashtag pool).
    """

    def __init__(self, niche_id: str, niche_root: Path | None = None) -> None:
        self._niche_id = niche_id
        self._niche_root = niche_root
        self._hashtag_pool: list[str] = []
        self._cta_pool: list[str] = []
        self._config_loaded = False
        logger.info("[%s] %s initialized", self._niche_id, type(self).__name__)

    def _ensure_config(self) -> None:
        """Load hashtag and CTA pools from templates.yaml."""
        if self._config_loaded:
            return
        self._config_loaded = True
        if not self._niche_root:
            return
        templates_path = self._niche_root / "config" / "templates.yaml"
        if not templates_path.exists():
            return
        try:
            with open(templates_path) as f:
                config = yaml.safe_load(f) or {}
            captions = config.get("captions", {})
            self._hashtag_pool = captions.get("hashtag_pool", [])
            self._cta_pool = captions.get("cta_library", [])
        except Exception as exc:
            logger.warning("[%s] Failed to load templates.yaml: %s", self._niche_id, exc)

    # ------------------------------------------------------------------
    # Per-platform adaptation methods — override in subclass if needed
    # ------------------------------------------------------------------

    def _adapt_instagram(self, content: dict[str, Any], story: dict[str, Any]) -> None:
        """Instagram: strip URLs, enforce 3-5 hashtags, append CTA only if missing.

        Note: the LLM writer already includes a niche-appropriate CTA pulled
        from ``NICHE_VOICE[niche].ctas`` (e.g. anime's "Peak or mid?"). We do
        NOT force-append ``templates.yaml.cta_library[0]`` on top, because
        that produced two CTAs per caption — the LLM's plus a generic one
        like "Would you add this to your list?". ``enforce_platform_rules``
        falls back to ``_DEFAULT_CTAS[0]`` only when the caption truly has
        no CTA shape at all (no question mark, no LLM CTA pattern).
        """
        ig = content.get("instagram", {})
        caption = ig.get("caption", "")
        if not caption:
            return

        hashtags = ig.get("hashtags", [])
        result = enforce_platform_rules(
            "instagram",
            caption,
            caption,
            hashtags=hashtags,
            cta=None,
        )
        ig["caption"] = result.caption
        # Pad hashtags from pool if under 3
        if len(hashtags) < 3 and self._hashtag_pool:
            existing = {h.lower().lstrip("#") for h in hashtags}
            for tag in self._hashtag_pool:
                tag_clean = tag.lstrip("#")
                if tag_clean.lower() not in existing:
                    hashtags.append(f"#{tag_clean}" if not tag.startswith("#") else tag)
                    if len(hashtags) >= 5:
                        break
            ig["hashtags"] = hashtags[:5]

    def _adapt_youtube(self, content: dict[str, Any]) -> None:
        """YouTube: question-format title, <=40 chars."""
        yt = content.get("youtube", {})
        title = yt.get("title", "")
        if not title:
            return
        result = enforce_platform_rules("youtube", title, "")
        yt["title"] = result.title

    def _adapt_x_twitter(self, content: dict[str, Any], story: dict[str, Any]) -> None:
        """X/Twitter: move URLs to first_reply."""
        tw = content.get("x_twitter", {})
        tweet = tw.get("tweet", "")
        if not tweet:
            return
        source_url = story.get("source_url", "")
        result = enforce_platform_rules("twitter", "", tweet, url=source_url)
        tw["tweet"] = result.caption
        if result.first_comment:
            tw["first_reply"] = result.first_comment

    def _adapt_facebook(self, content: dict[str, Any]) -> None:
        """Facebook: strip URLs, remove competitor hashtags, engagement Q."""
        fb = content.get("facebook", {})
        caption = fb.get("caption", "")
        if not caption:
            return
        hashtags = re.findall(r"#\w+", caption)
        result = enforce_platform_rules("facebook", "", caption, hashtags=hashtags)
        fb["caption"] = result.caption

    def _adapt_tiktok(self, content: dict[str, Any]) -> None:
        """TikTok: strip URLs, hook in first 150 chars, hashtags, <=2200 chars."""
        tk = content.get("tiktok", {})
        caption = tk.get("caption", "")
        if not caption:
            return

        # Strip URLs
        caption = _URL_PATTERN.sub("", caption).strip()

        # Ensure hook is in first 150 chars (TikTok algorithm requirement)
        hook = content.get("hook", "")
        if hook and hook.lower() not in caption[:150].lower():
            caption = f"{hook}\n\n{caption}"

        # Enforce hashtag count (3-5)
        hashtags = tk.get("hashtags", re.findall(r"#\w+", caption))
        if len(hashtags) < 3 and self._hashtag_pool:
            existing = {h.lower().lstrip("#") for h in hashtags}
            for tag in self._hashtag_pool:
                tag_clean = tag.lstrip("#")
                if tag_clean.lower() not in existing:
                    hashtags.append(f"#{tag_clean}" if not tag.startswith("#") else tag)
                    if len(hashtags) >= 5:
                        break
            tk["hashtags"] = hashtags[:5]

        # Truncate to 2200 chars
        if len(caption) > 2200:
            caption = caption[:2197].rsplit(" ", 1)[0] + "..."

        tk["caption"] = caption

    def _adapt_threads(self, content: dict[str, Any]) -> None:
        """Threads: enforce <=500 chars."""
        th = content.get("threads", {})
        caption = th.get("caption", "")
        if not caption:
            return
        # Strip URLs (Threads has limited link support)
        caption = _URL_PATTERN.sub("", caption).strip()
        # Truncate to 500 chars
        if len(caption) > 500:
            caption = caption[:497].rsplit(" ", 1)[0] + "..."
        th["caption"] = caption

    def _adapt_story(self, content: dict[str, Any], story: dict[str, Any]) -> None:
        """Apply all platform adaptations to a single story.

        Override this to add extra niche-specific logic while keeping
        the full rule enforcement chain via ``super()._adapt_story()``.
        """
        self._adapt_instagram(content, story)
        self._adapt_youtube(content)
        self._adapt_x_twitter(content, story)
        self._adapt_facebook(content)
        self._adapt_tiktok(content)
        self._adapt_threads(content)

    # ------------------------------------------------------------------
    # execute() — shared pipeline entry point
    # ------------------------------------------------------------------

    def execute(self, context: Any) -> Any:
        """Apply platform-specific adaptations to all stories."""
        self._ensure_config()
        stories = context.get("stories", [])
        adapted_count = 0

        for story in stories:
            content = story.get("content")
            if not content:
                continue

            self._adapt_story(content, story)
            adapted_count += 1

        context.setdefault("run_stats", {})["adapt"] = {
            "adapted": adapted_count,
            "total": len(stories),
        }
        logger.info(
            "[%s] PlatformAdaptation: adapted %d stories",
            self._niche_id,
            adapted_count,
        )
        return context
