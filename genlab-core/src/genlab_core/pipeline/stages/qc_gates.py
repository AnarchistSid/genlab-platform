"""Pipeline stage: Pre-render quality gates.

Runs three gates on each blueprint candidate:
  1. Claim coverage — every must_cite claim has ≥1 source URL
  2. Template constraints — slides ≤ max, words ≤ max, duration ≤ max
  3. Content completeness — required fields present (hook, body, sources)

Annotates each blueprint with ``validation_status`` dict. Blueprints that
fail all three gates get a score penalty (configurable, default 0.3).

Non-fatal: failures are logged and annotated but never crash the pipeline.
"""

from __future__ import annotations

import logging
from typing import Any

from genlab_core.pipeline.stage_context import StageContext

logger = logging.getLogger(__name__)


class QCGates:
    """Pre-render quality gate stage.

    Reads: context['stories'], context['niche_config']
    Writes: context['stories'][*]['validation_status'], context['run_stats']['qc']
    """

    # Default constraints (overridden by niche_config.templates if present)
    DEFAULT_MAX_SLIDES = 10
    DEFAULT_MAX_WORDS = 300
    DEFAULT_MAX_DURATION = 90
    SCORE_PENALTY = 0.3

    def execute(self, context: StageContext) -> StageContext:
        blueprints = context.get("stories", [])
        if not blueprints:
            logger.info("[QCGates] No stories to validate")
            return context

        # Filter out stories that earlier stages marked unpublishable.
        # ``_skip_llm=True`` is set by:
        #   * VideoGate (video_gate.py:205) when no valid clip exists
        #   * WritingStrategy (base_writing.py:446) when LLM returned an
        #     empty hook (banned/off-topic/un-writable content)
        # Both signals mean: the content cannot be published. The story
        # WILL be dropped at PushToBacklog (push_to_backlog.py:1173).
        # Validating them here produces misleading "Missing required
        # field: caption/body" failures that mask real QC regressions —
        # the operator-facing dashboard's QC pass rate then attributes
        # the problem to writing-quality regression when the actual
        # cause is upstream (no clip / un-writable source).
        #
        # The bug surface (2026-06-20): HookStrategy's title-derived
        # recovery (base_hooks.py:275, 296) pops ``_skip_llm`` for some
        # stories whose LLM-skip was at the writing stage. Those
        # stories then survive this filter, retain a recovered hook,
        # but have no caption/body — failing QC for the wrong reason.
        # Filtering by ``_skip_llm`` BEFORE recovery would be too
        # aggressive (some recoveries DO complete the story). The
        # right filter is here, AFTER all stages have had their chance
        # to fix incomplete stories, and BEFORE QC validates them.
        pre_filter = len(blueprints)
        blueprints = [bp for bp in blueprints if not bp.get("_skip_llm")]
        excluded = pre_filter - len(blueprints)
        if excluded > 0:
            logger.info(
                "[QCGates] Excluded %d LLM-skipped stories from QC "
                "(will be dropped at push_to_backlog)",
                excluded,
            )

        if not blueprints:
            logger.info("[QCGates] All stories LLM-skipped — nothing to validate")
            context.setdefault("run_stats", {})["qc"] = {
                "passed": 0,
                "failed": 0,
                "total": 0,
                "pass_rate": "n/a",
                "failure_reasons": {},
                "failure_examples": [],
                "excluded_skip_llm": excluded,
            }
            return context

        config = context.get("niche_config", {})

        # Pre-validation: 3-pass dedup to remove near-duplicate stories
        dedup_cfg = config.get("dedup", {})
        if dedup_cfg.get("enabled", True) and len(blueprints) > 1:
            try:
                from genlab_core.intelligence.dedup_engine import DedupEngine

                engine = DedupEngine(
                    jaccard_threshold=dedup_cfg.get("jaccard_threshold", 0.85),
                    tfidf_threshold=dedup_cfg.get("tfidf_threshold", 0.80),
                    url_field="source_url",
                    text_field="title",
                )
                result = engine.run(blueprints)
                if result.pass1_removed + result.pass2_removed + result.pass3_removed > 0:
                    logger.info(
                        "[QCGates] Dedup: removed %d/%d stories (url=%d, jaccard=%d, tfidf=%d)",
                        len(blueprints) - len(result.unique),
                        len(blueprints),
                        result.pass1_removed,
                        result.pass2_removed,
                        result.pass3_removed,
                    )
                    blueprints = result.unique
                    context["stories"] = blueprints
            except Exception as exc:
                logger.debug("[QCGates] Dedup skipped: %s", exc)

        templates_cfg = config.get("templates", {})
        max_slides = templates_cfg.get("max_slides", self.DEFAULT_MAX_SLIDES)
        max_words = templates_cfg.get("max_words", self.DEFAULT_MAX_WORDS)
        max_duration = templates_cfg.get("max_duration_seconds", self.DEFAULT_MAX_DURATION)

        passed = 0
        failed = 0
        # Aggregate per-issue counts so the run_report surfaces what's
        # actually wrong instead of just "20% passed". Without this the
        # only signal on a degrading QC pass rate was the rate itself —
        # operators had to grep journal logs to find specific failures.
        failure_reasons: dict[str, int] = {}
        failure_examples: list[dict[str, object]] = []

        for bp in blueprints:
            try:
                status = self._validate(bp, max_slides, max_words, max_duration)
                bp["validation_status"] = status

                if status["all_passed"]:
                    passed += 1
                else:
                    failed += 1
                    issues = status.get("issues", []) or []
                    for issue in issues:
                        # Truncate values from the issue string so the
                        # aggregate key isn't unique per blueprint
                        # (e.g. "Body too long: 350 > 300 words" →
                        # "Body too long"). Keep the first ":" prefix.
                        key = str(issue).split(":", 1)[0].strip()
                        failure_reasons[key] = failure_reasons.get(key, 0) + 1
                    if len(failure_examples) < 5:
                        failure_examples.append(
                            {
                                "candidate_id": bp.get("candidate_id", "unknown"),
                                "title": (bp.get("title") or "")[:80],
                                "issues": issues[:5],
                            }
                        )
                    logger.info(
                        "[QCGates] FAILED %s — %s",
                        bp.get("candidate_id", "unknown"),
                        "; ".join(str(i) for i in issues[:3]),
                    )
                    # Apply score penalty
                    if "priority_score" in bp:
                        bp["priority_score"] = max(0, bp["priority_score"] - self.SCORE_PENALTY)
                    # 2026-07-14: hard-skip on "Missing required field:
                    # caption/body" — the only failure mode that produces
                    # a blueprint the publisher CANNOT ship (no text).
                    # Prior behavior: penalize score only → blueprint
                    # still reached VISUAL_READY and could be
                    # auto-approved with an empty caption.
                    # Concrete case: sports blueprint e434882d today —
                    # hook present, all platform captions empty, QC
                    # failed with this exact issue, but status advanced
                    # to VISUAL_READY anyway. Fired qc_collapse (0% for
                    # 2 runs) + nightly_schedule_missing_slot.
                    # Other issues (length, word count, banned phrases)
                    # keep the score-penalty behavior — they might
                    # recover or be operator-fixable.
                    if any("Missing required field: caption/body" in str(i) for i in issues):
                        bp["_skip_llm"] = True
                        logger.warning(
                            "[QCGates] hard-skip %s: empty caption/body "
                            "is unpublishable; marking _skip_llm=True "
                            "so downstream stages drop the blueprint",
                            bp.get("candidate_id", "unknown"),
                        )
            except Exception:
                logger.exception(
                    "[QCGates] Error validating blueprint %s",
                    bp.get("candidate_id", "unknown"),
                )
                bp["validation_status"] = {"all_passed": False, "error": True}
                failure_reasons["validation_exception"] = (
                    failure_reasons.get("validation_exception", 0) + 1
                )
                failed += 1

        total = passed + failed
        rate = f"{passed / total:.1%}" if total else "n/a"
        logger.info(
            "[QCGates] %d/%d passed (%s), %d failed; reasons=%s",
            passed,
            total,
            rate,
            failed,
            sorted(failure_reasons.items(), key=lambda kv: -kv[1]) or "[]",
        )

        context.setdefault("run_stats", {})["qc"] = {
            "passed": passed,
            "failed": failed,
            "total": total,
            "pass_rate": rate,
            "failure_reasons": failure_reasons,
            "failure_examples": failure_examples,
            # Surface the upstream-skip count so the dashboard can
            # distinguish "writing produced bad blueprints" from
            # "writing correctly declined to write un-publishable
            # source material". Pre-fix these were conflated.
            "excluded_skip_llm": excluded,
        }

        return context

    def _validate(
        self,
        bp: dict[str, Any],
        max_slides: int,
        max_words: int,
        max_duration: int,
    ) -> dict[str, Any]:
        issues: list[str] = []

        # Gate 1: Claim coverage
        claims_passed = self._check_claims(bp, issues)

        # Gate 2: Template constraints
        constraints_passed = self._check_constraints(
            bp,
            max_slides,
            max_words,
            max_duration,
            issues,
        )

        # Gate 3: Content completeness
        completeness_passed = self._check_completeness(bp, issues)

        return {
            "claims_passed": claims_passed,
            "constraints_passed": constraints_passed,
            "completeness_passed": completeness_passed,
            "all_passed": claims_passed and constraints_passed and completeness_passed,
            "issues": issues,
        }

    @staticmethod
    def _check_claims(bp: dict[str, Any], issues: list[str]) -> bool:
        """Every must_cite claim needs ≥1 source URL."""
        claims = bp.get("claims", [])
        sources = bp.get("sources", bp.get("source_urls", []))
        if not claims:
            return True

        for claim in claims:
            if not isinstance(claim, dict):
                continue
            if claim.get("must_cite") and not sources:
                issues.append(f"Uncited must_cite claim: {claim.get('text', '')[:60]}")
                return False
        return True

    @staticmethod
    def _check_constraints(
        bp: dict[str, Any],
        max_slides: int,
        max_words: int,
        max_duration: int,
        issues: list[str],
    ) -> bool:
        ok = True
        fmt = bp.get("format", "")

        # Slide count for carousels
        slides = bp.get("slides", [])
        if fmt == "carousel" and len(slides) > max_slides:
            issues.append(f"Too many slides: {len(slides)} > {max_slides}")
            ok = False

        # Word count — RENDER #5 fix (2026-06-13): use the same caption-lookup
        # chain as `_check_completeness` below (lines 248-256) so the word-count
        # check sees the same string the completeness check sees. Pre-fix this
        # block read `bp.get("body")` directly which is empty in every
        # production blueprint shape since Sprint 64 — so word_count was always
        # 0 and the "too long" check never fired.
        content = bp.get("content", {})
        body = (
            bp.get("caption", "")
            or bp.get("body", "")
            or (content.get("caption", "") if isinstance(content, dict) else "")
            or (
                content.get("instagram", {}).get("caption", "") if isinstance(content, dict) else ""
            )
        )
        if isinstance(body, str):
            word_count = len(body.split())
            if word_count > max_words:
                issues.append(f"Body too long: {word_count} > {max_words} words")
                ok = False

        # Duration for reels
        duration = bp.get("duration_seconds", 0)
        if fmt == "reel" and duration > max_duration:
            issues.append(f"Reel too long: {duration}s > {max_duration}s")
            ok = False

        return ok

    @staticmethod
    def _check_completeness(bp: dict[str, Any], issues: list[str]) -> bool:
        ok = True

        # Hook is required
        content = bp.get("content", {})
        hook = bp.get("hook", "") or (content.get("hook", "") if isinstance(content, dict) else "")
        if not hook:
            issues.append("Missing required field: hook")
            ok = False

        # Caption or body text required. Accept any of the shapes used by
        # the writing stages: top-level caption/body (legacy), content.caption
        # (base_writing.write_story_llm), or content.instagram.caption (the
        # platform-adaptation nested form).
        body = (
            bp.get("caption", "")
            or bp.get("body", "")
            or (content.get("caption", "") if isinstance(content, dict) else "")
            or (
                content.get("instagram", {}).get("caption", "") if isinstance(content, dict) else ""
            )
        )
        if not body:
            issues.append("Missing required field: caption/body")
            ok = False

        # Source URL required
        source_url = bp.get("source_url", "") or bp.get("canonical_url", "")
        sources = bp.get("sources", bp.get("source_urls", []))
        if not source_url and not sources:
            issues.append("No source URLs")
            ok = False

        return ok
