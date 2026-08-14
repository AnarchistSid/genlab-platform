#!/usr/bin/env python3
"""Phase 5.C session 1 — autonomous flag manager (proposer only).

Fires daily at 07:00 UTC via ``genlab-flag-manager.timer``. Runs
every registered flag-flip proposer, persists non-None proposals
to ``flag_flip_proposals`` in status='pending'.

Session 1 (this): PROPOSES only. Session 2 adds auto-apply after
24h operator-override window.

## Idempotency

Before persisting a new proposal for a flag, marks any prior
pending proposal for the same flag as ``superseded``. Only the
freshest proposal per flag stays pending — the operator sees a
clean queue, not a growing pile of stale suggestions.

## Usage

    uv run python scripts/autonomous_flag_manager.py
    uv run python scripts/autonomous_flag_manager.py --dry-run

## Exit codes

  * 0 — completed
  * 1 — DATABASE_URL unset
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

logger = logging.getLogger("autonomous_flag_manager")


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true",
                    help="Phase 5.C session 2: also auto-apply pending "
                         "proposals >= 24h old + confidence > 0.9 "
                         "with no operator rejection")
    ap.add_argument(
        "--override-window-hours", type=int, default=24,
        help="Hours a pending proposal must age before auto-apply",
    )
    ap.add_argument(
        "--apply-confidence-threshold", type=float, default=0.9,
        help="Minimum confidence for auto-apply (roadmap: 0.9)",
    )
    ap.add_argument(
        "--env-path", default=os.environ.get("GENLAB_ENV_PATH", "/opt/genlab/.env"),
        help="Path to .env file to rewrite (default: /opt/genlab/.env)",
    )
    return ap.parse_args(argv)


def _supersede_prior_pending(conn, flag_name: str) -> int:
    """Flip any existing pending row for this flag to 'superseded'.
    Returns count superseded. Fail-open to 0."""
    try:
        result = conn.execute(
            """
            UPDATE flag_flip_proposals
            SET status = 'superseded'
            WHERE flag_name = %s AND status = 'pending'
            """,
            (flag_name,),
        )
        return result.rowcount or 0
    except Exception as exc:
        logger.warning(
            "[flag_mgr] supersede failed flag=%s: %s", flag_name, exc,
        )
        try:
            conn.rollback()
        except Exception:
            pass
        return 0


def _persist_proposal(conn, proposal) -> bool:
    try:
        conn.execute(
            """
            INSERT INTO flag_flip_proposals
              (flag_name, from_state, to_state, rationale, evidence,
               confidence)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                proposal.flag_name, proposal.from_state,
                proposal.to_state, proposal.rationale,
                json.dumps(proposal.evidence), proposal.confidence,
            ),
        )
        return True
    except Exception as exc:
        logger.warning(
            "[flag_mgr] persist failed flag=%s: %s",
            proposal.flag_name, exc,
        )
        try:
            conn.rollback()
        except Exception:
            pass
        return False


# ── Session 2: auto-apply helpers ────────────────────────────────


