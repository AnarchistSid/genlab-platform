"""Operator daily briefing (Phase 5.D, 2026-08-15).

Roadmap Task 5.D — cut operator's daily Gen Lab time from ~2h to
<30 min by shipping a 5-line "what needs your judgment today"
summary via email + a Mission Control card.

## Flow

  1. ``collect_state(conn)`` — aggregates ~7 signals from Postgres
     into a single dict (yesterday's publish counts by niche,
     top-alert count, calibration progress per niche, pending
     autonomous flag flips + strategist proposals, current cost).
  2. ``render_prompt(state)`` — compact JSON block + system
     framing telling Haiku to write 5 short lines focused on:
        what changed / what worked / what didn't / what needs
        your judgment / one metric to celebrate.
  3. ``call_llm(prompt)`` — AnthropicStrategistClient with
     caller_type='optional' so Phase 2.D budget gate can throttle
     without failing.
  4. ``BriefingResult`` — dataclass carrying summary_md + cost +
     the structured state that fed the LLM (persisted for
     provenance on the card).

## Fail-open at every layer

  * Any DB query failing → dict for that key is
    ``{"error": "..."}``; siblings still get collected.
  * LLM call failing / non-JSON → summary falls back to a
    deterministic plain-text render of the aggregate so the
    email always contains SOMETHING useful.
  * Runner catches BriefingError + persists a "briefing failed"
    row rather than exiting non-zero (rule #26).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Final

logger = logging.getLogger(__name__)


# Two-message system prompt — kept in module so tests can pin its
# shape without importing the runner.
_SYSTEM_PROMPT: Final[str] = (
    "You are the operator's morning briefing for Gen Lab, an "
    "autonomous social-media publishing agent across 5 channels. "
    "You will receive a JSON snapshot of yesterday's state. Reply "
    "with a Markdown briefing under 5 short lines. Focus on: "
    "(1) what changed yesterday, (2) what worked (one wins), "
    "(3) what didn't (one loss with actionable diagnosis), "
    "(4) what needs the operator's judgment today (be specific: "
    "list flag flips / strategist proposals / stuck queues), "
    "(5) one metric worth celebrating or worrying about. "
    "Do NOT hedge. Do NOT summarize the input format. Do NOT "
    "greet or sign off. Just the 5 lines."
)


@dataclass
class BriefingResult:
    """One completed briefing pass. ok=False when even the fallback
    render failed (unreachable in normal operation)."""
    ok: bool
    summary_md: str
    structured: dict[str, Any]
    llm_cost_usd: float = 0.0
    n_pending_flag_flips: int = 0
    n_pending_strategist_proposals: int = 0
    reason_code: str = ""

    def to_row(self) -> dict[str, Any]:
        return {
            "summary_md": self.summary_md,
            "structured": self.structured,
            "llm_cost_usd": self.llm_cost_usd,
            "n_pending_flag_flips": self.n_pending_flag_flips,
            "n_pending_strategist_proposals": self.n_pending_strategist_proposals,
        }


# ── State collection ──────────────────────────────────────────────


def _fetch_one(conn, sql: str, params: tuple = ()) -> dict[str, Any] | None:
    """Small wrapper so every collector fails the same way. Returns
    a plain dict (not a Row) so callers can json.dumps() freely.

    Rule #19 discipline: log at WARNING with exc_info=True so column-
    typo errors (e.g. ``views_count`` when the column is ``views``)
    surface as stack traces in the journal instead of silent-failing.
    The Phase 5.D first live-fire ate 4 broken collectors this way —
    fail-open masked signal — before this elevation."""
    try:
        row = conn.execute(sql, params).fetchone()
    except Exception as exc:
        logger.warning("[briefing] query failed: %s", exc, exc_info=True)
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    if row is None:
        return None
    return dict(row)


def _fetch_all(conn, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(sql, params).fetchall()
    except Exception as exc:
        logger.warning("[briefing] query failed: %s", exc, exc_info=True)
        try:
            conn.rollback()
        except Exception:
            pass
        return []
    return [dict(r) for r in rows or []]


def _publishes_yesterday(conn) -> dict[str, Any]:
    rows = _fetch_all(
        conn,
        """
        SELECT niche_id, COUNT(*) AS n
        FROM publishing_analytics
        WHERE published_at >= NOW() - INTERVAL '24 hours'
          AND published_at < NOW()
        GROUP BY niche_id
        ORDER BY niche_id
        """,
    )
    return {"per_niche": rows, "total": sum(int(r.get("n") or 0) for r in rows)}


def _top_performer_yesterday(conn) -> dict[str, Any] | None:
    """Note: real publishing_analytics columns are `views` / `likes`
    (bigint) — NOT `views_count` / `likes_count`. Phase 5.D first
    live-fire had the wrong names → fail-open ate the exception →
    briefing rendered `top_performer: null` on a healthy system.
    Class-of-bug: signal-loss-through-merged-failure-paths."""
    return _fetch_one(
        conn,
        """
        SELECT niche_id, platform, post_id, views, likes
        FROM publishing_analytics
        WHERE published_at >= NOW() - INTERVAL '24 hours'
          AND published_at < NOW()
          AND views IS NOT NULL
        ORDER BY views DESC
        LIMIT 1
        """,
    )


def _pending_alerts(conn) -> dict[str, Any]:
    """Only unresolved alerts should reach the briefing — otherwise
    the LLM keeps suggesting repair actions for things already fixed
    (observed live 2026-08-15: repair_permissions.sh alert lingered
    in the briefing hours after the fix ran because the query lacked
    the resolved_at IS NULL filter)."""
    rows = _fetch_all(
        conn,
        """
        SELECT niche_id, severity, check_name, message
        FROM pipeline_alerts
        WHERE created_at >= NOW() - INTERVAL '24 hours'
          AND resolved_at IS NULL
        ORDER BY created_at DESC
        LIMIT 5
        """,
    )
    return {"recent": rows, "count": len(rows)}


def _pending_flag_flips(conn) -> dict[str, Any]:
    rows = _fetch_all(
        conn,
        """
        SELECT flag_name, from_state, to_state, confidence, rationale,
               EXTRACT(EPOCH FROM (NOW() - proposed_at))::float / 3600 AS age_h
        FROM flag_flip_proposals
        WHERE status = 'pending'
        ORDER BY proposed_at ASC
        LIMIT 5
        """,
    )
    return {"queue": rows, "count": len(rows)}


def _pending_strategist(conn) -> dict[str, Any]:
    """Real schema: strategist_reports stores per-run rows with
    proposals[] JSONB. "Pending" = reports not yet reviewed
    (reviewed_at IS NULL) in the last 14 days. Older un-reviewed
    reports are effectively abandoned — surfacing them only adds
    noise. There is no separate strategist_proposals table."""
    row = _fetch_one(
        conn,
        """
        SELECT COUNT(*) AS n_reports,
               SUM(jsonb_array_length(proposals))::int AS n_proposals
        FROM strategist_reports
        WHERE reviewed_at IS NULL
          AND run_at >= NOW() - INTERVAL '14 days'
        """,
    )
    if row is None:
        return {"count": 0, "n_reports": 0}
    return {
        "count": int(row.get("n_proposals") or 0),
        "n_reports": int(row.get("n_reports") or 0),
    }


def _calibration_progress(conn) -> list[dict[str, Any]]:
    """Per-niche AUTO-approver 7-day sample count + agreement rate.
    Real schema: `gate_approved` BOOL + `operator_action` TEXT
    ('approved'/'rejected'); `decided_at` for time. Agreement =
    both agree in same direction. Rule #22 pinning: operator_action
    values are lowercase — 2026-08-15 verification query returned
    {'approved', 'rejected'} only."""
    return _fetch_all(
        conn,
        """
        SELECT niche_id,
               COUNT(*) AS n_samples,
               SUM(CASE
                     WHEN (gate_approved = TRUE  AND operator_action = 'approved')
                       OR (gate_approved = FALSE AND operator_action = 'rejected')
                     THEN 1 ELSE 0
                   END)::float / NULLIF(COUNT(*), 0) AS agreement_rate
        FROM auto_approval_calibration
        WHERE decided_at >= NOW() - INTERVAL '7 days'
        GROUP BY niche_id
        ORDER BY niche_id
        """,
    )


def _cost_today(conn) -> dict[str, Any] | None:
    """Real cost table: pipeline_run_costs — per-run row with
    total_usd. There is no llm_call_log table (the memo I built
    the query against was based on a design that never shipped).
    Sum today's runs across niches."""
    return _fetch_one(
        conn,
        """
        SELECT SUM(total_usd)::float AS total_usd,
               COUNT(*) AS n_runs,
               SUM(entry_count)::int AS n_calls
        FROM pipeline_run_costs
        WHERE completed_at >= DATE_TRUNC('day', NOW())
        """,
    )


