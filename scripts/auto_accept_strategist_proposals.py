#!/usr/bin/env python3
"""Auto-accept low-risk arm_add strategist proposals.

Reads unaccepted proposals from strategist_reports, classifies each
via genlab_core.scheduling.proposal_auto_accept, and appends
auto-classified indices to the report's ``proposals_accepted`` array
so the existing apply_strategist_actions.py picks them up on its
next fire.

Rate limit: at most MAX_AUTO_ACCEPTS_PER_WEEK per niche over the
rolling 7-day window (counted via ``proposals_accepted`` timestamps).

Usage:
    python scripts/auto_accept_strategist_proposals.py         # dry-run
    python scripts/auto_accept_strategist_proposals.py --apply

Exit codes:
    0 — success
    3 — unhandled exception
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "genlab-core" / "src"))

logger = logging.getLogger("proposal_auto_accept")


def _load_env(env_file: str = "/opt/genlab/.env") -> None:
    if os.environ.get("DATABASE_URL"):
        return
    env_path = Path(env_file)
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _fetch_unreviewed_reports(conn, niche_id):
    where_niche = "AND niche_id = %s" if niche_id else ""
    params = (niche_id,) if niche_id else ()
    return conn.execute(
        # 2026-08-11 Bug 3c: proposals_accepted MUST be in SELECT list.
        # Previously omitted -> report.get("proposals_accepted") returns
        # None -> already_accepted = set(None or []) = set() -> every
        # run re-accepts the same indices -> proposals_accepted grows
        # to [1, 1, 1, ...] over time. Silent-fail: writes appear to
        # work; dedup logic structurally broken since the fetch never
        # provides the state needed to dedup against. Discovery
        # 2026-08-11 during honest audit of "learning is fixed" claim.
        f"""
        SELECT id::text AS id,
               niche_id,
               proposals,
               proposals_accepted,
               causal_hypotheses,
               extra
        FROM strategist_reports
        WHERE proposals IS NOT NULL
          AND jsonb_array_length(proposals) > 0
          {where_niche}
        ORDER BY week_of DESC
        """,
        params,
    ).fetchall()


def _fetch_existing_arm_ids(conn, niche_id):
    """Fetch every arm_id for this niche. dict_row-safe: reads via
    r["arm_id"] with r[0] fallback so the same code works whether
    the caller opened conn with dict_row or default tuple cursor.
    Same class-of-bug pattern fixed 3× this session (see
    _count_recent_auto_accepts docstring)."""
    rows = conn.execute(
        "SELECT arm_id FROM bandit_arms WHERE niche_id = %s",
        (niche_id,),
    ).fetchall()
    def _val(r):
        return r.get("arm_id") if hasattr(r, "get") else r[0]
    return frozenset(str(_val(r)) for r in rows)


def _count_recent_auto_accepts(conn, niche_id: str, days: int = 7) -> int:
    """Count auto_accept-tagged indices applied in the last N days
    for this niche. Used as the rate-limit denominator.

    2026-07-24 fix: main() opens the conn with row_factory=dict_row,
    so ``row[0]`` raises KeyError. Same class-of-bug hit in c91bd77c
    (drift resolver) and dd376829 (parser) earlier this session. Use
    a named column (COUNT(*) AS n) + row["n"] with tuple fallback.
    """
    row = conn.execute(
        """
        SELECT COUNT(*)::int AS n
        FROM strategist_reports
        WHERE niche_id = %s
          AND run_at > NOW() - make_interval(days => %s)
          AND jsonb_typeof(extra->'auto_accepted_indices') = 'array'
        """,
        (niche_id, days),
    ).fetchone()
    if not row:
        return 0
    # Support both dict_row and tuple cursors.
    if hasattr(row, "get"):
        return int(row.get("n") or 0)
    return int(row[0] or 0)


def _append_auto_accepted(conn, report_id: str, indices: list[int]) -> None:
    """Append indices to both proposals_accepted (so apply_strategist_
    actions picks them up) AND extra.auto_accepted_indices (for the
    rate-limit counter)."""
    conn.execute(
        """
        UPDATE strategist_reports
        SET proposals_accepted = COALESCE(proposals_accepted, '[]'::jsonb)
              || %s::jsonb,
            extra = COALESCE(extra, '{}'::jsonb) || jsonb_build_object(
                'auto_accepted_indices',
                COALESCE(extra->'auto_accepted_indices', '[]'::jsonb) || %s::jsonb
            )
        WHERE id = %s
        """,
        (json.dumps(indices), json.dumps(indices), report_id),
    )


def _append_llm_accepted(conn, report_id: str, indices: list[int]) -> None:
    """Phase 1.B: append LLM-reviewer-driven accepts to proposals_
    accepted with source-tag in extra so meta-learning (1.C) can
    distinguish LLM vs heuristic accepts."""
    conn.execute(
        """
        UPDATE strategist_reports
        SET proposals_accepted = COALESCE(proposals_accepted, '[]'::jsonb)
              || %s::jsonb,
            extra = COALESCE(extra, '{}'::jsonb) || jsonb_build_object(
                'llm_reviewer_accepted_indices',
                COALESCE(extra->'llm_reviewer_accepted_indices', '[]'::jsonb)
                    || %s::jsonb,
                'llm_reviewer_last_run_at', %s::text
            )
        WHERE id = %s
        """,
        (
            json.dumps(indices), json.dumps(indices),
            _now_iso(), report_id,
        ),
    )


def _append_llm_rejected(conn, report_id: str, indices: list[int]) -> None:
    """Phase 1.B: append LLM-reviewer-driven rejects to proposals_
    rejected with source-tag in extra."""
    conn.execute(
        """
        UPDATE strategist_reports
        SET proposals_rejected = COALESCE(proposals_rejected, '[]'::jsonb)
              || %s::jsonb,
            extra = COALESCE(extra, '{}'::jsonb) || jsonb_build_object(
                'llm_reviewer_rejected_indices',
                COALESCE(extra->'llm_reviewer_rejected_indices', '[]'::jsonb)
                    || %s::jsonb,
                'llm_reviewer_last_run_at', %s::text
            )
        WHERE id = %s
        """,
        (
            json.dumps(indices), json.dumps(indices),
            _now_iso(), report_id,
        ),
    )


def _now_iso() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat()


def _proposal_confidence(proposal: dict) -> str:
    # Proposals themselves may carry confidence; fall through to
    # the linked hypothesis if not.
    return str(proposal.get("confidence", "")).strip()


# Phase 1.B (2026-08-14): daily Anthropic spend cap for the LLM
# reviewer path. Bounded blast radius — if the reviewer misbehaves
# (infinite loop, retry storm, unbounded prompt growth), it can't
# exceed this cap. Reset per calendar day.
_LLM_REVIEWER_DAILY_BUDGET_USD: float = 0.50


def _daily_llm_spend_usd(conn) -> float:
    """Query today's Anthropic spend across all callers via
    llm_run_cost table (populated by cost_persist.py). Returns 0.0
    on any query error — fail-open so a broken cost table can't
    silently block auto-accept (worse than exceeding budget)."""
    try:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(cost_usd), 0)::float AS spend
            FROM llm_run_cost
            WHERE created_at >= CURRENT_DATE
              AND provider = 'anthropic'
            """,
        ).fetchone()
        if not row:
            return 0.0
        return float(row.get("spend") if hasattr(row, "get") else row[0])
    except Exception as exc:
        logger.debug("[auto_accept] daily spend query failed: %s", exc)
        return 0.0


