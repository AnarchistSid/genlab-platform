#!/usr/bin/env bash
# ============================================================================
# cleanup_stale_visual_ready.sh — demote VISUAL_READY blueprints whose
# rendered media has been garbage-collected.
#
# Why this exists (2026-07-18): discovered by inspecting the 12:00 IST
# publisher fire journal. Blueprints from 2026-06-27 (3 weeks ago) were
# still sitting in VISUAL_READY, being retried by parallel_publish, and
# failing with MISSING_RENDER because disk_quota.py had cleaned up their
# .tmp/runs/ media as expired.
#
# The publisher wastes time trying to publish these + logs errors that
# obscure real prod issues. This script demotes them back to DRAFTED so
# they either get re-rendered by the render stage OR the render-failure
# reason surfaces cleanly for the operator.
#
# ## What it does
#
#   1. SELECT blueprints WHERE status IN ('VISUAL_READY', 'APPROVED')
#      AND scheduled_for IS NULL (rule from CLAUDE.md cleanup_safety.md)
#      AND visual_paths IS NOT NULL
#   2. For each row: check if ANY of the paths in visual_paths (JSON
#      array of file paths) exists on disk
#   3. If NONE exist: mark as stale
#   4. Dry-run: print stats. Only.
#   5. --apply: UPDATE stale rows to status='DRAFTED' +
#      error_message='cleanup:media_gc_removed'
#
# ## Ship-safety
#
# - Idempotent: after a run, demoted blueprints won't match on re-run
# - Read-only by default: --apply required for state change
# - EXCLUDES scheduled_for IS NOT NULL per cleanup_safety.md rule
# - No effect on blueprints with valid media (path existence is the gate)
#
# ## Usage
#
#   ./scripts/cleanup_stale_visual_ready.sh            # dry-run (default)
#   ./scripts/cleanup_stale_visual_ready.sh --apply    # execute demotion
# ============================================================================
set -euo pipefail

MODE="dry-run"
if [[ "${1:-}" == "--apply" ]]; then
    MODE="apply"
elif [[ -n "${1:-}" ]]; then
    echo "Usage: $0 [--apply]" >&2
    exit 2
fi

GENLAB=/opt/genlab
VENV=$GENLAB/.venv/bin/python
ENV_FILE=$GENLAB/.env

if [ ! -f "$ENV_FILE" ]; then
    echo "[fatal] $ENV_FILE not found — are we on the prod box?" >&2
    exit 2
fi

DATABASE_URL=$(grep -E "^DATABASE_URL=" "$ENV_FILE" | head -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'\$//")
if [ -z "$DATABASE_URL" ]; then
    echo "[fatal] DATABASE_URL not found in $ENV_FILE" >&2
    exit 2
fi
export DATABASE_URL
export MODE

sudo -u genlab -E $VENV - <<'PY'
import json
import os
import sys
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone

import psycopg

MODE = os.environ.get("MODE", "dry-run")

conn = psycopg.connect(os.environ["DATABASE_URL"])
conn.autocommit = False
cur = conn.cursor()

# Two categories per CLAUDE.md cleanup_safety.md:
#   (a) UNSCHEDULED stale — safe to demote
#   (b) SCHEDULED stale — REPORT ONLY. cleanup_safety.md forbids touching
#       scheduled posts. But these WILL fail on their fire date because
#       media has been GC'd. Reporting lets operator decide.
cur.execute("""
    SELECT id, niche_id, extra->>'visual_paths', created_at, scheduled_for
    FROM blueprints
    WHERE status IN ('VISUAL_READY', 'APPROVED')
      AND extra ? 'visual_paths'
      AND extra->>'visual_paths' <> ''
    ORDER BY created_at
""")

stale_rows = []            # unscheduled + stale (demotion-eligible)
scheduled_stale_rows = []  # scheduled + stale (report only)
inspected = 0

for row in cur.fetchall():
    bp_id, niche_id, visual_paths_raw, created_at, scheduled_for = row
    inspected += 1

    try:
        paths = json.loads(visual_paths_raw)
        if not isinstance(paths, list):
            paths = [visual_paths_raw]
    except (json.JSONDecodeError, TypeError):
        paths = [visual_paths_raw]

    any_present = False
    for p in paths:
        if p and Path(str(p)).is_file():
            any_present = True
            break
    if not any_present:
        first_path = paths[0] if paths else "?"
        if scheduled_for is not None:
            scheduled_stale_rows.append((bp_id, niche_id, first_path, created_at, scheduled_for))
        else:
            stale_rows.append((bp_id, niche_id, first_path, created_at))

print(f"[cleanup] inspected {inspected} blueprints in VISUAL_READY/APPROVED")
print(f"[cleanup] {len(stale_rows)} unscheduled-stale (demotion-eligible)")
print(f"[cleanup] {len(scheduled_stale_rows)} scheduled-stale (REPORT ONLY - will fail on fire)")

if scheduled_stale_rows:
    print()
    print("=" * 60)
    print(" SCHEDULED-STALE - cleanup_safety.md forbids touching these,")
    print(" BUT they will fail on their scheduled fire date because media")
    print(" has been garbage-collected. Operator action needed:")
    print("   * re-render the source, OR")
    print("   * cancel the schedule (UPDATE ... SET scheduled_for = NULL), OR")
    print("   * accept the failure at fire time")
    print("=" * 60)
    from collections import Counter as _C
    by_niche = _C(r[1] for r in scheduled_stale_rows)
    print(f" by niche: {dict(by_niche)}")
    upcoming = sorted(scheduled_stale_rows, key=lambda r: r[4])[:5]
    print(" next 5 to fire:")
    for bp_id, niche_id, _, _, scheduled_for in upcoming:
        print(f"   {str(bp_id)[:8]} niche={niche_id} scheduled={scheduled_for.date()}")

if not stale_rows:
    print()
    print("[cleanup] no unscheduled-stale rows to demote - script exits clean")
    sys.exit(0)

now = datetime.now(timezone.utc)
age_buckets = Counter()
for _, niche_id, _, created_at in stale_rows:
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    age_days = (now - created_at).days
    if age_days < 1:
        age_buckets["<1d"] += 1
    elif age_days < 7:
        age_buckets["1-6d"] += 1
    elif age_days < 30:
        age_buckets["7-29d"] += 1
    else:
        age_buckets["30d+"] += 1
print(f"[cleanup] age distribution: {dict(age_buckets)}")

niche_buckets = Counter(r[1] for r in stale_rows)
print(f"[cleanup] by niche: {dict(niche_buckets)}")

print("[cleanup] sample (first 5):")
for bp_id, niche_id, path, created_at in stale_rows[:5]:
    print(f"  {str(bp_id)[:8]} niche={niche_id} created={created_at.date()} missing={path[:80]}")

if MODE == "dry-run":
    print()
    print("=" * 60)
    print(f" DRY-RUN - would demote {len(stale_rows)} blueprint(s) to DRAFTED")
    print(" Re-run with --apply to execute")
    print("=" * 60)
    sys.exit(0)

print()
print(f"[cleanup] --apply - demoting {len(stale_rows)} blueprint(s)...")
ids = [r[0] for r in stale_rows]
cur.execute(
    """
    UPDATE blueprints
    SET status = 'DRAFTED',
        error_message = 'cleanup:media_gc_removed'
    WHERE id = ANY(%s)
      AND scheduled_for IS NULL
    """,
    (ids,),
)
conn.commit()
print(f"[cleanup] demoted {cur.rowcount} row(s) VISUAL_READY/APPROVED -> DRAFTED")
print("[cleanup] error_message set to 'cleanup:media_gc_removed' for visibility")
PY
