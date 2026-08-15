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
    a plain dict (not a Row) so callers can json.dumps() freely."""
    try:
        row = conn.execute(sql, params).fetchone()
    except Exception as exc:
        logger.warning("[briefing] query failed: %s", exc)
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
        logger.warning("[briefing] query failed: %s", exc)
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
    return _fetch_one(
        conn,
        """
        SELECT niche_id, platform, post_id, views_count, likes_count
        FROM publishing_analytics
        WHERE published_at >= NOW() - INTERVAL '24 hours'
          AND published_at < NOW()
          AND views_count IS NOT NULL
        ORDER BY views_count DESC
        LIMIT 1
        """,
    )


def _pending_alerts(conn) -> dict[str, Any]:
    rows = _fetch_all(
        conn,
        """
        SELECT niche_id, severity, check_name, message
        FROM pipeline_alerts
        WHERE created_at >= NOW() - INTERVAL '24 hours'
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
    rows = _fetch_all(
        conn,
        """
        SELECT proposal_type, COUNT(*) AS n
        FROM strategist_proposals
        WHERE status = 'pending'
        GROUP BY proposal_type
        ORDER BY n DESC
        """,
    )
    return {"by_type": rows, "count": sum(int(r.get("n") or 0) for r in rows)}


def _calibration_progress(conn) -> list[dict[str, Any]]:
    """Per-niche AUTO-approver sample count. Column names match
    auto_approval_calibration table shipped in AUTO #1c."""
    return _fetch_all(
        conn,
        """
        SELECT niche_id,
               COUNT(*) AS n_samples,
               SUM(CASE WHEN gate_verdict = operator_action THEN 1 ELSE 0 END)::float
                 / NULLIF(COUNT(*), 0) AS agreement_rate
        FROM auto_approval_calibration
        WHERE logged_at >= NOW() - INTERVAL '7 days'
        GROUP BY niche_id
        ORDER BY niche_id
        """,
    )


def _cost_today(conn) -> dict[str, Any] | None:
    return _fetch_one(
        conn,
        """
        SELECT SUM(cost_usd)::float AS total_usd,
               COUNT(*) AS n_calls
        FROM llm_call_log
        WHERE ts >= DATE_TRUNC('day', NOW())
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
            f"{top.get('views_count') or 0} views."
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
