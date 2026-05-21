#!/usr/bin/env python3
"""Weekly config auto-update — translates bandit learning into YAML config changes.

Reads completed PendingFeedbackTask records from the last 30 days,
identifies better posting hours and content type ratios,
and writes conservative YAML updates.

Usage:
    uv run --package genlab-core python -m genlab_core.scripts.run_config_update
    uv run --package genlab-core python -m genlab_core.scripts.run_config_update --dry-run
    uv run --package genlab-core python -m genlab_core.scripts.run_config_update --niche-id gaming
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("genlab.config_update")

# Niche config directories
NICHE_CONFIG_DIRS: dict[str, str] = {
    "ai_creators": "BlackboxBrief/config",
    "gaming": "CriticalRush/niches/gaming/config",
    "sports": "ClutchWire/config",
    "movies": "SpliceReel/config",
    "anime": "FrameDrift/config",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Weekly config auto-update from bandit learning")
    parser.add_argument("--niche-id", default="all", choices=list(NICHE_CONFIG_DIRS.keys()) + ["all"])
    parser.add_argument("--dry-run", action="store_true", help="Log changes without writing")
    args = parser.parse_args()

    niches = list(NICHE_CONFIG_DIRS.keys()) if args.niche_id == "all" else [args.niche_id]

    try:
        from genlab_core.http.backlog_client import BacklogClient
        from genlab_core.learning.config_updater import ConfigUpdater
        from genlab_core.learning.pending_feedback_store import PendingFeedbackStore
    except ImportError as e:
        logger.error("Required modules not available: %s", e)
        return 1

    # Use GENLAB_PROJECT_ROOT (set by launch_wrapper.sh) for correct niche
    # paths. _PROJECT_ROOT from settings.py is wrong here because AGENT_ROOT
    # is set to CriticalRush in .env, making it resolve to GenLab/CriticalRush/
    # instead of GenLab/.
    import os
    _PROJECT_ROOT = Path(os.environ.get(
        "GENLAB_PROJECT_ROOT",
        str(Path(__file__).resolve().parent.parent.parent.parent.parent),
    ))

    client = BacklogClient()
    store = PendingFeedbackStore(client)

    # Get all completed feedback records
    try:
        proxy = client.pending_feedback
        records_raw = proxy.all(formula="{collection_status}='complete'")
        logger.info("Found %d completed feedback records", len(records_raw))
    except Exception as exc:
        logger.error("Failed to fetch feedback records: %s", exc)
        return 1

    # Parse into PendingFeedbackTask objects
    feedback_records = []
    for item in records_raw:
        try:
            task = store._from_sharepoint_item(item)
            feedback_records.append(task)
        except Exception:
            pass

    logger.info("Parsed %d valid feedback records", len(feedback_records))

    total_changes = 0
    for niche_id in niches:
        config_dir = _PROJECT_ROOT / NICHE_CONFIG_DIRS[niche_id]
        if not config_dir.exists():
            logger.warning("Config dir not found: %s", config_dir)
            continue

        niche_records = [r for r in feedback_records if r.niche_id == niche_id]
        logger.info("=== %s: %d records ===", niche_id, len(niche_records))

        updater = ConfigUpdater(config_dir)
        changes = updater.run(niche_records, dry_run=args.dry_run)
        total_changes += len(changes)

        for change in changes:
            logger.info("  %s: %s → %s (was %s)", change["file"], change["field"], change["new"], change["old"])

        if changes and not args.dry_run:
            _persist_changes(niche_id, changes, dry_run=args.dry_run)
        elif changes and args.dry_run:
            # Still record dry-run rows so the dashboard can preview
            # what *would* have changed had operators flipped the gate.
            _persist_changes(niche_id, changes, dry_run=True)

    prefix = "[DRY RUN] " if args.dry_run else ""
    logger.info("%sDone: %d config changes across %d niches", prefix, total_changes, len(niches))
    return 0


def _persist_changes(niche_id: str, changes: list[dict], dry_run: bool) -> None:
    """Append config-update audit rows to the config_updates table.

    Failure to persist is logged but non-fatal — the YAML write
    already happened, and missing an audit row is recoverable. The
    dashboard's Config Updates tab consumes these rows.
    """
    try:
        import json as _json
        import os

        import psycopg

        db_url = os.environ.get("DATABASE_URL", "").strip()
        if not db_url:
            logger.warning("[CONFIG_UPDATE] DATABASE_URL unset — skip persist")
            return
        with psycopg.connect(db_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                for change in changes:
                    cur.execute(
                        """
                        INSERT INTO config_updates
                            (niche_id, file_path, field, old_value,
                             new_value, reason, n_records, dry_run, extra)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            niche_id,
                            change.get("file", ""),
                            change.get("field", ""),
                            str(change.get("old", "")),
                            str(change.get("new", "")),
                            change.get("reason", ""),
                            int(change.get("n", 0) or 0),
                            dry_run,
                            _json.dumps({
                                k: v for k, v in change.items()
                                if k not in (
                                    "file", "field", "old", "new",
                                    "reason", "n",
                                )
                            }),
                        ),
                    )
            conn.commit()
    except Exception as exc:
        logger.warning("[CONFIG_UPDATE] persist failed: %s", exc)


if __name__ == "__main__":
    sys.exit(main())
