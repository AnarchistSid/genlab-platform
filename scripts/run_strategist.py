#!/usr/bin/env python3
"""Weekly Strategist runner — one cycle per (niche, week).

CLI entry point for the Strategist meta-layer (see
docs/STRATEGIST-SPEC.md). Fires from `genlab-strategist.timer` weekly
on Sunday at 02:00 UTC (07:30 IST) — early enough that the operator
can review before Monday morning + late enough that the previous
week's engagement data has settled.

Per invocation:
  1. Instantiate PostgresStateCollector + AnthropicStrategistClient
     + PostgresPersister (the shipped-with-Strategist-1b triplet).
  2. For each active niche, run Strategist.run_for_niche() and log
     the RunOutcome.
  3. Report per-niche telemetry (cost, duration, proposals emitted,
     persist status) as a summary at end.
  4. Exit non-zero only if EVERY niche's run failed (partial success
     is normal — one niche's state collector failing shouldn't
     mask others' successful reports).

Fail-soft everywhere: LLM errors, DB errors, missing env vars — all
logged, none propagate. The Strategist is advisory; a bad run doesn't
block anything downstream.

## Usage

    # Default: run all active niches for the current week
    python -m scripts.run_strategist

    # Single niche (dev / debug)
    python -m scripts.run_strategist --niche ai_creators

    # Specific week (backfill / catch-up)
    python -m scripts.run_strategist --week-of 2026-06-29

    # Dry-run: build prompts but don't call LLM or persist
    python -m scripts.run_strategist --dry-run

## Exit codes

  * 0  — all runs succeeded (or all niches were skipped for known reasons)
  * 1  — every attempted run failed (LLM outage / DB outage — page)
  * 2  — CLI arg error (bad --niche, bad --week-of)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, date, datetime

logger = logging.getLogger("run_strategist")

# The 5 active niches — mirrors NICHE_DIR_NAMES in genlab_core.pipeline.cli.
# If a 6th niche launches, add it here (or refactor to import the canonical
# list from genlab_core.pipeline.cli). Keeping the list explicit for now
# because the CLI shouldn't fail silently when the niche registry drifts.
ACTIVE_NICHES = ["ai_creators", "gaming", "sports", "movies", "anime"]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--niche",
        choices=ACTIVE_NICHES,
        help="Run only this niche (default: all active niches).",
    )
    p.add_argument(
        "--week-of",
        help="ISO date within the target week (defaults to today). "
        "Used verbatim — the Strategist normalizes to Monday internally.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip LLM call and persist; just verify state collection works.",
    )
    return p.parse_args(argv)


def _resolve_week_of(arg: str | None) -> date:
    from genlab_core.intelligence.strategist import week_of

    if arg:
        try:
            d = date.fromisoformat(arg)
        except ValueError as exc:
            logger.error("Invalid --week-of value %r — expected ISO date (YYYY-MM-DD)", arg)
            raise SystemExit(2) from exc
        return week_of(d)
    return week_of()


def _build_pipeline() -> tuple:
    """Instantiate the Strategist + its three collaborators (Postgres + Anthropic + Postgres).

    Split out for testability — tests can mock any of the three by
    monkeypatching this factory.
    """
    import psycopg
    from genlab_core.intelligence.anthropic_client import AnthropicStrategistClient
    from genlab_core.intelligence.persister import PostgresPersister
    from genlab_core.intelligence.state_collector import PostgresStateCollector
    from genlab_core.intelligence.strategist import Strategist
    from psycopg.rows import dict_row

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        raise RuntimeError("DATABASE_URL not set in env — refusing to run headless")

    conn = psycopg.connect(dsn, row_factory=dict_row)
    collector = PostgresStateCollector(conn)
    llm = AnthropicStrategistClient()
    persister = PostgresPersister(conn)
    return Strategist(collector=collector, llm=llm, persister=persister), conn


class _DryRunLLM:
    """No-op LLM for --dry-run mode. Returns a canned valid-shape response
    so the orchestrator's parse + persist stages exercise correctly without
    burning Anthropic credits or hitting the wire."""

    class _Result:
        def __init__(self, text: str):
            self.text = text
            self.cost_usd = 0.0

    def generate_report(self, system_prompt: str, user_prompt: str):
        import json

        canned = {
            "detected_phase": "BOOTSTRAP",
            "phase_evidence": "DRY-RUN: state collector returned a snapshot but LLM was not called.",
            "weekly_summary": (
                "DRY-RUN mode — this report was synthesized by the CLI without an LLM call. "
                "Do not act on it; re-run without --dry-run for a real analysis."
            ),
            "proposals": [],
            "causal_hypotheses": [],
            "universal_playbook_proposals": [],
        }
        return self._Result(json.dumps(canned))


class _NullPersister:
    """No-op persister for --dry-run — logs the report but doesn't write.

    Accepts the same cost-telemetry kwargs as the real persister
    (added 2026-07-14) so Strategist.run_for_niche can call
    ``persist(report, cost_usd=..., input_tokens=..., output_tokens=...)``
    without a TypeError from the dry-run path.
    """

    def persist(
        self,
        report,
        *,
        cost_usd: float | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        logger.info(
            "[DRY-RUN] would persist report id=%s niche=%s phase=%s proposals=%d "
            "cost_usd=%s in=%s out=%s",
            report.id,
            report.niche_id,
            report.detected_phase.value,
            len(report.proposals),
            cost_usd,
            input_tokens,
            output_tokens,
        )


def _run_all(niches: list[str], week: date, *, dry_run: bool) -> int:
    """Execute Strategist for each niche. Returns process exit code."""
    if dry_run:
        # Minimal setup — just the collector needs a DB. LLM + persister mocked.
        import psycopg
        from genlab_core.intelligence.state_collector import PostgresStateCollector
        from genlab_core.intelligence.strategist import Strategist
        from psycopg.rows import dict_row

        dsn = os.environ.get("DATABASE_URL", "").strip()
        conn = psycopg.connect(dsn, row_factory=dict_row) if dsn else None
        collector = PostgresStateCollector(conn) if conn else _MinimalCollector()
        strategist = Strategist(collector=collector, llm=_DryRunLLM(), persister=_NullPersister())
    else:
        strategist, _conn = _build_pipeline()

    successes = 0
    failures = 0
    for niche in niches:
        logger.info("Running Strategist for niche=%s week_of=%s", niche, week)
        outcome = strategist.run_for_niche(niche, week)
        if outcome.status == "persisted":
            successes += 1
            logger.info(
                "  ✓ %s → persisted (proposals=%d hypotheses=%d cost=$%.4f duration=%.2fs)",
                niche,
                outcome.proposals_count,
                outcome.hypotheses_count,
                outcome.cost_usd,
                outcome.duration_sec,
            )
        else:
            failures += 1
            logger.warning(
                "  ✗ %s → %s (error=%s cost=$%.4f duration=%.2fs)",
                niche,
                outcome.status,
                outcome.error,
                outcome.cost_usd,
                outcome.duration_sec,
            )

    total = successes + failures
    logger.info(
        "Strategist week %s summary: %d/%d niches persisted (%d failures)",
        week,
        successes,
        total,
        failures,
    )
    # Uses the shared runner_healthcheck helper (2026-07-02, PR #657)
    # with strategist-specific thresholds preserved from the original
    # ``return 1 if total > 0 and successes == 0 else 0`` semantics:
    #
    #   * max_error_rate=0.999 — only 100% failure counts. Rationale:
    #     Strategist LLM calls are expensive and flaky (Anthropic
    #     rate limits, transient timeouts). 1-2 failed niches out of
    #     5 is often environmental, not a code bug. Only ALL-fail
    #     signals something worth alerting on.
    #   * high_error_exit_code=1 — preserves the original contract.
    #     Systemd OnFailure fires on any nonzero regardless of value.
    #   * min_total_for_check=1 — total=0 is legit (all niches
    #     skipped due to config), so respect that; anything above
    #     0 is check-eligible.
    from genlab_core.runner_healthcheck import exit_code_from_health

    return exit_code_from_health(
        total=total,
        errors=failures,
        min_total_for_check=1,
        max_error_rate=0.999,
        high_error_exit_code=1,
    )


class _MinimalCollector:
    """Fallback collector for --dry-run when DATABASE_URL is unset. Returns
    minimal state so the CLI can be smoke-tested without any prod dependency."""

    def collect(self, niche_id: str, week_of: date) -> dict:
        return {
            "niche_id": niche_id,
            "week_of": week_of.isoformat(),
            "run_at": datetime.now(UTC).isoformat(),
        }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    week = _resolve_week_of(args.week_of)
    niches = [args.niche] if args.niche else ACTIVE_NICHES
    return _run_all(niches, week, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