def collect_state(conn) -> dict[str, Any]:
    """Aggregate the ~7 signals the LLM needs. Every collector
    fail-opens to ``{"error": ...}`` — sibling signals still fill."""
    state: dict[str, Any] = {}
    for key, fn in (
        ("publishes_yesterday", _publishes_yesterday),
        ("top_performer_yesterday", _top_performer_yesterday),
        ("pending_alerts", _pending_alerts),
        ("pending_flag_flips", _pending_flag_flips),
        ("pending_strategist", _pending_strategist),
        ("calibration_progress", _calibration_progress),
        ("cost_today", _cost_today),
    ):
        try:
            state[key] = fn(conn)
        except Exception as exc:
            logger.warning("[briefing] collector %s failed: %s", key, exc)
            state[key] = {"error": str(exc)[:200]}
    return state


# ── LLM call + fallback render ────────────────────────────────────


def render_prompt(state: dict[str, Any]) -> str:
    """Compact serialization — Haiku sees JSON + a nudge on tone."""
    return (
        "Yesterday's Gen Lab state (JSON):\n\n"
        f"{json.dumps(state, indent=2, default=str)}\n\n"
        "Write the 5-line briefing now."
    )


def _fallback_render(state: dict[str, Any]) -> str:
    """When the LLM is unavailable (budget cap, network, empty
    response), deterministic Markdown from the raw aggregate. The
    operator still gets a functional email — not just an error."""
    lines = ["**Operator briefing (fallback render)**"]
    pubs = state.get("publishes_yesterday") or {}
    lines.append(
        f"- Publishes (24h): {pubs.get('total', 0)} total across "
        f"{len((pubs.get('per_niche') or []))} niches."
    )
    top = state.get("top_performer_yesterday") or {}
    if isinstance(top, dict) and top.get("post_id"):
        lines.append(
            f"- Top: {top.get('niche_id')}/{top.get('platform')} — "
            f"{top.get('views') or 0} views."
        )
    flips = state.get("pending_flag_flips") or {}
    strat = state.get("pending_strategist") or {}
    alerts = state.get("pending_alerts") or {}
    lines.append(
        f"- Pending review: {flips.get('count', 0)} flag flips, "
        f"{strat.get('count', 0)} strategist proposals, "
        f"{alerts.get('count', 0)} recent alerts."
    )
    cost = state.get("cost_today") or {}
    if isinstance(cost, dict) and cost.get("total_usd") is not None:
        lines.append(
            f"- LLM spend today: ${float(cost['total_usd']):.2f} "
            f"over {int(cost.get('n_calls') or 0)} calls."
        )
    lines.append("(LLM synthesis unavailable — see structured data on card.)")
    return "\n".join(lines)


