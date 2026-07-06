"""Pin the 2026-07-07 salvage pass on `Strategist._parse_report`.

Live-fire caught: after PR `05738fe6` fixed the anthropic timeout,
gaming was the first niche where the LLM actually returned a response
that failed schema validation. Error: `too_short` on some list field
in the pydantic model. Rather than lose the entire niche's report to
one under-specified LLM entry, the salvage pass filters known
too-short-list cases BEFORE `model_validate`.

Two salvage rules:
  * `universal_playbook_proposals[].evidence_niches` needs ≥2 items
    (Playbook = cross-niche pattern; single-niche entries aren't playbook material).
  * `causal_hypotheses[].evidence` needs ≥1 item (evidence-free
    hypotheses are unfalsifiable).

If this pin regresses, we're back to any single bad list entry
burning the whole niche's report.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from genlab_core.intelligence.strategist import Strategist


def _make_strategist() -> Strategist:
    """Construct with mocks for LLM + persister — we only need `_parse_report`."""
    from genlab_core.intelligence.strategist import StrategistConfig

    return Strategist(
        llm=MagicMock(),
        collector=MagicMock(),
        persister=MagicMock(),
        config=StrategistConfig(),
    )


def _valid_base_payload() -> dict:
    """Minimum payload that PASSES schema validation (all lists satisfy min_length)."""
    return {
        "detected_phase": "BOOTSTRAP",
        "phase_evidence": "First-run week; no baseline metrics available yet.",
        "proposals": [],
        "causal_hypotheses": [],
        "universal_playbook_proposals": [],
        "weekly_summary": (
            "First-run week: baseline metrics not yet ingested; "
            "no strategic changes proposed until data lands."
        ),
    }


class TestPlaybookSalvage:
    """PlaybookProposal.evidence_niches must have ≥2 items."""

    def test_single_niche_playbook_entry_filtered(self, monkeypatch) -> None:
        """One entry with 1 evidence_niche gets dropped, valid one kept."""
        import json

        s = _make_strategist()
        payload = _valid_base_payload()
        payload["universal_playbook_proposals"] = [
            {  # Would fail schema — evidence_niches too short
                "pattern_text": "Novelty-driven arms outperform in bootstrap.",
                "evidence_niches": ["gaming"],  # only 1 — too short
                "confidence": "medium",
            },
            {  # Passes schema
                "pattern_text": "Question hooks outperform statement hooks.",
                "evidence_niches": ["gaming", "sports"],  # 2 — OK
                "confidence": "medium",
            },
        ]
        raw_text = json.dumps(payload)

        report = s._parse_report(raw_text, "gaming", date.today())

        assert report is not None, "salvage should let the valid entry through"
        assert len(report.universal_playbook_proposals) == 1
        assert report.universal_playbook_proposals[0].evidence_niches == ["gaming", "sports"]

    def test_all_playbook_entries_single_niche_returns_empty_list(self) -> None:
        """If every playbook entry has <2 niches, we still salvage to an empty list,
        not None — the report itself is otherwise valid."""
        import json

        s = _make_strategist()
        payload = _valid_base_payload()
        payload["universal_playbook_proposals"] = [
            {
                "pattern_text": "Pattern A observed only in gaming this week.",
                "evidence_niches": ["gaming"],
                "confidence": "low",
            },
            {
                "pattern_text": "Pattern B observed only in movies this week.",
                "evidence_niches": ["movies"],
                "confidence": "low",
            },
        ]
        raw_text = json.dumps(payload)

        report = s._parse_report(raw_text, "gaming", date.today())

        assert report is not None
        assert report.universal_playbook_proposals == []


class TestHypothesisSalvage:
    """CausalHypothesis.evidence must have ≥1 item."""

    def test_empty_evidence_hypothesis_dropped(self) -> None:
        import json

        s = _make_strategist()
        payload = _valid_base_payload()
        payload["causal_hypotheses"] = [
            {  # Would fail — evidence empty
                "pattern": "Something happened.",
                "hypothesis": "This is because of the reason X here.",
                "confidence": "low",
                "evidence": [],  # empty — too short
                "testable_prediction": "Something predictable.",
            },
            {  # Passes
                "pattern": "Real observation with data.",
                "hypothesis": "Compelling causal reason based on N samples.",
                "confidence": "medium",
                "evidence": ["N=42 gaming publishes, arm A won 30/42."],
                "testable_prediction": "If we ship arm A more, we should see wins hold.",
            },
        ]
        raw_text = json.dumps(payload)

        report = s._parse_report(raw_text, "gaming", date.today())

        assert report is not None
        assert len(report.causal_hypotheses) == 1
        assert report.causal_hypotheses[0].pattern == "Real observation with data."


class TestSalvageDoesNotMaskGenuineFailures:
    """The salvage only touches KNOWN list-item constraints — a real schema
    failure elsewhere still returns None + logs the specific field path."""

    def test_short_weekly_summary_still_fails(self, caplog) -> None:
        """weekly_summary min_length=50; a 10-char summary bypasses the salvage
        pass (not a list) and fails schema, returning None."""
        import json
        import logging

        s = _make_strategist()
        payload = _valid_base_payload()
        payload["weekly_summary"] = "too short"  # 9 chars, below min_length=50

        with caplog.at_level(logging.ERROR):
            report = s._parse_report(json.dumps(payload), "gaming", date.today())

        assert report is None
        # New logging includes structured error path — should NOT be a bare
        # "1 validation error" message
        assert any("weekly_summary" in rec.message for rec in caplog.records), (
            "expected new structured logging to name the failing field; "
            f"got: {[r.message for r in caplog.records]}"
        )
