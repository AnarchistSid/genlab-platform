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
import re
from pathlib import Path
from typing import Any

import yaml

from genlab_core.intelligence.hook_validator import HookValidator, clean_hook
from genlab_core.writing.text_case import to_sentence_case

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
        for suffix in (
            " and",
            " or",
            " but",
            " the",
            " a",
            " an",
            " is",
            " are",
            " in",
            " of",
            " to",
            " for",
            " with",
        ):
            if result.lower().endswith(suffix):
                result = result[: -len(suffix)].rstrip(" .,!?-:")
                break
        return result

    def _generate_hook(self, story: dict, used_hooks: set[str] | None = None) -> str | None:
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
                story,
                self._niche_id,
                used_hooks,
                return_style=True,
                return_classifier_score=True,
            )
            # return_classifier_score=True yields (hook, style, clf_score).
            # We tolerate older 2-tuple + plain-str returns for any test/mock
            # that hasn't been updated — keeps the strategy backward-compat.
            llm_hook: str | None
            hook_style: str | None = None
            clf_score: float | None = None
            if isinstance(result, tuple):
                if len(result) == 3:
                    llm_hook, hook_style, clf_score = result
                elif len(result) == 2:
                    llm_hook, hook_style = result
                else:
                    llm_hook = result[0] if result else None
            else:  # Defensive: stub returning plain str
                llm_hook = result
            if llm_hook and not self._is_banned(llm_hook):
                if hook_style:
                    story["hook_style"] = hook_style
                # Lever D1 (2026-06-21): persist the HookClassifier's
                # winning-hook score on the story dict so downstream
                # stages (push_to_backlog + bandit reward) can use it
                # as a learning signal. Pre-D1 this was computed and
                # discarded inside generate_hook's local scope. None
                # is a valid value (single-candidate / classifier
                # failure paths) — downstream must tolerate it.
                if clf_score is not None:
                    story["hook_classifier_score"] = clf_score
                return llm_hook
        except ImportError:
            pass

        category = self._classify_story(story)
        hooks_config = (self._templates or {}).get("hooks", {})
        categories = hooks_config.get("story_categories", {})
        forbidden = hooks_config.get("forbidden_styles", [])

        # Build prioritized formula list: own category first, then others
        cat_config = categories.get(category, categories.get("default", {}))
        primary_formulas = list(cat_config.get("formulas", hooks_config.get("formulas", [])))
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
                hook = hook[:57].rsplit(" ", 1)[0].rstrip(".") + "..."
            if hook.lower() not in used_hooks and not self._is_banned(hook):
                return hook

        # F-0080 HARD-FAIL (2026-07-27): previously the title-derived
        # fallback returned `story["title"]` verbatim here when both the
        # LLM path and the template path failed. That produced ~47% of
        # published hooks matching source titles (Phase 8.3 read-through
        # of 15 real recent hooks) — the exact "mass-produced templated"
        # format YouTube terminated 16 channels for in Jan 2026 and
        # Screen Culture / KH Studio for in Dec 2025. On the two
        # auto-approved niches there is no operator review to catch a
        # passthrough reel; on the other three the operator has been
        # rejecting 40-58% of blueprints, and this fallback is a
        # significant fraction of those rejections.
        #
        # Return None. The single caller (`execute` at ~line 418) treats
        # None as "no hook produced" and drops the story with a WARN.
        # The publisher tolerates a niche producing no reel that day
        # (mandate is 41.4% per DECISION.md Rev 7); a missing reel
        # beats a terminated channel.
        #
        # If you are tempted to re-add a fallback here, first read the
        # Phase 8.3 HOOK_READTHROUGH.md and RESEARCH_2026_07.md — and
        # confirm the fallback isn't shipping the same format that got
        # 4.7B lifetime views permanently terminated on 2026-01.
        logger.warning(
            "[%s] F-0080 hard-fail: LLM path + template path both failed "
            "for story title=%r; returning None (was: title-derived "
            "passthrough hook)",
            self._niche_id,
            (story.get("title") or "")[:60],
        )
        return None

    @staticmethod
    def _is_banned(hook: str) -> bool:
        """Check if hook contains any banned phrase or matches a banned pattern."""
        from genlab_core.writing.llm_hook_generator import _BANNED_PATTERNS, _BANNED_PHRASES

        hook_lower = hook.lower()
        if any(phrase in hook_lower for phrase in _BANNED_PHRASES):
            return True
        return any(pat.search(hook) for pat in _BANNED_PATTERNS)

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
            # Stories marked _skip_llm by base_writing — LLM declined to
            # write a hook (banned pattern, off-topic, etc.). Before giving
            # up, try a title-derived fallback so we don't lose the slot.
            # If the title can't yield a valid hook either, keep the skip
            # flag set so push_to_backlog drops it.
            if story.get("_skip_llm"):
                title = (story.get("title", "") or "").strip()
                if title:
                    cleaned = title.split("|")[0].strip()
                    cleaned = re.sub(r"\s*\([^)]*\)\s*", " ", cleaned).strip()
                    cleaned = re.sub(r"^#\w+\s*", "", cleaned).strip()
                    if len(cleaned) > 60:
                        cleaned = cleaned[:57].rsplit(" ", 1)[0].rstrip(".") + "..."
                    # Title is only a usable hook if it has hook-shape:
                    # ends in a question OR contains an action verb (not
                    # just a bare noun phrase). Without this guard the
                    # 2026-05-21 audit found us shipping "Forza Horizon
                    # 6", "Deep Rock Galactic: Rogue Core", "Atletico
                    # Madrid - Athletic Bilbao" as hooks — those are
                    # match listings, not curiosity hooks.
                    has_question = cleaned.rstrip().endswith("?")
                    has_verb = bool(
                        re.search(
                            r"\b(is|was|did|are|just|hit|got|made|drops?|broke|"
                            r"won|lost|leaked|killed|saved|ended|started|"
                            r"happened|caught|forced|destroyed|exposed|"
                            r"changes?|matters?|reveals?)\b",
                            cleaned,
                            re.IGNORECASE,
                        )
                    )
                    long_enough = len(cleaned) >= 25
                    looks_hooky = (has_question or has_verb) and long_enough
                    if cleaned and looks_hooky and not self._is_banned(cleaned):
                        cleaned = to_sentence_case(cleaned)
                        story.setdefault("content", {})["hook"] = cleaned
                        story.pop("_skip_llm", None)
                        used_hooks.add(cleaned.lower())
                        hooked_count += 1
                        logger.info(
                            "[%s] LLM skip recovered via title-derived hook: %s",
                            self._niche_id,
                            cleaned[:60],
                        )
                        continue
                    if cleaned and not looks_hooky:
                        # Fall through to the template-formula path —
                        # raw titles like "Forza Horizon 6" need to be
                        # wrapped in a curiosity formula, not shipped
                        # as-is. Drop the skip flag so the path below
                        # runs; the formula generator handles bare
                        # subjects.
                        logger.info(
                            "[%s] Title not hook-shaped, deferring to template formula: %s",
                            self._niche_id,
                            cleaned[:60],
                        )
                        story.pop("_skip_llm", None)
                        # don't continue — let the formula path below handle it
                    else:
                        logger.info(
                            "[%s] LLM skip not recoverable (title empty/banned), "
                            "leaving for push_to_backlog drop: %s",
                            self._niche_id,
                            (story.get("title") or "")[:40],
                        )
                        continue
                else:
                    logger.info(
                        "[%s] LLM skip not recoverable (no title), leaving for drop",
                        self._niche_id,
                    )
                    continue

            # The writing stage may already have produced an LLM hook. R-52:
            # previously such hooks were trusted blindly (`continue`) and never
            # saw the validator — markdown/Reddit artifacts or >60-char hooks
            # shipped unchecked. Now we validate (with a hard ≤60 cap), salvage
            # if possible, and only fall through to template regeneration when
            # the LLM hook is unrecoverable.
            existing_hook = story.get("content", {}).get("hook", "")
            if existing_hook and story.get("content", {}).get("written_by") == "llm":
                llm_vr = validator.validate(existing_hook, platform="instagram", max_chars=60)
                validated_count += 1
                if llm_vr.passed:
                    used_hooks.add(existing_hook.lower())
                    skipped_llm += 1
                    # 2026-07-14 fix: score the validated LLM hook via
                    # the hook_classifier so downstream auto-approval
                    # gate has real signal. Prior behavior short-
                    # circuited BEFORE classifier scoring (line 350
                    # `continue`) — the writer-path LLM hooks never
                    # got a hook_classifier_score set on the story,
                    # so 0/316 blueprints had this field populated in
                    # 30d and auto_approval_gate defaulted to 0.5
                    # (neutral) for every one of them, dragging the
                    # 6-check confidence mean below the 0.80 threshold.
                    #
                    # Fail-open: any classifier error → clf_score stays
                    # None → gate treats as "unknown" (its documented
                    # fallback), no regression vs pre-fix.
                    try:
                        from genlab_core.learning.hook_classifier import (
                            HookClassifier,
                        )

                        clf = HookClassifier(niche_id=self._niche_id)
                        score = clf.score_hook(existing_hook)
                        # clf.score_hook returns 0.5 on any error (see its
                        # docstring). 0.5 IS a legitimate neutral prediction
                        # value, so we always persist. Downstream can
                        # distinguish 'model unavailable' via metadata (not
                        # via presence/absence of the field).
                        story["hook_classifier_score"] = float(score)
                    except Exception as exc:  # noqa: BLE001 — fail-open
                        logger.debug(
                            "[%s] hook_classifier scoring skipped: %s",
                            self._niche_id,
                            exc,
                        )
                    logger.debug(
                        "[%s] Skipping hook generation — valid LLM hook exists: %s",
                        self._niche_id,
                        existing_hook[:40],
                    )
                    continue
                # Salvage: strip artifacts, enforce ≤60, sentence-case, re-validate.
                salvaged = clean_hook(existing_hook)
                if len(salvaged) > 60:
                    salvaged = salvaged[:57].rsplit(" ", 1)[0].rstrip(".") + "..."
                salvaged = to_sentence_case(salvaged)
                if validator.validate(salvaged, platform="instagram", max_chars=60).passed:
                    story["content"]["hook"] = salvaged
                    used_hooks.add(salvaged.lower())
                    hooked_count += 1
                    logger.info(
                        "[%s][HookValidator] Salvaged LLM hook %r -> %r (%s)",
                        self._niche_id,
                        existing_hook[:40],
                        salvaged[:40],
                        [f.value for f in llm_vr.failures],
                    )
                    continue
                # Unsalvageable — drop the LLM hook and regenerate via the
                # template/title path below.
                rejected_count += 1
                logger.warning(
                    "[%s][HookValidator] LLM hook unsalvageable, regenerating: %r (%s)",
                    self._niche_id,
                    existing_hook[:50],
                    [f.value for f in llm_vr.failures],
                )
                content = story.setdefault("content", {})
                content.pop("hook", None)
                content["written_by"] = ""

            category = self._classify_story(story)
            hook = self._generate_hook(story, used_hooks)
            # F-0080 HARD-FAIL: _generate_hook returns None when the LLM
            # path AND the template path both failed. The pre-fix code
            # returned the source title as a fallback here — the exact
            # format that got 16 YouTube channels terminated in Jan 2026.
            # Drop the story instead. Publisher tolerates a niche
            # producing no reel that day.
            if hook is None:
                rejected_count += 1
                logger.warning(
                    "[%s] F-0080: dropping story with no viable hook "
                    "(title=%r) — LLM + template both failed",
                    self._niche_id,
                    (story.get("title") or "")[:60],
                )
                continue
            # R-50/R-51: template & fallback formulas (templates.yaml) ship
            # all-lowercase; normalize to sentence case before validate/store.
            hook = to_sentence_case(hook)
            used_hooks.add(hook.lower())

            # Validate hook against universal quality rules (hard ≤60 cap, R-52)
            vr = validator.validate(hook, platform="instagram", max_chars=60)
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
                    hook = hook[:57].rsplit(" ", 1)[0].rstrip(".") + "..."

            # Semantic quality gate: hook should contain at least one word
            # from the story title (specificity check)
            title_words = set(w.lower() for w in (story.get("title", "").split()) if len(w) > 3)
            hook_words = set(w.lower() for w in hook.split() if len(w) > 3)
            specificity = len(title_words & hook_words)
            if specificity == 0 and title_words:
                logger.debug(
                    "[%s] Hook has no title overlap — may be generic: '%s' (title: '%s')",
                    self._niche_id,
                    hook[:40],
                    story.get("title", "")[:40],
                )

            content = story.setdefault("content", {})
            content["hook"] = hook
            content["hook_category"] = category
            content["hook_specificity"] = specificity

            # 2026-07-21 Agent-3 fix: unconditional hook_classifier
            # scoring at end-of-loop covers all 5 hook-generation paths
            # (LLM primary, LLM validate fast-path, salvage, template
            # regen, title-recovered). Pre-fix, only paths A + B set
            # this — 3 paths silently produced hooks without scores →
            # 39-80% of blueprints per niche were missing
            # hook_classifier_score → gate confidence dragged neutral.
            # `not in` guard preserves the deliberate writes upstream
            # (paths A + B set it during generation with more context).
            if "hook_classifier_score" not in story:
                try:
                    from genlab_core.learning.hook_classifier import HookClassifier

                    clf = HookClassifier(niche_id=self._niche_id)
                    if clf._loaded:
                        story["hook_classifier_score"] = float(clf.score_hook(hook))
                except Exception as exc:  # noqa: BLE001
                    # Rule #19: WARN not DEBUG. Silent-fail here means
                    # gate loses signal for this blueprint.
                    logger.warning(
                        "[%s] hook_classifier scoring skipped: %s",
                        self._niche_id,
                        exc,
                        exc_info=True,
                    )

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
