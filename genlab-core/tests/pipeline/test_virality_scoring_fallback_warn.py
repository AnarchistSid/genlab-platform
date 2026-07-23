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


class TestDecisionTraceEmission:
    """2026-07-23: ViralityScoring must emit a decision trace so
    operators can diagnose ``avg_score=0.0`` mysteries post-hoc.

    Motivating incident: today's movies pipeline reported
    ``virality.avg_score=0.0`` in run_report but decision_traces.jsonl
    contained ONLY VideoGate rows — no way to tell WHICH stories were
    scored or WHAT patterns matched without re-running.
    """

    def test_emits_aggregate_trace(self, tmp_path, monkeypatch) -> None:
        """One aggregate trace with stage=ViralityScoring, scored
        count, avg_score, and per_story breakdown in metadata."""
        stage = ViralityScoring()
        traces: list[dict] = []

        def _capture(context, **kwargs):
            traces.append(kwargs)

        monkeypatch.setattr(
            "genlab_core.observability.decision_trace.record_decision",
            _capture,
        )
        monkeypatch.setattr(
            "genlab_core.pipeline.reasoning_trace.append_trace",
            lambda *a, **k: None,
        )

        stage.execute({
            "niche_id": "movies",
            "niche_config": {
                "scoring_weights": {
                    "virality_scoring": {
                        "patterns": {"named_tool": r"\btrailer\b"},
                    }
                }
            },
            "stories": [
                {"title": "Final Trailer for Movie X", "hook": "wow"},
                {"title": "No matches here", "hook": "nothing"},
            ],
        })

        virality_traces = [t for t in traces if t.get("stage") == "ViralityScoring"]
        assert virality_traces, "expected one ViralityScoring decision trace"
        trace = virality_traces[0]
        assert trace["metadata"]["scored"] == 2
        assert trace["metadata"]["avg_score"] > 0.0
        per_story = trace["metadata"]["per_story"]
        assert len(per_story) == 2
        # First story matched named_tool, second didn't.
        assert per_story[0]["score"] > 0.0
        assert "named_tool" in per_story[0]["matched"]
        assert per_story[1]["score"] == 0.0
        assert per_story[1]["matched"] == []

    def test_warning_decision_when_avg_below_gate_floor(
        self, tmp_path, monkeypatch
    ) -> None:
        """When avg_score < 0.05 (the auto_approval_gate floor), the
        trace decision is 'warning' — so filtering
        ``decision=='warning' AND stage=='ViralityScoring'`` surfaces
        "gate would reject everything" runs at a glance."""
        stage = ViralityScoring()
        traces: list[dict] = []
        monkeypatch.setattr(
            "genlab_core.observability.decision_trace.record_decision",
            lambda context, **kwargs: traces.append(kwargs),
        )
        monkeypatch.setattr(
            "genlab_core.pipeline.reasoning_trace.append_trace",
            lambda *a, **k: None,
        )

        stage.execute({
            "niche_id": "movies",
            "niche_config": {
                "scoring_weights": {
                    "virality_scoring": {
                        "patterns": {"named_tool": r"\bunmatchable_needle\b"},
                    }
                }
            },
            "stories": [
                {"title": "Doesn't match anything", "hook": "nothing"},
            ],
        })

        virality = [t for t in traces if t.get("stage") == "ViralityScoring"]
        assert virality
        assert virality[0]["decision"] == "warning", (
            "avg_score < 0.05 must produce decision='warning' so operators "
            "can grep decision_traces for gate-rejection risk"
        )