def call_llm(prompt: str, *, client=None) -> tuple[str, float]:
    """Run Haiku with caller_type='optional' so Phase 2.D budget
    gate can throttle. Returns ``(text, cost_usd)``. Empty text on
    failure — caller falls back to ``_fallback_render``."""
    try:
        if client is None:
            from genlab_core.intelligence.anthropic_client import (
                AnthropicStrategistClient,
            )
            client = AnthropicStrategistClient()
        result = client.generate_report(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=prompt,
            caller_type="optional",
        )
        text = (result.text or "").strip()
        return text, float(getattr(result, "cost_usd", 0.0) or 0.0)
    except Exception as exc:
        logger.warning("[briefing] LLM call failed: %s", exc)
        return "", 0.0


def generate(conn, *, client=None) -> BriefingResult:
    """End-to-end synthesis. Every layer fail-opens so a partial
    state / offline LLM still yields a persistable row."""
    state = collect_state(conn)
    prompt = render_prompt(state)
    text, cost = call_llm(prompt, client=client)
    summary = text if text else _fallback_render(state)
    flips = state.get("pending_flag_flips") or {}
    strat = state.get("pending_strategist") or {}
    return BriefingResult(
        ok=True,
        summary_md=summary,
        structured=state,
        llm_cost_usd=cost,
        n_pending_flag_flips=int(flips.get("count") or 0),
        n_pending_strategist_proposals=int(strat.get("count") or 0),
    )
