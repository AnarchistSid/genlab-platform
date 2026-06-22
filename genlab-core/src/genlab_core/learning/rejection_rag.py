"""Episodic RAG: surface recent operator rejection patterns to LLM prompts.

The 2026-06-22 autonomy audit identified Loop 8 (feedback_category)
as captured-but-not-acted-on: every operator Reject/Revise tags one
of 6 categorical reasons (weak_hook, too_generic, unsupported_claim,
bad_fit, too_long, low_value) AND PR #461 now writes each operator
review as an episodic_events row with that category. But nothing
downstream READS the categorical signal to ADAPT.

This module closes that loop for the hook-generation surface:
``get_recent_rejection_context(niche_id)`` returns a formatted
summary string the hook system prompt can paste in verbatim,
warning the LLM about the failure patterns the operator has been
flagging this week.

Why episodic_events not the calibration table:
- episodic_events stores the (niche, action, reason, blueprint_id)
  tuple compactly via record_event() — single table query
- The calibration table requires a join through blueprints to get
  the hook_text + niche_id together
- The RAG context is fresh-bias by design (only last 7d matter for
  drift detection); episodic_events has a niche+ts index that
  matches this query shape

Cost: 1 SQL query per hook generation = ~5ms p99. Cached at module
level with a 5-min TTL so a single pipeline run (10 hooks across a
niche) pays the query cost once, not 10 times.

The RAG context is INJECTED, not retrieved every call. The LLM
sees ALL recent rejection categories for the niche — not just
similarity-matched ones to the current story. This is intentional:
operator-flagged failure patterns are general (e.g. "hooks were
too generic this week") and apply across stories. Retrieval-by-
embedding would over-narrow the signal.
"""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

# Module-level cache: {niche_id: (timestamp, formatted_context)}.
# 5-min TTL — one pipeline run for a niche generates ~3-10 hooks; the
# cached context applies to all of them. Operator reviews flow on a
# much slower clock (~5/day across niches) so stale-by-5-min is fine.
_RAG_CACHE: dict[str, tuple[float, str]] = {}
_RAG_TTL_SECONDS = 300


def _reset_cache_for_tests() -> None:
    """Test helper: clear the module-level cache between unit tests."""
    _RAG_CACHE.clear()


# Map raw feedback_category enum values (operator-form picks) to
# human-readable advice strings the LLM can act on directly. The
# 6 canonical categories are pinned in
# tests/test_episodic_emit_early_stop_and_operator_review.py and
# UI ISSUE_OPTIONS in dashboard/frontend/src/components/review/.
_CATEGORY_ADVICE: dict[str, str] = {
    "weak_hook": "Hook lacked specificity or punch — be more concrete",
    "too_generic": "Hook was too generic — reference specific story details",
    "unsupported_claim": "Hook made claims not in the source — stick to facts",
    "bad_fit": "Hook didn't fit the niche audience — match the niche voice",
    "too_long": "Hook was too long — keep under 50 chars",
    "low_value": "Hook didn't grab attention — sharpen the curiosity gap",
}


def get_recent_rejection_context(
    niche_id: str,
    *,
    window_days: int = 7,
    max_categories: int = 6,
) -> str:
    """Return a formatted system-prompt block summarizing recent
    operator rejections for this niche, or empty string if no data.

    Args:
        niche_id: The niche to query (e.g. "gaming", "ai_creators").
        window_days: How far back to look. Default 7 days — operator
            review patterns shift weekly with content trends.
        max_categories: Cap on distinct categories to mention. Default
            6 (all canonical categories).

    Returns:
        A multi-line string ready to paste into a system prompt, OR
        empty string if (a) no episodic_events table, (b) DATABASE_URL
        unset, (c) no rejections in window, (d) cache miss + DB error.

    Cache: 5-min TTL keyed on niche_id. Tests should call
    ``_reset_cache_for_tests()`` between assertions.

    Fail-OPEN: every error path returns "" silently. The hook
    generator MUST work without this signal — RAG is augmentation,
    not a hard dependency.
    """
    now = time.time()
    cached = _RAG_CACHE.get(niche_id)
    if cached and (now - cached[0]) < _RAG_TTL_SECONDS:
        return cached[1]

    # Empty string is the explicit "no signal" value — the prompt
    # injector skips the block when this returns empty.
    if not os.environ.get("DATABASE_URL"):
        _RAG_CACHE[niche_id] = (now, "")
        return ""

    try:
        from genlab_core.storage.tenant_context import pg_connect

        dsn = os.environ["DATABASE_URL"]
        # pg_connect sets `app.niche_id` GUC automatically + satisfies
        # SR-A/C/D (raw psycopg.connect fails the architectural lint).
        # episodic_events lives behind RLS keyed on app.niche_id, so
        # passing niche_id here is what makes the SELECT visible.
        with pg_connect(dsn, connect_timeout=5, niche_id=niche_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                      payload->>'feedback_issue' AS category,
                      COUNT(*) AS n
                    FROM episodic_events
                    WHERE event_type = 'operator_review'
                      AND niche_id = %s
                      AND payload->>'action' IN ('rejected', 'revised')
                      AND payload->>'feedback_issue' IS NOT NULL
                      AND payload->>'feedback_issue' != ''
                      AND created_at > now() - (%s::int || ' days')::interval
                    GROUP BY 1
                    ORDER BY n DESC
                    LIMIT %s
                    """,
                    (niche_id, window_days, max_categories),  # noqa: E501
                )
                rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001 — RAG is augmentation only
        logger.debug("[rejection_rag] query failed (%s); returning empty", exc)
        _RAG_CACHE[niche_id] = (now, "")
        return ""

    if not rows:
        _RAG_CACHE[niche_id] = (now, "")
        return ""

    # Build the formatted block. The header is intentionally direct
    # ("AVOID:") so the LLM treats this as a constraint, not a
    # suggestion. Each line carries the (count, advice) so the
    # LLM understands BOTH the relative frequency AND what to fix.
    lines = [
        "RECENT OPERATOR FEEDBACK (last 7 days, top rejection reasons "
        "for this niche — AVOID repeating these failure patterns):",
    ]
    for category, count in rows:
        advice = _CATEGORY_ADVICE.get(
            category, f"Operators flagged this {count}x — avoid this pattern"
        )
        lines.append(f"  - {category} ({count}x): {advice}")

    context = "\n".join(lines)
    _RAG_CACHE[niche_id] = (now, context)
    return context