def _find_apply_ready_proposals(
    conn, window_hours: int, min_confidence: float,
) -> list[dict]:
    """Return pending proposals that have aged past the operator-
    override window AND clear the confidence threshold. Fail-open
    to [] on any query error."""
    try:
        rows = conn.execute(
            """
            SELECT id::text AS id, flag_name, from_state, to_state,
                   rationale, confidence
            FROM flag_flip_proposals
            WHERE status = 'pending'
              AND confidence >= %s
              AND proposed_at <= NOW() - (%s || ' hours')::INTERVAL
            ORDER BY proposed_at ASC
            """,
            (min_confidence, window_hours),
        ).fetchall()
    except Exception as exc:
        logger.warning("[flag_mgr] apply-query failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return []
    return [dict(r) for r in rows or []]


def _env_line_for(flag_name: str, value: str) -> str:
    """Format env line — value quoted only if it contains spaces."""
    if any(c.isspace() for c in value):
        return f'{flag_name}="{value}"\n'
    return f"{flag_name}={value}\n"


def _write_env_flag(env_path: str, flag_name: str, value: str) -> tuple[bool, str]:
    """Rewrite (or append) a single env-flag line in .env. Preserves
    every other line + comments verbatim. Backup written first.

    Returns (ok, diagnostic)."""
    import shutil
    from datetime import UTC, datetime
    from pathlib import Path

    path = Path(env_path)
    if not path.exists():
        return False, f"{env_path} not found"

    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    # NOTE: with_suffix() appends on dotfiles (`.env` has no suffix).
    # Use with_name to get `<path>/.env.bak.<ts>` deterministically.
    backup = path.with_name(f"{path.name}.bak.{ts}")
    try:
        shutil.copy2(path, backup)
    except Exception as exc:
        return False, f"backup failed: {exc}"

    try:
        lines = path.read_text().splitlines(keepends=True)
        # Find existing line for this flag (last occurrence wins
        # since .env is read top-down and later assignments win)
        target_line = _env_line_for(flag_name, value)
        found = False
        out_lines: list[str] = []
        for line in lines:
            stripped = line.lstrip()
            if stripped.startswith(f"{flag_name}=") or stripped.startswith(f"{flag_name} ="):
                # Replace with fresh value; only replace ONCE — leave
                # subsequent lines alone in case operator has annotations
                if not found:
                    out_lines.append(target_line)
                    found = True
                # skip further occurrences to avoid duplication
                # (there shouldn't be any but safer to dedup)
            else:
                out_lines.append(line)
        if not found:
            # Append at end with a newline guard
            if out_lines and not out_lines[-1].endswith("\n"):
                out_lines[-1] += "\n"
            out_lines.append(target_line)
        path.write_text("".join(out_lines))
        return True, f"flag={flag_name} value={value}"
    except Exception as exc:
        # Restore backup
        try:
            shutil.copy2(backup, path)
        except Exception:
            pass
        return False, f"write failed: {exc}"


def _mark_applied(conn, proposal_id: str, applied_by: str) -> bool:
    try:
        conn.execute(
            """
            UPDATE flag_flip_proposals
            SET status = 'applied',
                applied_at = NOW(),
                applied_by = %s
            WHERE id = %s AND status = 'pending'
            """,
            (applied_by, proposal_id),
        )
        return True
    except Exception as exc:
        logger.warning(
            "[flag_mgr] mark-applied failed id=%s: %s", proposal_id, exc,
        )
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def main(argv=None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        logger.error("DATABASE_URL unset")
        return 1

    import psycopg
    from psycopg.rows import dict_row
    from genlab_core.scheduling.flag_flip_proposer import collect_proposals

    mode = "propose+apply" if args.apply else "propose-only"
    print(f"\nAutonomous flag manager ({mode})")
    print(f"{'flag':50} {'from':>6} {'to':>6} {'conf':>6}")
    print("-" * 80)
    total_new = 0
    total_superseded = 0
    total_applied = 0
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        proposals = collect_proposals(conn)
        if proposals:
            for p in proposals:
                print(
                    f"  {p.flag_name:50} {p.from_state:>6} → {p.to_state:>6} "
                    f"conf={p.confidence:.2f}"
                )
                print(f"    {p.rationale}")
                if args.dry_run:
                    continue
                total_superseded += _supersede_prior_pending(conn, p.flag_name)
                if _persist_proposal(conn, p):
                    total_new += 1
        else:
            print("  (no new proposals today — evidence gates not met)")

        if not args.dry_run:
            conn.commit()

        # ── Session 2: auto-apply pass ───────────────────────────
        if args.apply and not args.dry_run:
            print("\nAuto-apply pass:")
            ready = _find_apply_ready_proposals(
                conn, args.override_window_hours,
                args.apply_confidence_threshold,
            )
            if not ready:
                print("  (no proposals aged past override window)")
            for row in ready:
                ok, msg = _write_env_flag(
                    args.env_path, row["flag_name"], row["to_state"],
                )
                if ok:
                    _mark_applied(conn, row["id"], "auto")
                    conn.commit()
                    total_applied += 1
                    print(
                        f"  [APPLIED] {row['flag_name']} → {row['to_state']} "
                        f"(conf={row['confidence']:.2f}): {msg}"
                    )
                else:
                    print(
                        f"  [WRITE FAILED] {row['flag_name']}: {msg}"
                    )

    logger.info(
        "[flag_mgr] proposals=%d superseded=%d applied=%d",
        total_new, total_superseded, total_applied,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
