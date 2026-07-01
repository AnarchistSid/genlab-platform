"""Tests for the run_strategist CLI (Batch 2 shipping tonight).

Focus: argparse contracts + dry-run mode works without any prod
dependencies. Full integration test (real DB + real LLM) is deferred
to prod smoke testing.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

# scripts/ isn't on the default import path; add it here.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

import run_strategist  # noqa: E402


class TestArgParsing:
    def test_default_no_args(self):
        args = run_strategist._parse_args([])
        assert args.niche is None
        assert args.week_of is None
        assert args.dry_run is False

    def test_niche_filter(self):
        args = run_strategist._parse_args(["--niche", "gaming"])
        assert args.niche == "gaming"

    def test_niche_whitelist_enforced(self):
        with pytest.raises(SystemExit):
            run_strategist._parse_args(["--niche", "not_a_real_niche"])

    def test_dry_run_flag(self):
        args = run_strategist._parse_args(["--dry-run"])
        assert args.dry_run is True

    def test_week_of_arg(self):
        args = run_strategist._parse_args(["--week-of", "2026-06-29"])
        assert args.week_of == "2026-06-29"


class TestWeekOfResolution:
    def test_valid_iso_date(self):
        # 2026-06-30 is a Tuesday; Monday of that week is 2026-06-29
        assert run_strategist._resolve_week_of("2026-06-30") == date(2026, 6, 29)

    def test_defaults_to_current_week_monday(self):
        result = run_strategist._resolve_week_of(None)
        assert result.weekday() == 0

    def test_invalid_iso_date_exits_2(self):
        with pytest.raises(SystemExit) as exc:
            run_strategist._resolve_week_of("not-a-date")
        assert exc.value.code == 2


class TestDryRun:
    def test_dry_run_no_db_no_llm(self, monkeypatch):
        """Dry-run should succeed without DATABASE_URL and without touching
        the Anthropic API. Uses _MinimalCollector + _DryRunLLM + _NullPersister."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        exit_code = run_strategist.main(["--dry-run", "--niche", "ai_creators"])
        assert exit_code == 0

    def test_dry_run_multi_niche(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        exit_code = run_strategist.main(["--dry-run"])
        assert exit_code == 0


class TestDryRunLLM:
    def test_generates_valid_schema_conforming_json(self):
        """The dry-run canned response must parse via StrategistReport,
        otherwise dry-run mode is useless for smoke-testing the pipeline."""
        import json

        from genlab_core.intelligence.proposal_schema import StrategistReport

        llm = run_strategist._DryRunLLM()
        result = llm.generate_report("system", "user")
        # Should be parseable to a valid StrategistReport (need to inject
        # trusted niche_id + week_of, which is what the real orchestrator does)
        raw = json.loads(result.text)
        raw["niche_id"] = "ai_creators"
        raw["week_of"] = "2026-06-29"
        report = StrategistReport.model_validate(raw)
        assert report.niche_id == "ai_creators"
        # Dry-run summary is explicit — verifies the operator won't act on it
        assert "DRY-RUN" in report.weekly_summary
        assert result.cost_usd == 0.0


class TestExitCodeSemantics:
    def test_all_failed_returns_1(self, monkeypatch):
        """If every attempted niche's Strategist run fails, exit code is 1
        (so systemd shows red + failure-alert fires). Partial success is
        exit 0."""
        monkeypatch.delenv("DATABASE_URL", raising=False)

        # Simulate a Strategist that always fails
        from genlab_core.intelligence.strategist import RunOutcome

        class _AlwaysFailStrategist:
            def run_for_niche(self, niche, week):
                return RunOutcome(
                    niche_id=niche, week_of=week, status="llm_call_failed", error="mock"
                )

        # Patch the pipeline builder to return our failing strategist
        def _fake_build():
            return _AlwaysFailStrategist(), None

        monkeypatch.setattr(run_strategist, "_build_pipeline", _fake_build)
        # Also patch the dry-run mode; we want the REAL path to hit our failing strategist
        exit_code = run_strategist._run_all(
            ["ai_creators", "gaming"], date(2026, 6, 29), dry_run=False
        )
        assert exit_code == 1

    def test_partial_success_returns_0(self, monkeypatch):
        """If ANY niche succeeds, exit is 0 — transient outages shouldn't
        page the operator when 4 of 5 niches worked."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        from genlab_core.intelligence.strategist import RunOutcome

        counter = {"i": 0}

        class _PartialStrategist:
            def run_for_niche(self, niche, week):
                counter["i"] += 1
                if counter["i"] == 1:
                    return RunOutcome(
                        niche_id=niche, week_of=week, status="persisted", report_id="fake"
                    )
                return RunOutcome(
                    niche_id=niche, week_of=week, status="llm_call_failed", error="mock"
                )

        def _fake_build():
            return _PartialStrategist(), None

        monkeypatch.setattr(run_strategist, "_build_pipeline", _fake_build)
        exit_code = run_strategist._run_all(
            ["ai_creators", "gaming"], date(2026, 6, 29), dry_run=False
        )
        assert exit_code == 0