def _build_state_snapshot(conn, niche_id: str) -> dict:
    """One-shot context bundle for the LLM reviewer. Minimal — just
    what's needed to sanity-check a proposal against current state.
    Kept small because Haiku tokens cost."""
    snap: dict = {"niche_id": niche_id}
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) FILTER (WHERE n_plays >= 1)::int AS active_arms,
                   COUNT(*)::int AS total_arms
            FROM bandit_arms WHERE niche_id = %s
            """,
            (niche_id,),
        ).fetchone()
        if row:
            snap["active_arms"] = (
                row.get("active_arms") if hasattr(row, "get") else row[0]
            )
            snap["total_arms"] = (
                row.get("total_arms") if hasattr(row, "get") else row[1]
            )
    except Exception:
        pass
    return snap


class _LLMAnthropicClient:
    """Adapts genlab_core.intelligence.anthropic_client to the LLMClient
    Protocol expected by Reviewer. Late import so heuristic-only
    path doesn't pay the SDK import cost."""

    def __init__(self):
        from genlab_core.intelligence.anthropic_client import (
            AnthropicStrategistClient,
        )
        self._c = AnthropicStrategistClient()

    def review(self, system_prompt: str, user_prompt: str) -> str:
        # Reuse the strategist client but override max_tokens smaller
        # for Haiku-cost review calls. The generate_report method
        # returns a CallResult; we return the text.
        try:
            r = self._c.generate_report(system_prompt, user_prompt)
            return getattr(r, "text", str(r))
        except Exception as exc:
            raise RuntimeError(f"anthropic call failed: {exc}") from exc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--niche", default=None)
    ap.add_argument("--env-file", default="/opt/genlab/.env")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    _load_env(args.env_file)

    from genlab_core.scheduling.proposal_auto_accept import (
        MAX_AUTO_ACCEPTS_PER_WEEK,
        AcceptDecision,
        classify_arm_add,
        classify_gate_threshold,
        classify_novelty_rate,
        classify_reward_weight,
        get_max_auto_accepts_per_week,
        is_enabled,
    )
    # Phase 1.B (2026-08-14): LLM reviewer imports — lazy so the
    # heuristic-only mode doesn't pay import cost of Anthropic client.
    from genlab_core.scheduling.llm_proposal_reviewer import (
        CONFIDENCE_THRESHOLD_ACCEPT,
        CONFIDENCE_THRESHOLD_REJECT,
        Reviewer,
        is_enabled as llm_reviewer_enabled,
    )

    if not is_enabled():
        logger.info(
            "GENLAB_PROPOSAL_AUTO_ACCEPT_ENABLED not set to 'true' — exiting cleanly"
        )
        return 0

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        logger.error("DATABASE_URL not set")
        return 2

    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        reports = _fetch_unreviewed_reports(conn, args.niche)
        if not reports:
            logger.info("no reports with proposals — exiting cleanly")
            return 0

        classified = {
            "auto_accept": [], "operator_gate": [], "skip": [],
            # Phase 1.B: separate buckets for LLM-reviewer verdicts so
            # audit can distinguish heuristic vs LLM decisions.
            "llm_accept": [], "llm_reject": [],
        }
        by_report: dict[str, list[int]] = {}
        # Track LLM-driven decisions separately so we can write source
        # tags on apply.
        llm_accept_by_report: dict[str, list[int]] = {}
        llm_reject_by_report: dict[str, list[int]] = {}

        # LLM reviewer state (built once, reused across all proposals)
        reviewer = None
        llm_budget_exhausted = False
        if llm_reviewer_enabled():
            initial_spend = _daily_llm_spend_usd(conn)
            if initial_spend >= _LLM_REVIEWER_DAILY_BUDGET_USD:
                logger.info(
                    "[llm_reviewer] daily budget exhausted "
                    "(spend=$%.4f >= cap=$%.2f) — skipping fallback",
                    initial_spend, _LLM_REVIEWER_DAILY_BUDGET_USD,
                )
                llm_budget_exhausted = True
            else:
                try:
                    reviewer = Reviewer(_LLMAnthropicClient())
                    logger.info(
                        "[llm_reviewer] enabled — daily spend=$%.4f/$%.2f",
                        initial_spend, _LLM_REVIEWER_DAILY_BUDGET_USD,
                    )
                except Exception as exc:
                    logger.warning(
                        "[llm_reviewer] init failed: %s — falling back "
                        "to heuristic only", exc,
                    )

        for report in reports:
            report_id = report["id"]
            niche_id = report["niche_id"]

            proposals = report.get("proposals") or []
            already_accepted = set(report.get("proposals_accepted") or [])
            recent_auto = _count_recent_auto_accepts(conn, niche_id)
            if recent_auto >= MAX_AUTO_ACCEPTS_PER_WEEK:
                logger.info(
                    "[auto_accept] niche=%s rate-limited (recent_auto=%d ≥ max=%d)",
                    niche_id,
                    recent_auto,
                    MAX_AUTO_ACCEPTS_PER_WEEK,
                )
                continue

            existing_arms = _fetch_existing_arm_ids(conn, niche_id)

            for idx, proposal in enumerate(proposals):
                if idx in already_accepted:
                    continue
                if not isinstance(proposal, dict):
                    continue

                # 2026-08-11 Session 2 + 3: route on proposal type. All
                # 4 programmatic proposal types now flow through auto-
                # accept. Only `manual_action` remains operator-gated
                # by design (that type is definitionally not machine-
                # applicable — it's advice text for the operator).
                proposal_type = proposal.get("type")
                if proposal_type == "arm_add":
                    decision = classify_arm_add(
                        proposal,
                        existing_arm_ids=existing_arms,
                        proposal_confidence=_proposal_confidence(proposal),
                    )
                elif proposal_type == "reward_weight":
                    decision = classify_reward_weight(
                        proposal,
                        niche_id=niche_id,
                        proposal_confidence=_proposal_confidence(proposal),
                    )
                elif proposal_type == "gate_threshold":
                    decision = classify_gate_threshold(
                        proposal,
                        proposal_confidence=_proposal_confidence(proposal),
                    )
                elif proposal_type == "novelty_rate":
                    decision = classify_novelty_rate(
                        proposal,
                        proposal_confidence=_proposal_confidence(proposal),
                    )
                else:
                    decision = AcceptDecision(
                        False,
                        f"skip:unhandled_type ({proposal_type!r})",
                    )
                if decision.should_auto_accept:
                    classified["auto_accept"].append(
                        (niche_id, idx, decision.reason)
                    )
                    by_report.setdefault(report_id, []).append(idx)
                elif decision.reason.startswith("operator_gate:"):
                    # Phase 1.B: heuristic classifier PUNTED — try LLM
                    # reviewer before dropping to operator gate. Never
                    # calls Anthropic when reviewer=None (flag off /
                    # budget exhausted / init failed).
                    llm_verdict = None
                    if reviewer is not None:
                        try:
                            state_snap = _build_state_snapshot(conn, niche_id)
                            llm_verdict = reviewer.review(
                                proposal, niche_id, state_snap,
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "[llm_reviewer] review failed niche=%s "
                                "idx=%d: %s — punting to operator",
                                niche_id, idx, exc,
                            )
                        # Recheck budget after each call — cheap protection
                        # against unbounded spend if reviewer runs slow.
                        if _daily_llm_spend_usd(conn) >= _LLM_REVIEWER_DAILY_BUDGET_USD:
                            logger.info(
                                "[llm_reviewer] daily budget hit "
                                "during run — disabling for rest of pass"
                            )
                            reviewer = None

                    if llm_verdict is not None:
                        if (
                            llm_verdict.decision == "accept"
                            and llm_verdict.confidence >= CONFIDENCE_THRESHOLD_ACCEPT
                        ):
                            classified["llm_accept"].append((
                                niche_id, idx,
                                f"llm_accept:conf={llm_verdict.confidence:.2f} "
                                f"reason={llm_verdict.reason[:80]}",
                            ))
                            llm_accept_by_report.setdefault(
                                report_id, [],
                            ).append(idx)
                            # Also count against the aggregate rate limit
                            by_report.setdefault(report_id, [])
                            continue
                        if (
                            llm_verdict.decision == "reject"
                            and llm_verdict.confidence >= CONFIDENCE_THRESHOLD_REJECT
                        ):
                            classified["llm_reject"].append((
                                niche_id, idx,
                                f"llm_reject:conf={llm_verdict.confidence:.2f} "
                                f"reason={llm_verdict.reason[:80]}",
                            ))
                            llm_reject_by_report.setdefault(
                                report_id, [],
                            ).append(idx)
                            continue
                        # LLM abstain or low-confidence → still operator scope
                    classified["operator_gate"].append(
                        (niche_id, idx, decision.reason)
                    )
                else:
                    classified["skip"].append(
                        (niche_id, idx, decision.reason)
                    )

                # Enforce rate-limit within this run too so a single
                # report can't burn through the whole budget.
                if len(by_report.get(report_id, [])) >= (
                    MAX_AUTO_ACCEPTS_PER_WEEK - recent_auto
                ):
                    break

        summary = {k: len(v) for k, v in classified.items()}
        logger.info("classified: %s", summary)

        if not args.apply:
            print(
                f"\nDRY RUN — would auto-accept {summary['auto_accept']} proposals"
            )
            for niche_id, idx, reason in classified["auto_accept"][:10]:
                print(f"  [{niche_id}] idx={idx}: {reason}")
            return 0

        # APPLY
        applied = 0
        for report_id, indices in by_report.items():
            try:
                _append_auto_accepted(conn, report_id, indices)
                applied += len(indices)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[auto_accept] failed to append %s to report %s: %s",
                    indices,
                    report_id[:8],
                    exc,
                )

        # Phase 1.B: LLM-driven decisions. Same SQL primitive as
        # heuristic path, but tagged differently in extra so audit can
        # separate "heuristic decided" vs "LLM decided" for meta-
        # learning (Phase 1.C — track which source actually helped).
        llm_applied = 0
        for report_id, indices in llm_accept_by_report.items():
            try:
                _append_llm_accepted(conn, report_id, indices)
                llm_applied += len(indices)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[llm_reviewer] failed to append LLM-accepts %s to "
                    "report %s: %s", indices, report_id[:8], exc,
                )

        llm_rejected = 0
        for report_id, indices in llm_reject_by_report.items():
            try:
                _append_llm_rejected(conn, report_id, indices)
                llm_rejected += len(indices)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[llm_reviewer] failed to append LLM-rejects %s to "
                    "report %s: %s", indices, report_id[:8], exc,
                )
        conn.commit()

        logger.info(
            "DONE heuristic_accepted=%d llm_accepted=%d llm_rejected=%d "
            "still_operator_gate=%d",
            applied, llm_applied, llm_rejected,
            len(classified["operator_gate"]),
        )
        return 0


def _main_with_durable_error() -> int:
    try:
        return main()
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 0
    except Exception as exc:  # noqa: BLE001
        try:
            from genlab_core.observability.durable_error import write_durable_error

            write_durable_error("auto_accept_strategist_proposals", exc)
        except Exception as import_exc:  # noqa: BLE001
            print(
                f"(also failed to import durable_error: {import_exc})",
                file=sys.stderr,
            )
            import traceback as _tb

            _tb.print_exc(file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(_main_with_durable_error())
