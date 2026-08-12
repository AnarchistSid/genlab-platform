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


# 2026-08-12: per-type `proposed` field shape validator.
#
# The strategist prompt (`1cd74f5a`) instructs the LLM to emit
# type-specific `proposed` values (number for reward_weight/
# gate_threshold/novelty_rate; dict for arm_add; enum-string for
# phase_shift; prose for playbook_update/manual_action). Prompts
# are guidance — the LLM sometimes ignores them and writes prose
# where a number is required. Prior to this validator, malformed
# proposals landed in `strategist_reports.proposals` with the wrong
# shape and every downstream auto-accept classifier silently
# rejected them at consumer time (skip:non_numeric_proposed).
# Result: prompt fix looked "shipped" but produced zero usable
# proposals for weeks (F-QB-0702-adjacent class-of-bug).
#
# Salvage-pass pattern (parallel to _salvage_playbook /
# _salvage_hypotheses at strategist.py:247+): drop malformed
# proposals with a WARNING, keep the well-formed ones. Fail-soft
# — one bad proposal doesn't invalidate the whole weekly report.

_PHASE_ENUM_VALUES: frozenset[str] = frozenset(
    {"BOOTSTRAP", "GROWTH", "OPTIMIZE", "MONETIZE", "DEFEND"}
)


def _proposal_has_valid_proposed_shape(p: dict) -> tuple[bool, str]:
    """Validate a proposal's `proposed` field against its `type`.

    Returns (ok, reason). Reason is short (dedup key) when ok=False.

    * reward_weight / gate_threshold / novelty_rate -> `float(proposed)` must work
    * arm_add -> dict with arm_id (or JSON-string form per 2026-07-24 compat)
    * phase_shift -> string in the phase enum
    * playbook_update / manual_action -> non-empty string
    * Unknown types -> pass (outer Pydantic validator catches)
    """
    ptype = p.get("type") if isinstance(p, dict) else None
    proposed = p.get("proposed") if isinstance(p, dict) else None

    if ptype in ("reward_weight", "gate_threshold", "novelty_rate"):
        try:
            float(proposed)  # type: ignore[arg-type]
            return True, ""
        except (TypeError, ValueError):
            return False, f"{ptype}:non_numeric_proposed"

    if ptype == "arm_add":
        if isinstance(proposed, dict) and "arm_id" in proposed:
            return True, ""
        if isinstance(proposed, str) and proposed.strip().startswith("{"):
            # 2026-07-24 compat: LLM sometimes serialises the dict as a
            # JSON string. classify_arm_add parses this shape.
            try:
                parsed = json.loads(proposed)
                if isinstance(parsed, dict) and "arm_id" in parsed:
                    return True, ""
                return False, "arm_add:json_string_no_arm_id"
            except (json.JSONDecodeError, ValueError):
                return False, "arm_add:unparseable_json_string"
        return False, "arm_add:not_dict_or_json_string"

    if ptype == "phase_shift":
        if isinstance(proposed, str) and proposed.upper() in _PHASE_ENUM_VALUES:
            return True, ""
        return False, "phase_shift:not_enum_string"

    if ptype in ("playbook_update", "manual_action"):
        if isinstance(proposed, str) and len(proposed.strip()) > 0:
            return True, ""
        return False, f"{ptype}:empty_string"

    # Unknown type — let outer Pydantic ProposalType enum catch it.
    return True, ""


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

        # 2026-08-12 salvage — per-type `proposed` field shape check.
        # See _proposal_has_valid_proposed_shape docstring for the
        # motivating class-of-bug + shape rules per type. Failing this
        # check means the downstream auto-accept classifier would silently
        # reject the proposal at consumer time (skip:non_numeric_proposed
        # etc.), producing no user-visible signal. Salvage here so the
        # operator sees exactly what got dropped and why.
        proposals = payload.get("proposals") or []
        if isinstance(proposals, list):
            salvaged_proposals: list[Any] = []
            drop_reason_counter: dict[str, int] = {}
            for p in proposals:
                ok, reason = _proposal_has_valid_proposed_shape(
                    p if isinstance(p, dict) else {}
                )
                if ok:
                    salvaged_proposals.append(p)
                else:
                    drop_reason_counter[reason] = (
                        drop_reason_counter.get(reason, 0) + 1
                    )
            dropped_n = len(proposals) - len(salvaged_proposals)
            if dropped_n:
                # Compact per-reason summary so a future operator
                # digging into a low-proposal week sees the shape
                # of the LLM's drift.
                reason_summary = ",".join(
                    f"{r}={n}" for r, n in sorted(drop_reason_counter.items())
                )
                logger.warning(
                    "strategist.salvage_proposals niche=%s dropped=%d kept=%d "
                    "reasons=%s",
                    niche_id,
                    dropped_n,
                    len(salvaged_proposals),
                    reason_summary,
                )
            payload["proposals"] = salvaged_proposals

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
