"""Side-effect actions triggered by operator-accepted Strategist proposals.

`strategy_phase.PhaseConfig` covers the READ path — hot code (reward
shaper / auto-approval gate / writer) queries a cached PhaseConfig each
run and applies its overrides. This module covers the WRITE path — a
periodic runner materialises "structural" proposals like `arm_add`
that create new rows in dependent tables.

Kept separate from arm_loader.py so the reward-loop hot path (which
imports arm_loader) doesn't drag DB dependencies from the Strategist
schema into every pipeline run.

Design principles:

1. **Idempotent.** Applying the same proposal twice must be safe — the
   `strategist_reports.extra->>arm_add_applied_at` timestamp is the
   idempotency stamp. Re-runs skip proposals already stamped.

2. **Fail-closed per proposal.** One bad proposal shape doesn't block
   the rest. Each proposal wrapped in try/except; errors logged; move on.

3. **Feature-flag same as strategy_phase.** GENLAB_STRATEGIST_INTEGRATION_ENABLED
   gates the whole apply cycle. Without the flag, `apply_pending_actions`
   is a no-op that logs "disabled" and returns 0.

Typical wiring: `scripts/apply_strategist_actions.py` runs from a
systemd timer (daily 03:00 UTC after Sunday Strategist fire) OR is
invoked by the dashboard endpoint on operator accept.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


def _integration_enabled() -> bool:
    # 2026-07-14: env_true unifies with rest of codebase (1|true|yes|on).
    from genlab_core.settings import env_true

    return env_true("GENLAB_STRATEGIST_INTEGRATION_ENABLED")


def apply_pending_actions(niche_id: str | None = None) -> dict[str, int]:
    """Scan strategist_reports for accepted-but-unapplied proposals and
    materialise the side effects (create new bandit arms, etc.).

    Args:
        niche_id: If provided, only process reports for this niche.
            None → process all niches.

    Returns:
        Dict of {action_type: count_applied}. Never raises. Empty dict
        when feature flag is off.
    """
    if not _integration_enabled():
        logger.info("strategist_actions: integration disabled — skipping")
        return {}

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        logger.warning("strategist_actions: DATABASE_URL not set — cannot apply")
        return {}

    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        logger.error("strategist_actions: psycopg not installed (%s)", exc)
        return {}

    counters: dict[str, int] = {"arm_add": 0, "errors": 0, "reports_scanned": 0}

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        reports = _fetch_reports_to_apply(conn, niche_id)
        for report in reports:
            counters["reports_scanned"] += 1
            proposals = report.get("proposals") or []
            accepted_indices = report.get("proposals_accepted") or []
            if isinstance(accepted_indices, str):
                try:
                    accepted_indices = json.loads(accepted_indices)
                except (json.JSONDecodeError, ValueError):
                    accepted_indices = []
            already_applied = _already_applied_indices(report.get("extra") or {})

            for idx in accepted_indices:
                try:
                    idx = int(idx)
                except (TypeError, ValueError):
                    continue
                if idx in already_applied:
                    continue
                try:
                    proposal = proposals[idx]
                except (IndexError, TypeError):
                    continue
                if not isinstance(proposal, dict):
                    continue
                proposal_type = proposal.get("type")
                try:
                    if proposal_type == "arm_add":
                        if _apply_arm_add(conn, report["niche_id"], proposal):
                            _mark_applied(conn, report["id"], idx)
                            counters["arm_add"] += 1
                    # Other proposal types (phase_shift, gate_threshold,
                    # reward_weight, novelty_rate, playbook_update) are
                    # READ-path integrations handled by strategy_phase.
                    # They don't need materialisation here.
                except Exception as exc:
                    logger.warning(
                        "strategist_actions.proposal_failed report=%s idx=%d type=%s err=%s",
                        report["id"],
                        idx,
                        proposal_type,
                        exc,
                    )
                    counters["errors"] += 1

    logger.info("strategist_actions.apply.complete counters=%s", counters)
    return counters


def _fetch_reports_to_apply(conn, niche_id: str | None) -> list[dict[str, Any]]:
    """Reports whose proposals need materialising into bandit_arms.

    2026-08-11 Bug 3d: was requiring reviewed_at IS NOT NULL, but the
    proposal_auto_accept path (scripts/auto_accept_strategist_proposals.py)
    writes proposals_accepted WITHOUT touching reviewed_at (reviewed_at
    is operator-review-timestamp semantics). Auto-accepted reports were
    invisible to this fetcher — apply script scanned 0 reports every
    fire despite proposals_accepted being populated. Silent-dead again.

    Fix: accept EITHER operator-reviewed OR auto-accepted reports.
    ``extra ? 'auto_accepted_indices'`` (JSONB has-key) identifies the
    auto-accept path.
    """
    if niche_id:
        return conn.execute(
            """
            SELECT id, niche_id, proposals, proposals_accepted, extra
            FROM strategist_reports
            WHERE niche_id = %s
              AND proposals_accepted IS NOT NULL
              AND (reviewed_at IS NOT NULL
                   OR extra ? 'auto_accepted_indices')
            ORDER BY run_at DESC LIMIT 50
            """,
            (niche_id,),
        ).fetchall()
    return conn.execute(
        """
        SELECT id, niche_id, proposals, proposals_accepted, extra
        FROM strategist_reports
        WHERE proposals_accepted IS NOT NULL
          AND (reviewed_at IS NOT NULL
               OR extra ? 'auto_accepted_indices')
        ORDER BY run_at DESC LIMIT 200
        """,
    ).fetchall()


def _already_applied_indices(extra: dict[str, Any] | str) -> set[int]:
    """Extract the set of proposal indices already applied — stored in
    ``strategist_reports.extra->>'applied_indices'`` as a JSON array."""
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except (json.JSONDecodeError, ValueError):
            return set()
    if not isinstance(extra, dict):
        return set()
    raw = extra.get("applied_indices", [])
    if isinstance(raw, list):
        try:
            return {int(i) for i in raw}
        except (TypeError, ValueError):
            return set()
    return set()


def _apply_arm_add(conn, niche_id: str, proposal: dict[str, Any]) -> bool:
    """Materialise an arm_add proposal by inserting a bandit_arms row
    with the proposed priors.

    Proposal shape:
      {"type": "arm_add",
       "target": "ai_creators.arms",
       "proposed": {"arm_id": "style:behind_the_scenes",
                    "prior_alpha": 1, "prior_beta": 2}}

    Idempotent: if the arm already exists in bandit_arms (niche_id,
    arm_id), we skip and return False. The insert uses ON CONFLICT DO
    NOTHING so a race between two runners is safe.

    Returns True if a NEW row was created, False if the arm existed
    already or the proposal was malformed.
    """
    proposed = proposal.get("proposed") or {}
    # 2026-08-11 Bug 3e: strategist writes `proposed` as a JSON-encoded
    # STRING, not a nested dict. Same shape as proposal_auto_accept.py
    # discovered 2026-07-24 (see classify_arm_add lines 113-127). Prior
    # to this line, every apply attempt returned False silently because
    # isinstance(str, dict) is False -> arm_add=0, learning loop dead.
    # Defensive JSON parse when it looks like an object literal.
    if isinstance(proposed, str):
        s = proposed.strip()
        if s.startswith("{") and s.endswith("}"):
            try:
                proposed = json.loads(s)
            except (json.JSONDecodeError, ValueError):
                return False
        else:
            return False
    if not isinstance(proposed, dict):
        return False
    arm_id = proposed.get("arm_id", "")
    if not isinstance(arm_id, str) or not arm_id.strip():
        return False
    try:
        alpha = float(proposed.get("prior_alpha", 1.0))
        beta = float(proposed.get("prior_beta", 1.0))
    except (TypeError, ValueError):
        alpha, beta = 1.0, 1.0
    # Sanity-clamp Beta parameters so a bad proposal can't seed a
    # near-degenerate posterior. alpha=beta=1 is the standard uniform
    # prior; we allow anything from 0.5 to 100.
    alpha = max(0.5, min(alpha, 100.0))
    beta = max(0.5, min(beta, 100.0))

    result = conn.execute(
        """
        INSERT INTO bandit_arms (niche_id, arm_id, alpha, beta, n_plays)
        VALUES (%s, %s, %s, %s, 0)
        ON CONFLICT (niche_id, arm_id) DO NOTHING
        RETURNING id
        """,
        (niche_id, arm_id, alpha, beta),
    )
    inserted = result.fetchone() is not None
    if inserted:
        conn.commit()
        logger.info(
            "strategist_actions.arm_added niche=%s arm=%s alpha=%.2f beta=%.2f",
            niche_id,
            arm_id,
            alpha,
            beta,
        )
    return inserted


def _mark_applied(conn, report_id: str, index: int) -> None:
    """Append the index to strategist_reports.extra.applied_indices."""
    conn.execute(
        """
        UPDATE strategist_reports
        SET extra = COALESCE(extra, '{}'::jsonb) || jsonb_build_object(
            'applied_indices',
            COALESCE(
                (extra->'applied_indices')::jsonb,
                '[]'::jsonb
            ) || to_jsonb(%s::int),
            'last_apply_at',
            %s::text
        )
        WHERE id = %s::uuid
        """,
        (index, datetime.now(UTC).isoformat(), report_id),
    )
    conn.commit()
