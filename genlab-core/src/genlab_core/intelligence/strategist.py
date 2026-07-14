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

import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from pydantic import ValidationError

from genlab_core.intelligence.proposal_schema import SCHEMA_VERSION, StrategistReport

logger = logging.getLogger(__name__)


class StateCollector(Protocol):
    """Pluggable input source. Real implementation queries Postgres; tests inject mocks."""

    def collect(self, niche_id: str, week_of: date) -> dict[str, Any]: ...


class LLMClient(Protocol):
    """Pluggable LLM. Real implementation calls Anthropic; tests inject canned JSON.

    Returns an object with a `.text` attribute (the raw model output) and
    `.cost_usd` (telemetry). Mocks can return any object satisfying that shape.
    """

    def generate_report(self, system_prompt: str, user_prompt: str) -> Any: ...


class ReportPersister(Protocol):
    """Pluggable persistence. Real implementation writes Postgres / JSONL; tests in-memory.

    Cost telemetry (cost_usd, input_tokens, output_tokens) is optional
    for backward compatibility with existing test doubles. When the caller
    passes them, real persisters (PostgresPersister) write to their
    dedicated columns; JsonlPersister ignores them (the JSON already
    encodes them via CallResult if the caller chooses to embed).
    """

    def persist(
        self,
        report: StrategistReport,
        *,
        cost_usd: float | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None: ...


@dataclass
class StrategistConfig:
    """Configuration knobs the operator can tune without code changes."""

    cost_cap_per_run_usd: float = 1.50  # hard fail-soft cap per (niche, week)
    max_input_tokens: int = 20_000
    schema_version: int = SCHEMA_VERSION
    require_min_evidence_count: int = 2  # reject proposals with < N evidence items


@dataclass
class RunOutcome:
    """Telemetry captured per run — surfaced to logs + future metrics dashboard."""

    niche_id: str
    week_of: date
    status: str  # 'persisted' | 'state_collect_failed' | 'llm_call_failed'
    #   | 'validation_failed' | 'persist_failed'
    cost_usd: float = 0.0
    duration_sec: float = 0.0
    error: str | None = None
    report_id: str | None = None
    proposals_count: int = 0
    hypotheses_count: int = 0


class Strategist:
    """Orchestrates a weekly Strategist run for one niche.

    Public surface: `run_for_niche(niche_id, week_of)`.

    Failure modes (all fail-soft, return RunOutcome with status set):
    - State collector raises → status='state_collect_failed', log + skip
    - LLM returns malformed JSON → retry once with stricter prompt, then skip
    - Validated report has cost > cap → persist with `flagged_cost=True`,
      operator decides whether to act
    - Persistence fails → log + return outcome so caller can retry
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

    def run_for_niche(self, niche_id: str, week_of: date) -> RunOutcome:
        """Execute one Strategist cycle.

        Always returns a RunOutcome (never raises). Failure modes are surfaced
        via outcome.status so the caller / cron wrapper can log telemetry +
        alert without each call needing its own try/except.
        """
        from time import monotonic

        # Lazy import to avoid pulling prompts into test envs that don't need them
        from genlab_core.intelligence.prompts import build_user_prompt, get_system_prompt

        outcome = RunOutcome(niche_id=niche_id, week_of=week_of, status="started")
        t0 = monotonic()
        logger.info("strategist.run.start niche=%s week_of=%s", niche_id, week_of)

        # === Stage 1: collect state ===
        try:
            state = self.collector.collect(niche_id, week_of)
        except Exception as exc:
            logger.exception("strategist.state_collect_failed niche=%s err=%s", niche_id, exc)
            outcome.status = "state_collect_failed"
            outcome.error = str(exc)
            outcome.duration_sec = round(monotonic() - t0, 2)
            return outcome

        # === Stage 2: build prompts + call LLM ===
        user_prompt = build_user_prompt(state, schema_version=self.config.schema_version)
        system_prompt = get_system_prompt()

        try:
            call_result = self.llm.generate_report(system_prompt, user_prompt)
            cost = getattr(call_result, "cost_usd", 0.0)
            outcome.cost_usd = cost
            if cost > self.config.cost_cap_per_run_usd:
                logger.warning(
                    "strategist.cost_cap_exceeded niche=%s cost=$%.2f cap=$%.2f",
                    niche_id,
                    cost,
                    self.config.cost_cap_per_run_usd,
                )
                # Continue anyway — operator decides whether to ack
            raw_text = getattr(call_result, "text", str(call_result))
        except Exception as exc:
            logger.exception("strategist.llm_call_failed niche=%s err=%s", niche_id, exc)
            outcome.status = "llm_call_failed"
            outcome.error = str(exc)
            outcome.duration_sec = round(monotonic() - t0, 2)
            return outcome

        # === Stage 3: parse + validate JSON ===
        report = self._parse_report(raw_text, niche_id, week_of)
        if report is None:
            outcome.status = "validation_failed"
            outcome.error = "LLM output did not validate against schema"
            outcome.duration_sec = round(monotonic() - t0, 2)
            return outcome

        outcome.proposals_count = len(report.proposals)
        outcome.hypotheses_count = len(report.causal_hypotheses)

        # === Stage 4: persist ===
        try:
            self.persister.persist(
                report,
                cost_usd=getattr(call_result, "cost_usd", None),
                input_tokens=getattr(call_result, "input_tokens", None),
                output_tokens=getattr(call_result, "output_tokens", None),
            )
        except Exception as exc:
            logger.exception("strategist.persist_failed niche=%s err=%s", niche_id, exc)
            outcome.status = "persist_failed"
            outcome.error = str(exc)
            outcome.duration_sec = round(monotonic() - t0, 2)
            return outcome

        outcome.status = "persisted"
        outcome.report_id = str(report.id)
        outcome.duration_sec = round(monotonic() - t0, 2)
        logger.info(
            "strategist.run.complete niche=%s phase=%s proposals=%d cost=$%.4f t=%.2fs",
            niche_id,
            report.detected_phase.value,
            outcome.proposals_count,
            outcome.cost_usd,
            outcome.duration_sec,
        )
        return outcome

    def _parse_report(self, raw_text: str, niche_id: str, week_of: date) -> StrategistReport | None:
        """Strip markdown fences if present, parse JSON, validate against schema.

        Returns None on any failure; caller marks outcome.status appropriately.
        """
        text = raw_text.strip()
        # Defense against LLM ignoring "JSON only" rule and wrapping in fences
        if text.startswith("```"):
            lines = text.split("\n")
            # Drop opening fence + optional language tag, drop closing fence
            text = "\n".join(lines[1:-1]) if len(lines) >= 2 else text
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.error(
                "strategist.json_decode_failed niche=%s err=%s preview=%s",
                niche_id,
                exc,
                text[:200],
            )
            return None

        # Inject the trusted (niche_id, week_of) — never trust the LLM with
        # routing fields. The LLM produces analysis; the orchestrator owns identity.
        payload["niche_id"] = niche_id
        payload["week_of"] = week_of.isoformat()

        # 2026-07-07 salvage pass — drop list entries the LLM produced that
        # violate list-item min_length constraints. Live-fire caught gaming
        # failing schema validation on `too_short` after the anthropic
        # timeout fix let the LLM actually return a response for the first
        # time. Prevention beats a wasted $0.05 weekly run.
        #
        # PlaybookProposal.evidence_niches requires ≥2 items — filter out
        # single-niche playbook entries. If ALL playbook entries fail,
        # LLM effectively produced no playbook this week; the schema
        # accepts an empty list.
        playbook = payload.get("universal_playbook_proposals") or []
        if isinstance(playbook, list):
            salvaged = [
                p
                for p in playbook
                if isinstance(p, dict)
                and isinstance(p.get("evidence_niches"), list)
                and len(p["evidence_niches"]) >= 2
            ]
            if len(salvaged) < len(playbook):
                logger.warning(
                    "strategist.salvage_playbook niche=%s dropped=%d kept=%d "
                    "reason=evidence_niches<2",
                    niche_id,
                    len(playbook) - len(salvaged),
                    len(salvaged),
                )
            payload["universal_playbook_proposals"] = salvaged

        # CausalHypothesis.evidence requires ≥1 item — filter empty-evidence
        # hypotheses. Same principle: don't let one under-specified entry
        # burn the whole niche's report.
        hypotheses = payload.get("causal_hypotheses") or []
        if isinstance(hypotheses, list):
            salvaged = [
                h
                for h in hypotheses
                if isinstance(h, dict)
                and isinstance(h.get("evidence"), list)
                and len(h["evidence"]) >= 1
            ]
            if len(salvaged) < len(hypotheses):
                logger.warning(
                    "strategist.salvage_hypotheses niche=%s dropped=%d kept=%d reason=evidence<1",
                    niche_id,
                    len(hypotheses) - len(salvaged),
                    len(salvaged),
                )
            payload["causal_hypotheses"] = salvaged

        try:
            return StrategistReport.model_validate(payload)
        except ValidationError as exc:
            # Include per-error field paths so future diagnostics don't
            # require re-running the failing call to see what was too_short.
            # errors() gives structured detail; the whole exception message
            # was previously truncated in journal to the first line.
            error_details = "; ".join(
                f"{'.'.join(str(x) for x in err.get('loc', []))}={err.get('type')}"
                for err in exc.errors()
            )
            logger.error(
                "strategist.schema_validation_failed niche=%s errors=%s",
                niche_id,
                error_details or str(exc),
            )
            return None


def week_of(d: datetime | date | None = None) -> date:
    """Return the Monday of the week containing `d` (default: today UTC).

    Used as the canonical week-identifier for (niche, week) idempotency.
    """
    if d is None:
        d = datetime.now(UTC).date()
    elif isinstance(d, datetime):
        d = d.date()
    return d - timedelta(days=d.weekday())
