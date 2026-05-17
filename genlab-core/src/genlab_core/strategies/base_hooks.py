"""Base hook generation strategy with shared template/LLM logic.

All non-BB channels share the same hook generation pipeline:

1. Try LLM hook (Claude Haiku) via ``llm_hook_generator.generate_hook()``.
2. Fall back to category-aware template formulas from ``templates.yaml``.
3. Apply forbidden-style stripping, length enforcement, deduplication.
4. Validate via ``HookValidator``.

Subclasses must provide:
- ``niche_id``  — channel identifier
- ``niche_root`` — ``Path`` to the channel root directory
- ``_classify_story()`` — niche-specific story category classification
- ``_substitute_placeholders()`` — niche-specific formula placeholder resolution

Subclasses may override:
- ``_generate_hook()`` — full hook generation pipeline for one story
- ``_title_fallback_label`` — the label used in title-derived fallback hooks
"""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Any

import yaml

from genlab_core.intelligence.hook_validator import HookValidator, clean_hook

from .interfaces import HookStrategy

logger = logging.getLogger(__name__)


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


class BaseHookStrategy(HookStrategy):
    """Shared hook generation logic for all video-first channels.

    Parameters
    ----------
    niche_id:
        Channel identifier (``"sports"``, ``"movies"``, ``"anime"``, etc.).
    niche_root:
        Path to the channel root directory containing ``config/``.
    """

    # Override in subclass to change the title-fallback suffix.
    _title_fallback_label: str = "Trending moment"

    def __init__(self, niche_id: str, niche_root: Path) -> None:
        self._niche_id = niche_id
        self._niche_root = niche_root
        self._templates: dict | None = None
        logger.info("[%s] %s initialized", self._niche_id, type(self).__name__)

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def _ensure_config(self) -> None:
        if self._templates is not None:
            return
        self._templates = _load_yaml(self._niche_root / "config" / "templates.yaml")

    # ------------------------------------------------------------------
    # Abstract hooks — subclasses MUST implement these
    # ------------------------------------------------------------------

    def _classify_story(self, story: dict) -> str:
        """Classify story into a hook category.  Must be overridden."""
        raise NotImplementedError

    def _substitute_placeholders(self, formula: str, story: dict) -> str:
        """Replace ``{placeholders}`` in a formula with story data.  Must be overridden."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Shared hook generation
    # ------------------------------------------------------------------

    @staticmethod
    def _truncate_at_word(text: str, max_chars: int) -> str:
        """Truncate at last complete word before *max_chars*."""
        if len(text) <= max_chars:
            return text
        truncated = text[:max_chars]
        last_space = truncated.rfind(" ")
        if last_space > max_chars // 2:
            truncated = truncated[:last_space]
        result = truncated.rstrip(" .,!?-:")
        # Strip trailing conjunctions/prepositions that create incomplete sentences
        for suffix in (" and", " or", " but", " the", " a", " an", " is", " are", " in", " of", " to", " for", " with"):
            if result.lower().endswith(suffix):
                result = result[: -len(suffix)].rstrip(" .,!?-:")
                break
        return result

    def _generate_hook(self, story: dict, used_hooks: set[str] | None = None) -> str:
        """Generate a hook for a single story.

        Tries LLM first, falls back to category templates, then title-derived.
        Deduplication via *used_hooks* set.
        Banned phrases are rejected at every stage.
        """
        self._ensure_config()
        if used_hooks is None:
            used_hooks = set()

        # Try LLM-generated hook first
        try:
            from genlab_core.writing.llm_hook_generator import generate_hook

            result = generate_hook(
                story, self._niche_id, used_hooks, return_style=True,
            )
            # return_style=True yields (hook, style) — None style means
            # cold-start / no bandit influence, which we don't record.
            if isinstance(result, tuple):
                llm_hook, hook_style = result
            else:  # Defensive: older mock or stub returning plain str
                llm_hook, hook_style = result, None
            if llm_hook and not self._is_banned(llm_hook):
                if hook_style:
                    story["hook_style"] = hook_style
                return llm_hook
        except ImportError:
            pass

        category = self._classify_story(story)
        hooks_config = (self._templates or {}).get("hooks", {})
        categories = hooks_config.get("story_categories", {})
        forbidden = hooks_config.get("forbidden_styles", [])

        # Build prioritized formula list: own category first, then others
        cat_config = categories.get(category, categories.get("default", {}))
        primary_formulas = list(
            cat_config.get("formulas", hooks_config.get("formulas", []))
        )
        random.shuffle(primary_formulas)

        other_formulas: list[str] = []
        for other_cat, other_cfg in categories.items():
            if other_cat != category:
                other_formulas.extend(other_cfg.get("formulas", []))
        random.shuffle(other_formulas)

        for formula in primary_formulas + other_formulas:
            hook = self._substitute_placeholders(formula, story)
            if not hook:
                continue  # Formula needs data we don't have
            for f in forbidden:
                if hook.upper().startswith(f.upper()):
                    hook = hook[len(f) :].strip()
            if len(hook) > 60:
                hook = hook[:57].rsplit(" ", 1)[0] + "..."
            if hook.lower() not in used_hooks and not self._is_banned(hook):
                return hook

        # Fallback: title-derived hook (always unique)
        title = story.get("title", self._title_fallback_label)
        hook = title[:57].rsplit(" ", 1)[0] + "..." if len(title) > 60 else title
        return hook

    @staticmethod
    def _is_banned(hook: str) -> bool:
        """Check if hook contains any banned phrase."""
        from genlab_core.writing.llm_hook_generator import _BANNED_PHRASES
        hook_lower = hook.lower()
        return any(phrase in hook_lower for phrase in _BANNED_PHRASES)

    # ------------------------------------------------------------------
    # execute() — shared pipeline entry point
    # ------------------------------------------------------------------

    def execute(self, context: Any) -> Any:
        """Generate hooks for all stories in the pipeline."""
        self._ensure_config()

        stories = context.get("stories", [])
        if not stories:
            logger.info("[%s] HookStrategy: no stories to hook", self._niche_id)
            context.setdefault("run_stats", {})["hooks"] = {
                "status": "no_stories",
                "hooked_count": 0,
            }
            return context

        hooked_count = 0
        skipped_llm = 0
        validated_count = 0
        rejected_count = 0
        categories_used: dict[str, int] = {}
        used_hooks: set[str] = set()
        validator = HookValidator()

        for story in stories:
            # Skip clipless stories — no video means no reel, no hook needed
            if story.get("_skip_llm"):
                logger.debug(
                    "[%s] Skipping hook for clipless story: %s",
                    self._niche_id,
                    story.get("title", "")[:40],
                )
                continue

            # Skip if the writing stage already produced an LLM hook
            existing_hook = story.get("content", {}).get("hook", "")
            if existing_hook and story.get("content", {}).get("written_by") == "llm":
                used_hooks.add(existing_hook.lower())
                skipped_llm += 1
                logger.debug(
                    "[%s] Skipping hook generation — LLM hook exists: %s",
                    self._niche_id,
                    existing_hook[:40],
                )
                continue

            category = self._classify_story(story)
            hook = self._generate_hook(story, used_hooks)
            used_hooks.add(hook.lower())

            # Validate hook against universal quality rules
            vr = validator.validate(hook, platform="instagram")
            validated_count += 1
            if not vr.passed:
                rejected_count += 1
                logger.warning(
                    "[%s][HookValidator] Rejected: '%s' — %s",
                    self._niche_id,
                    hook[:50],
                    [f.value for f in vr.failures],
                )
                # Try to salvage the hook by cleaning artifacts
                hook = clean_hook(hook)
                if len(hook) > 60:
                    hook = hook[:57].rsplit(" ", 1)[0] + "..."

            # Semantic quality gate: hook should contain at least one word
            # from the story title (specificity check)
            title_words = set(w.lower() for w in (story.get("title", "").split()) if len(w) > 3)
            hook_words = set(w.lower() for w in hook.split() if len(w) > 3)
            specificity = len(title_words & hook_words)
            if specificity == 0 and title_words:
                logger.debug(
                    "[%s] Hook has no title overlap — may be generic: '%s' (title: '%s')",
                    self._niche_id, hook[:40], story.get("title", "")[:40],
                )

            content = story.setdefault("content", {})
            content["hook"] = hook
            content["hook_category"] = category
            content["hook_specificity"] = specificity

            categories_used[category] = categories_used.get(category, 0) + 1
            hooked_count += 1

        context.setdefault("run_stats", {})["hooks"] = {
            "hooked_count": hooked_count,
            "skipped_llm": skipped_llm,
            "validated": validated_count,
            "rejected": rejected_count,
            "categories_used": categories_used,
        }

        logger.info(
            "[%s] HookStrategy: generated %d hooks, skipped %d (LLM), "
            "validated %d, rejected %d (%s)",
            self._niche_id,
            hooked_count,
            skipped_llm,
            validated_count,
            rejected_count,
            categories_used,
        )
        return context
