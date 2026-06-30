"""Strategist meta-layer — the LLM-based weekly manager above the optimizer stack.

The Strategist is intentionally a thin orchestration layer: it collects state,
prompts the LLM, validates output, persists results. The intelligence lives in
the prompt + schema (see prompts.py + proposal_schema.py).

Design principles:
1. **Read-only by default** — Strategist never modifies live config; it writes
   proposals that the operator approves.
2. **Fail-soft** — LLM errors log + skip this run; never block the pipeline.
3. **Idempotent per (niche, week)** — re-running for the same week supersedes
   the previous report; we keep history but the operator only acts on the
   latest.
4. **Auditable** — every input + output JSON is persisted; reproducible offline.

See docs/STRATEGIST-SPEC.md for the full design.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from genlab_core.intelligence.proposal_schema import StrategistReport

logger = logging.getLogger(__name__)


class StateCollector(Protocol):
    """Pluggable input source. Real implementation queries Postgres; tests inject mocks."""

    def collect(self, niche_id: str, week_of: date) -> dict: ...


class LLMClient(Protocol):
    """Pluggable LLM. Real implementation calls Anthropic; tests inject canned JSON."""

    def generate_report(self, system_prompt: str, user_prompt: str) -> str: ...


class ReportPersister(Protocol):
    """Pluggable persistence. Real implementation writes Postgres; tests use in-memory."""

    def persist(self, report: StrategistReport) -> None: ...


@dataclass
class StrategistConfig:
    """Configuration knobs the operator can tune without code changes."""

    cost_cap_per_run_usd: float = 1.50  # hard fail-soft cap per (niche, week)
    max_input_tokens: int = 20_000
    schema_version: int = 1
    require_min_evidence_count: int = 2  # reject proposals with < N evidence items


class Strategist:
    """Orchestrates a weekly Strategist run for one niche.

    Public surface is one method: `run_for_niche(niche_id, week_of)`.

    Failure modes (all fail-soft, return None and log):
    - State collector raises → skip this run, log error
    - LLM returns malformed JSON → retry once with stricter prompt, then skip
    - Validated report has cost > cap → persist with `flagged_cost=True`,
      operator decides whether to act
    - Persistence fails → log + return the report so caller can retry
    """

    def __init__(
        self,
        collector: StateCollector,
        llm: LLMClient,
        persister: ReportPersister,
        config: StrategistConfig | None = None,
    ):
        self.collector = collector
        self.llm = llm
        self.persister = persister
        self.config = config or StrategistConfig()

    def run_for_niche(self, niche_id: str, week_of: date) -> StrategistReport | None:
        """Execute one Strategist cycle. Returns the persisted report or None on failure."""
        logger.info("strategist.run.start niche=%s week_of=%s", niche_id, week_of)

        try:
            state = self.collector.collect(niche_id, week_of)
        except Exception as exc:
            logger.exception("strategist.state_collect_failed niche=%s err=%s", niche_id, exc)
            return None

        # PR Strategist-1 stops here. PR Strategist-2 will add:
        #   - prompts.build_user_prompt(state, schema_version)
        #   - self.llm.generate_report(system_prompt, user_prompt)
        #   - StrategistReport.model_validate_json(llm_output)
        #   - self.persister.persist(report)
        raise NotImplementedError(
            "Strategist execution body lands in PR Strategist-1; this is the "
            "skeleton shipped 2026-06-30 to lock the interfaces. See "
            "docs/STRATEGIST-SPEC.md §8 for the implementation plan."
        )


def week_of(d: datetime | date | None = None) -> date:
    """Return the Monday of the week containing `d` (default: today UTC).

    Used as the canonical week-identifier for (niche, week) idempotency.
    """
    if d is None:
        d = datetime.now(UTC).date()
    elif isinstance(d, datetime):
        d = d.date()
    return d - timedelta(days=d.weekday())
