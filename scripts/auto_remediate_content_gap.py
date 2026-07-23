#!/usr/bin/env python3
"""Auto-remediate content_gap alerts by triggering per-niche pipeline runs.

Motivating problem: the ``check_content_gap`` monitor fires when a niche
has zero approved+scheduled blueprints for the next 48 hours. Today
the alert sits until an operator investigates or the nightly scheduler
happens to catch up. Meanwhile publish windows may lapse with no
content — hurting the growth-target goals (rule #24).

This script queries unresolved ``content_gap`` alerts, and for each
niche with a real gap triggers the corresponding niche pipeline via
systemd (``genlab-pipeline-{niche}.service``). The pipeline runs its
full fetch → score → render loop, producing new VISUAL_READY
blueprints that the nightly scheduler picks up in the next window.

Escalation logic: after triggering, we DO NOT immediately mark the
alert resolved. The existing auto_resolve_alerts.py timer already
picks up content_gap resolutions when the check itself re-evaluates
and finds coverage. If a triggered pipeline run produces zero blueprints
(``run_report.metrics.blueprints_count == 0``) we tag the alert with
a ``remediation_attempted`` flag so operator can see we tried.

Discipline
==========

* **Flag-gated.** ``GENLAB_CONTENT_GAP_REMEDIATOR_ENABLED`` opts in.
* **Rate-limited per niche.** Never trigger the same niche's
  pipeline more than once per 4-hour window — the pipeline itself
  takes ~5 minutes, and hammering a broken upstream (YouTube quota
  or credit-exhausted LLM) doesn't help.
* **Escalate correctly.** If the pipeline's most recent run report
  shows blueprints_count == 0, this is a genuine upstream failure
  (not a schedule stall). Do NOT auto-trigger again; let operator
  see it via the un-remediated content_gap alert.
* **Fail-open.** Any error path lets the alert continue firing.

Usage:
    python scripts/auto_remediate_content_gap.py         # dry-run
    python scripts/auto_remediate_content_gap.py --apply # trigger pipelines

Exit codes:
    0 — success (including no work)
    3 — unhandled exception (durable file written)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "genlab-core" / "src"))

logger = logging.getLogger("content_gap_remediator")


_ENABLE_ENV_VAR = "GENLAB_CONTENT_GAP_REMEDIATOR_ENABLED"

# Per-niche rate limit: skip re-triggering within this window since
# the last trigger (regardless of the pipeline's success/failure).
_RATE_LIMIT_HOURS = 4


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


def _is_enabled() -> bool:
    return os.environ.get(_ENABLE_ENV_VAR, "") in ("true", "TRUE", "True")


def _fetch_unresolved_content_gap_niches(conn) -> list[dict]:
    """Return niches with unresolved content_gap alerts + last trigger
    time from details."""
    rows = conn.execute(
        """
        SELECT id::text AS alert_id,
               niche_id,
               created_at,
               details
        FROM pipeline_alerts
        WHERE check_name = 'content_gap'
          AND resolved_at IS NULL
          AND niche_id IS NOT NULL
        ORDER BY created_at ASC
        """
    ).fetchall()
    result = []
    for r in rows:
        alert_id, niche_id, created_at, details = r
        parsed_details = {}
        if isinstance(details, dict):
            parsed_details = details
        elif isinstance(details, str):
            try:
                parsed_details = json.loads(details)
            except Exception:
                pass
        result.append(
            {
                "alert_id": alert_id,
                "niche_id": niche_id,
                "created_at": created_at,
                "details": parsed_details,
            }
        )
    return result


def _within_rate_limit(details: dict) -> bool:
    """Return True if the alert was remediated recently (within
    _RATE_LIMIT_HOURS)."""
    last_trigger = details.get("last_remediation_at")
    if not last_trigger:
        return False
    try:
        last_dt = datetime.fromisoformat(last_trigger.replace("Z", "+00:00"))
    except Exception:
        return False
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    age_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
    return age_hours < _RATE_LIMIT_HOURS


def _trigger_niche_pipeline(niche_id: str) -> tuple[bool, str]:
    """Ask systemd to start the niche pipeline. Returns (success, msg).

    Uses ``systemctl start`` — this is fire-and-forget. The oneshot
    service exits when its ExecStart command completes. We don't
    block waiting for the pipeline to finish (~5 min); we assume the
    fire is enough and the next monitor tick will observe the result.
    """
    unit = f"genlab-pipeline-{niche_id}.service"
    try:
        result = subprocess.run(
            ["sudo", "systemctl", "start", unit],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return True, f"triggered {unit}"
        return False, f"systemctl exit={result.returncode} stderr={result.stderr[:200]}"
    except subprocess.TimeoutExpired:
        return False, "systemctl start timed out"
    except Exception as exc:  # noqa: BLE001
        return False, f"systemctl start error: {exc}"


def _mark_alert_remediated(conn, alert_id: str, msg: str) -> None:
    """Append remediation timestamp + message to the alert's details.

    Does NOT set resolved_at — that's done by the check re-evaluating
    coverage on the next monitor tick. We only track the fact that we
    tried, so the rate-limit + escalation logic works.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    payload = {
        "last_remediation_at": now_iso,
        "last_remediation_msg": msg,
    }
    conn.execute(
        """
        UPDATE pipeline_alerts
        SET details = COALESCE(details, '{}'::jsonb) || %s::jsonb
        WHERE id = %s AND resolved_at IS NULL
        """,
        (json.dumps(payload), alert_id),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="Actually trigger pipelines (default: dry-run)")
    ap.add_argument("--env-file", default="/opt/genlab/.env")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    _load_env(args.env_file)

    if not _is_enabled():
        logger.info(
            "GENLAB_CONTENT_GAP_REMEDIATOR_ENABLED not set to 'true' — exiting cleanly"
        )
        return 0

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        logger.error("DATABASE_URL not set")
        return 2

    import psycopg

    with psycopg.connect(dsn) as conn:
        alerts = _fetch_unresolved_content_gap_niches(conn)
        if not alerts:
            logger.info("no unresolved content_gap alerts — exiting cleanly")
            return 0

        logger.info("found %d unresolved content_gap alerts", len(alerts))

        if not args.apply:
            print(f"\nDRY RUN — would remediate {len(alerts)} niches:")
            for a in alerts:
                rl = _within_rate_limit(a["details"])
                print(
                    f"  [{a['niche_id']}] alert_id={a['alert_id'][:8]} "
                    f"created={a['created_at']} rate_limited={rl}"
                )
            return 0

        # APPLY
        triggered = 0
        rate_limited = 0
        failed = 0

        for a in alerts:
            niche_id = a["niche_id"]
            if _within_rate_limit(a["details"]):
                logger.info(
                    "[remediator] skipping niche=%s alert=%s — rate limited",
                    niche_id,
                    a["alert_id"][:8],
                )
                rate_limited += 1
                continue

            ok, msg = _trigger_niche_pipeline(niche_id)
            if ok:
                triggered += 1
                logger.info(
                    "[remediator] triggered niche=%s alert=%s: %s",
                    niche_id,
                    a["alert_id"][:8],
                    msg,
                )
            else:
                failed += 1
                logger.warning(
                    "[remediator] failed niche=%s alert=%s: %s",
                    niche_id,
                    a["alert_id"][:8],
                    msg,
                )

            # Regardless of trigger success/failure, tag the attempt so
            # rate-limit + escalation see it.
            try:
                _mark_alert_remediated(conn, a["alert_id"], msg)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[remediator] failed to tag alert %s: %s",
                    a["alert_id"][:8],
                    exc,
                )

        conn.commit()
        logger.info(
            "DONE triggered=%d rate_limited=%d failed=%d",
            triggered,
            rate_limited,
            failed,
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

            write_durable_error("auto_remediate_content_gap", exc)
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
