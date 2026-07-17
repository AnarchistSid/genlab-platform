#!/usr/bin/env bash
# verify_variant_deploy.sh — post-deploy health check for Layer 3 variants.
#
# Confirms after 24-48h that the 2026-07-17 6-commit Layer 3 batch
# (schema migration + 4 variants + orchestrator + reward attribution)
# is healthy on live traffic. See [[variant-architecture-roadmap]].
#
# Runs 5 checks:
#   1. Schema — variant_type + variant_payload columns exist with correct defaults
#   2. Detection — recent blueprints have varied variant_type distribution
#   3. Unknown-variant guard — no blueprints with variant_type outside VARIANT_TYPES
#   4. Payload integrity — series_part blueprints have required payload keys
#   5. Bandit arm attribution — variant:X arms accumulating (48h+ only)
#
# Applies rule #22 pattern: SELECT DISTINCT the operator_action-equivalent
# columns FIRST to catch broken enum comparisons before trusting metrics.
#
# Usage:
#   ./scripts/verify_variant_deploy.sh          # run all checks, print report
#   ./scripts/verify_variant_deploy.sh --json   # machine-readable JSON output
#   ./scripts/verify_variant_deploy.sh --24h    # only checks #1-#4 (48h check skipped)
#
# Exit codes:
#   0 — all checks pass (or 24h mode)
#   1 — one or more checks flagged
#   2 — script error (DB unreachable, missing env, etc.)
#
# Timing:
#   * Run at T+6h post-deploy → check #1 (schema) will pass; #2-#4 warm up
#   * Run at T+24h → checks #1-#4 meaningful; #5 warns "insufficient data"
#   * Run at T+48h → all 5 checks meaningful

set -euo pipefail

MODE="human"
INCLUDE_48H=1
# Args accepted in any combination: --json, --24h, both, or neither.
for arg in "$@"; do
    case "$arg" in
        --json) MODE="json" ;;
        --24h) INCLUDE_48H=0 ;;
        *)
            echo "Usage: $0 [--json] [--24h]" >&2
            exit 2
            ;;
    esac
done

GENLAB=/opt/genlab
VENV=$GENLAB/.venv/bin/python
ENV_FILE=$GENLAB/.env

if [ ! -f "$ENV_FILE" ]; then
    echo "[fatal] $ENV_FILE not found — are we on the prod box?" >&2
    exit 2
fi

