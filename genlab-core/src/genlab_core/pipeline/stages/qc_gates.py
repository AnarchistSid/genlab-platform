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

logger = logging.getLogger(__name__)


class QCGates:
    """Pre-render quality gate stage.

    Reads: context['blueprints'], context['niche_config']
    Writes: context['blueprints'][*]['validation_status'], context['run_stats']['qc']
    """

    # Default constraints (overridden by niche_config.templates if present)
    DEFAULT_MAX_SLIDES = 10
    DEFAULT_MAX_WORDS = 300
    DEFAULT_MAX_DURATION = 90
    SCORE_PENALTY = 0.3

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        blueprints = context.get("blueprints", [])
        if not blueprints:
            logger.info("[QCGates] No blueprints to validate")
            return context

        config = context.get("niche_config", {})
        templates_cfg = config.get("templates", {})
        max_slides = templates_cfg.get("max_slides", self.DEFAULT_MAX_SLIDES)
        max_words = templates_cfg.get("max_words", self.DEFAULT_MAX_WORDS)
        max_duration = templates_cfg.get("max_duration_seconds", self.DEFAULT_MAX_DURATION)

        passed = 0
        failed = 0

        for bp in blueprints:
            try:
                status = self._validate(bp, max_slides, max_words, max_duration)
                bp["validation_status"] = status

                if status["all_passed"]:
                    passed += 1
                else:
                    failed += 1
                    # Apply score penalty
                    if "priority_score" in bp:
                        bp["priority_score"] = max(
                            0, bp["priority_score"] - self.SCORE_PENALTY
                        )
            except Exception:
                logger.exception(
                    "[QCGates] Error validating blueprint %s",
                    bp.get("candidate_id", "unknown"),
                )
                bp["validation_status"] = {"all_passed": False, "error": True}
                failed += 1

        total = passed + failed
        rate = f"{passed / total:.1%}" if total else "n/a"
        logger.info(
            "[QCGates] %d/%d passed (%s), %d failed",
            passed, total, rate, failed,
        )

        context.setdefault("run_stats", {})["qc"] = {
            "passed": passed,
            "failed": failed,
            "total": total,
            "pass_rate": rate,
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
            bp, max_slides, max_words, max_duration, issues,
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

        # Word count
        body = bp.get("body", bp.get("caption", ""))
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
        required = ["hook", "body"]

        for field_name in required:
            val = bp.get(field_name, bp.get("caption", ""))
            if not val:
                alt = bp.get("caption", "") if field_name == "body" else ""
                if not alt:
                    issues.append(f"Missing required field: {field_name}")
                    ok = False

        # At least one source
        sources = bp.get("sources", bp.get("source_urls", []))
        if not sources:
            issues.append("No source URLs")
            ok = False

        return ok
