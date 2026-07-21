"""Pin tests for the DEFAULT_PATTERNS fallback WARN in
ViralityScoring.execute (added 2026-07-21 as rule #17 sibling).

Silent fallback to AI-industry defaults was the class-of-bug root
cause of the 4-sports-blueprints-stuck incident. WARN log alerts
operators when a new niche is added without its own virality_scoring
section — should reach the eyes of anyone bootstrapping a new niche
via `create_niche` scaffolding.
"""

from __future__ import annotations

import logging

from genlab_core.pipeline.stages.virality_scoring import ViralityScoring


class TestFallbackWarn:
    def test_non_ai_niche_missing_patterns_warns(self, caplog) -> None:
        """The exact regression that took 4 sports blueprints out of
        the schedule: niche has no virality_scoring.patterns, module
        falls back to DEFAULT_PATTERNS silently. Must WARN now."""
        stage = ViralityScoring()
        with caplog.at_level(logging.WARNING):
            stage.execute({
                "niche_id": "sports",
                "niche_config": {},  # No virality_scoring section
                "stories": [{"hook": "test", "title": "", "caption": ""}],
            })
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert any(
            "sports" in r.message and "DEFAULT_PATTERNS" in r.message
            for r in warnings
        ), (
            f"Expected WARN mentioning niche + DEFAULT_PATTERNS, got: "
            f"{[r.message for r in warnings]}"
        )

    def test_ai_creators_missing_patterns_does_not_warn(self, caplog) -> None:
        """ai_creators is the exception — DEFAULT_PATTERNS ARE its
        vocabulary, so no override is expected. Must NOT WARN
        (otherwise every prod pipeline run for ai_creators would emit
        noise, teaching operators to ignore the warning)."""
        stage = ViralityScoring()
        with caplog.at_level(logging.WARNING):
            stage.execute({
                "niche_id": "ai_creators",
                "niche_config": {},
                "stories": [{"hook": "test", "title": "", "caption": ""}],
            })
        warnings = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING
            and "DEFAULT_PATTERNS" in r.message
        ]
        assert not warnings, (
            f"ai_creators should NOT trigger the fallback WARN, but got: "
            f"{[r.message for r in warnings]}"
        )

    def test_niche_with_patterns_does_not_warn(self, caplog) -> None:
        """When the niche_config supplies a virality_scoring.patterns
        override (the FIXED state for sports/gaming/movies/anime post
        2026-07-21), no fallback WARN is emitted."""
        stage = ViralityScoring()
        with caplog.at_level(logging.WARNING):
            stage.execute({
                "niche_id": "sports",
                "niche_config": {
                    "virality_scoring": {
                        "patterns": {
                            "pop_culture_reference": r"\b(nba|nfl)\b",
                        },
                    },
                },
                "stories": [{"hook": "test", "title": "", "caption": ""}],
            })
        warnings = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING
            and "DEFAULT_PATTERNS" in r.message
        ]
        assert not warnings, (
            f"Niche with pattern override should NOT WARN, got: "
            f"{[r.message for r in warnings]}"
        )

    def test_unknown_niche_id_warns(self, caplog) -> None:
        """Belt-and-suspenders — if a caller doesn't set niche_id at all,
        we still WARN (defaults to 'unknown' → not ai_creators → WARN)."""
        stage = ViralityScoring()
        with caplog.at_level(logging.WARNING):
            stage.execute({
                "niche_config": {},  # No niche_id, no virality_scoring
                "stories": [{"hook": "test", "title": "", "caption": ""}],
            })
        warnings = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING
            and "DEFAULT_PATTERNS" in r.message
        ]
        assert warnings, "Missing niche_id should still trigger the fallback WARN"