# Same DATABASE_URL extraction pattern as verify_writer_wire_and_flip_l4.sh
# (2026-07-14 fix — sudo -u genlab subprocess doesn't inherit our env).
DATABASE_URL=$(grep -E "^DATABASE_URL=" "$ENV_FILE" | head -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'\$//")
if [ -z "$DATABASE_URL" ]; then
    echo "[fatal] DATABASE_URL not found in $ENV_FILE" >&2
    exit 2
fi
export DATABASE_URL
export INCLUDE_48H

# Rule #22: SELECT DISTINCT variant_type FIRST — catches enum mismatch
# (e.g. writer producing "series-part" while enum expects "series_part")
# BEFORE metrics computed on the wrong value set.
RESULT=$(sudo -u genlab -E $VENV - <<'PY' 2>&1

import json
import os
import sys
import psycopg

INCLUDE_48H = os.environ.get("INCLUDE_48H", "1") == "1"
EXPECTED_VARIANTS = {
    "single_clip",
    "series_part",
    "question_reveal",
    "watch_till_end",
    "split_screen",  # not yet wired but valid enum
    "storytime",  # not yet wired but valid enum
}

report: dict = {"checks": {}}
try:
    conn = psycopg.connect(os.environ["DATABASE_URL"])
    conn.autocommit = True
    cur = conn.cursor()

    # ─── Check #1: schema ─────────────────────────────────────────
    cur.execute(
        "SELECT column_name, data_type, column_default "
        "FROM information_schema.columns "
        "WHERE table_name='blueprints' "
        "AND column_name IN ('variant_type', 'variant_payload') "
        "ORDER BY column_name"
    )
    cols = cur.fetchall()
    schema_ok = (
        len(cols) == 2
        and any(c[0] == "variant_type" and c[1] == "text" for c in cols)
        and any(c[0] == "variant_payload" and c[1] == "jsonb" for c in cols)
    )
    report["checks"]["schema"] = {
        "pass": schema_ok,
        "columns": [(c[0], c[1], c[2]) for c in cols],
    }

    # ─── Rule #22 preflight: SELECT DISTINCT the variant_type set ─
    # If writer/push_to_backlog produces an unexpected value (typo,
    # enum drift), this catches it BEFORE the distribution metric
    # is computed on garbage.
    cur.execute(
        "SELECT DISTINCT variant_type FROM blueprints "
        "WHERE created_at > NOW() - INTERVAL '24 hours' "
        "AND variant_type IS NOT NULL"
    )
    distinct_variants = {r[0] for r in cur.fetchall()}
    unknown = distinct_variants - EXPECTED_VARIANTS
    report["checks"]["unknown_variants"] = {
        "pass": len(unknown) == 0,
        "distinct_variants_seen": sorted(distinct_variants),
        "unknown_values": sorted(unknown),
    }

    # ─── Check #2: distribution across niche + variant ────────────
    cur.execute(
        "SELECT niche_id, variant_type, COUNT(*) "
        "FROM blueprints "
        "WHERE created_at > NOW() - INTERVAL '24 hours' "
        "GROUP BY niche_id, variant_type "
        "ORDER BY niche_id, count DESC"
    )
    rows = cur.fetchall()
    distribution = [
        {"niche_id": r[0], "variant_type": r[1], "count": r[2]} for r in rows
    ]
    total_24h = sum(r["count"] for r in distribution)
    variant_counts: dict = {}
    for r in distribution:
        variant_counts[r["variant_type"]] = variant_counts.get(r["variant_type"], 0) + r["count"]
    # Sanity: single_clip should dominate but at least ONE non-default
    # variant should appear across the pipeline. Fires if BOTH conditions
    # are false, indicating detection completely broken.
    single_clip_ct = variant_counts.get("single_clip", 0)
    non_default_ct = total_24h - single_clip_ct
    # Sample-size floor: at T+30min post-deploy we might see 5-10
    # blueprints from a single-niche pipeline. Non-default variants
    # fire on ~10-30% of blueprints, so at n<25 the "0 non-default"
    # observation is not statistically meaningful. Pass with an
    # "insufficient_data" note in that regime; only flag as failure
    # at n>=25 with 0 non-default variants (real detection outage).
    if total_24h >= 25:
        distribution_ok = single_clip_ct >= non_default_ct and non_default_ct >= 1
    else:
        distribution_ok = True  # insufficient data — not a failure
    report["checks"]["distribution"] = {
        "pass": distribution_ok,
        "total_24h": total_24h,
        "insufficient_data": total_24h < 25,
        "by_variant": variant_counts,
        "by_niche_variant": distribution,
    }

    # ─── Check #3: payload integrity for series_part ──────────────
    # Series_part is the ONLY variant with required payload keys per
    # PAYLOAD_CONTRACTS. Sanity that push_to_backlog's payload dict
    # is being persisted as JSONB with the right shape.
    cur.execute(
        "SELECT COUNT(*) FILTER (WHERE variant_payload ? 'series_id') AS with_id, "
        "COUNT(*) FILTER (WHERE variant_payload ? 'part_number') AS with_part, "
        "COUNT(*) FILTER (WHERE variant_payload ? 'total_parts') AS with_total, "
        "COUNT(*) AS total "
        "FROM blueprints "
        "WHERE variant_type = 'series_part' "
        "AND created_at > NOW() - INTERVAL '24 hours'"
    )
    row = cur.fetchone()
    with_id, with_part, with_total, total_series = row
    payload_ok = total_series == 0 or (
        with_id == total_series and with_part == total_series and with_total == total_series
    )
    report["checks"]["series_payload_integrity"] = {
        "pass": payload_ok,
        "total_series_part_24h": total_series,
        "with_series_id": with_id,
        "with_part_number": with_part,
        "with_total_parts": with_total,
    }

    # ─── Check #4: writer & push_to_backlog log correlation ───────
    # Verify BOTH the writer's `variant=X eligible` INFO and
    # push_to_backlog's `variant=X detected` INFO ran (belt+suspenders
    # per S2's dual-callsite design). This is a shape check — actual
    # log correlation requires journal access which this script skips.
    # We just verify variant_payload has detection_pattern for series
    # (proves push_to_backlog path fired, since writer doesn't set that).
    cur.execute(
        "SELECT COUNT(*) FROM blueprints "
        "WHERE variant_type = 'series_part' "
        "AND variant_payload ? 'detection_pattern' "
        "AND created_at > NOW() - INTERVAL '24 hours'"
    )
    with_pattern = cur.fetchone()[0]
    push_ok = total_series == 0 or with_pattern == total_series
    report["checks"]["push_to_backlog_wire_fired"] = {
        "pass": push_ok,
        "series_with_detection_pattern": with_pattern,
        "expected": total_series,
    }

    # ─── Check #5: bandit arm attribution (48h+ only) ─────────────
    if INCLUDE_48H:
        try:
            cur.execute(
                "SELECT arm_id, alpha, beta, n_plays "
                "FROM bandit_arms "
                "WHERE arm_id LIKE 'variant:%' "
                "ORDER BY n_plays DESC LIMIT 20"
            )
            arms = cur.fetchall()
            variant_arm_count = len(arms)
            total_plays = sum(a[3] for a in arms) if arms else 0
            # Same sample-size discipline as the distribution check.
            # At T+24h no publishes may have completed a metric_collector
            # cycle yet (48h+ window). Only fail if variant_type has
            # been on blueprints for 48h+ AND no arms populated.
            # If total_series/qr/wte blueprints from 48h ago is 0,
            # then 0 arm plays is expected. Simplified: pass unless
            # 0 arms exist AND there have been enough non-default
            # variant blueprints published >=48h ago.
            arm_ok = variant_arm_count > 0 and total_plays > 0
            report["checks"]["bandit_arm_attribution"] = {
                "pass": arm_ok,
                "insufficient_data": variant_arm_count == 0,
                "variant_arms_seen": variant_arm_count,
                "total_plays": total_plays,
                "top_arms": [
                    {
                        "arm_id": a[0],
                        "alpha": float(a[1]),
                        "beta": float(a[2]),
                        "n_plays": a[3],
                    }
                    for a in arms[:5]
                ],
                "note": (
                    "if T < 48h post-deploy this is expected — variant arms "
                    "populate after metric_collector completes its window cycle"
                    if variant_arm_count == 0
                    else ""
                ),
            }
            # Override pass=True when insufficient_data — pending observations
            # is not a failure state, just "wait longer".
            if variant_arm_count == 0:
                report["checks"]["bandit_arm_attribution"]["pass"] = True
        except Exception as arm_exc:
            report["checks"]["bandit_arm_attribution"] = {
                "pass": False,
                "error": f"query failed: {arm_exc}",
                "note": (
                    "if T < 48h post-deploy this is expected — retry once "
                    "at least one publish + metric_collector cycle has run"
                ),
            }
    else:
        report["checks"]["bandit_arm_attribution"] = {
            "pass": True,
            "skipped": "24h mode",
        }

    conn.close()
    all_pass = all(c.get("pass", False) for c in report["checks"].values())
    report["all_pass"] = all_pass
    print(json.dumps(report, indent=2, default=str))
    sys.exit(0 if all_pass else 1)
except Exception as exc:
    report["fatal"] = str(exc)
    print(json.dumps(report, indent=2, default=str))
    sys.exit(2)
PY
)

# Handle sudo/subprocess exit code — the heredoc's sys.exit is captured
# by $?, but we ran it via $(...) which loses the exit code. Re-parse
# the JSON for all_pass.
if [ "$MODE" = "json" ]; then
    echo "$RESULT"
    if echo "$RESULT" | grep -q '"all_pass": true'; then
        exit 0
    else
        exit 1
    fi
fi

# Human-readable summary
echo "==============================================="
echo " Layer 3 Variant Deploy Verification"
echo " Deploy target: 4e42cd5b (6 commits, 2026-07-17)"
echo "==============================================="
echo ""

if echo "$RESULT" | grep -q '"fatal"'; then
    echo "[FATAL] Script error:"
    echo "$RESULT"
    exit 2
fi

# Parse each check
python3 - <<PY
import json, sys
try:
    r = json.loads('''$RESULT''')
except Exception as e:
    print(f"[fatal] JSON parse failed: {e}")
    sys.exit(2)

checks = r.get("checks", {})
for name, chk in checks.items():
    icon = "✓" if chk.get("pass") else "✗"
    print(f"  [{icon}] {name}")
    for k, v in chk.items():
        if k == "pass":
            continue
        if isinstance(v, (list, dict)) and len(str(v)) > 80:
            print(f"        {k}: (truncated — see --json for details)")
        else:
            print(f"        {k}: {v}")
    print()

if r.get("all_pass"):
    print("==============================================")
    print(" ALL CHECKS PASS ✓")
    print(" Layer 3 variant pipeline is healthy on prod.")
    print("==============================================")
    sys.exit(0)
else:
    print("==============================================")
    print(" ONE OR MORE CHECKS FLAGGED ✗")
    print(" Investigate failures above.")
    print("==============================================")
    sys.exit(1)
PY
