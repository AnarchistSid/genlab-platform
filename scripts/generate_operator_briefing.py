#!/usr/bin/env python3
"""Phase 5.D — operator daily briefing bot (2026-08-15).

Fires daily at 06:00 UTC via ``genlab-operator-briefing.timer``.

  * Collects mission-control state from Postgres (publishes, alerts,
    calibration progress, pending flag flips, pending strategist
    proposals, LLM cost).
  * Anthropic Haiku writes a 5-line "what needs your judgment"
    summary. Falls back to a deterministic plain-text render if the
    LLM is unavailable (budget cap, network).
  * Persists to ``operator_briefings`` table.
  * Emails the summary to ``GENLAB_OPERATOR_EMAIL`` via
    Microsoft Graph (reuses Phase 3.C ``OutlookMailSender``).

## Usage

    uv run python scripts/generate_operator_briefing.py
    uv run python scripts/generate_operator_briefing.py --dry-run

## Exit codes

  * 0 always (rule #26) — partial success (email failed / LLM
    empty) surfaces via the dashboard card + email_error column,
    not systemd. Actual hard failures log at ERROR level.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

logger = logging.getLogger("operator_briefing")


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="Collect + render but DO NOT persist or send email")
    ap.add_argument("--no-email", action="store_true",
                    help="Persist to DB but skip email delivery")
    return ap.parse_args(argv)


def _persist(conn, result, email_sent: bool, email_recipient: str | None,
             email_error: str | None) -> None:
    """Insert one row into operator_briefings. Fail-open at insert
    time — we already have a rendered summary, log the DB miss and
    continue so systemd doesn't page for a briefing-storage hiccup."""
    try:
        conn.execute(
            """
            INSERT INTO operator_briefings
              (summary_md, structured, email_sent, email_recipient,
               email_error, llm_cost_usd, n_pending_flag_flips,
               n_pending_strategist_proposals)
            VALUES (%s, %s::jsonb, %s, %s, %s, %s, %s, %s)
            """,
            (
                result.summary_md,
                json.dumps(result.structured, default=str),
                email_sent,
                email_recipient,
                email_error,
                result.llm_cost_usd,
                result.n_pending_flag_flips,
                result.n_pending_strategist_proposals,
            ),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("[briefing] persist failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass


def _send_email(subject: str, body: str, recipient: str) -> tuple[bool, str | None]:
    """Deliver via Microsoft Graph. Returns ``(sent, error)``. All
    exceptions caught — the caller still persists the row so the
    dashboard card renders."""
    try:
        from genlab_core.integrations.outlook_sender import (
            OutlookMailSender, SendError,
        )
        sender = OutlookMailSender()
        result = sender.send(recipient, subject, body)
        if result.ok:
            return True, None
        return False, (result.reason or "unknown")
    except SendError as exc:
        return False, f"{exc.reason}: {exc.detail[:200]}"
    except Exception as exc:
        return False, str(exc)[:300]


def main(argv=None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        logger.error("DATABASE_URL unset")
        return 0  # rule #26 — data problem, not systemd problem

    import psycopg
    from psycopg.rows import dict_row
    from genlab_core.intelligence.operator_briefing import generate

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        result = generate(conn)

        print("\n" + "=" * 64)
        print("  Operator briefing")
        print("=" * 64)
        print(result.summary_md)
        print("=" * 64)
        print(
            f"  cost=${result.llm_cost_usd:.4f}  "
            f"flag_flips={result.n_pending_flag_flips}  "
            f"strategist={result.n_pending_strategist_proposals}"
        )
        print("=" * 64)

        if args.dry_run:
            logger.info("[briefing] dry-run — not persisting or sending")
            return 0

        recipient = os.environ.get("GENLAB_OPERATOR_EMAIL", "").strip()
        email_sent = False
        email_error: str | None = None

        if args.no_email or not recipient:
            if not recipient:
                email_error = "GENLAB_OPERATOR_EMAIL unset"
                logger.warning("[briefing] no operator email configured")
        else:
            subject = "Gen Lab — Operator briefing"
            email_sent, email_error = _send_email(subject, result.summary_md, recipient)
            if email_sent:
                logger.info("[briefing] email delivered to=%s", recipient)
            else:
                logger.warning(
                    "[briefing] email delivery failed: %s", email_error,
                )

        _persist(conn, result, email_sent, recipient or None, email_error)
        logger.info(
            "[briefing] persisted cost=$%.4f email_sent=%s",
            result.llm_cost_usd, email_sent,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
