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


def _proposal_confidence(proposal: dict) -> str:
    # Proposals themselves may carry confidence; fall through to
    # the linked hypothesis if not.
    return str(proposal.get("confidence", "")).strip()


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
        classify_arm_add,
        is_enabled,
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

        classified = {"auto_accept": [], "operator_gate": [], "skip": []}
        by_report: dict[str, list[int]] = {}

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

                decision = classify_arm_add(
                    proposal,
                    existing_arm_ids=existing_arms,
                    proposal_confidence=_proposal_confidence(proposal),
                )
                if decision.should_auto_accept:
                    classified["auto_accept"].append(
                        (niche_id, idx, decision.reason)
                    )
                    by_report.setdefault(report_id, []).append(idx)
                elif decision.reason.startswith("operator_gate:"):
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
        conn.commit()

        logger.info("DONE auto_accepted=%d skipped=%d", applied, len(classified["operator_gate"]))
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
