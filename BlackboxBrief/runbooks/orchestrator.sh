#!/bin/bash
# Unified runbook orchestrator for daily/finalize/publish modes.
#
# Modes:
#   daily      -> run full daily_intel pipeline
#   finalize   -> process DRAFTED content to VISUAL_READY (video overlays only)
#   publish    -> finalize + publish due VISUAL_READY posts
#   weekly-inspo -> archived pipeline (requires ENABLE_ARCHIVED_PIPELINES=1)
#
# Usage:
#   ./runbooks/orchestrator.sh daily
#   ./runbooks/orchestrator.sh finalize
#   ./runbooks/orchestrator.sh publish
#   ./runbooks/orchestrator.sh weekly-inspo

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${GENLAB_PROJECT_DIR:-${PROJECT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}}"
# Prefer uv workspace venv, fall back to local venv
_WORKSPACE_VENV="$(cd "$PROJECT_DIR/.." && pwd)/.venv/bin/python3"
_LOCAL_VENV="$PROJECT_DIR/venv/bin/python3"
if [ -z "${VENV_PYTHON:-}" ]; then
    if [ -x "$_WORKSPACE_VENV" ]; then
        VENV_PYTHON="$_WORKSPACE_VENV"
    else
        VENV_PYTHON="$_LOCAL_VENV"
    fi
fi
LOG_DIR="$PROJECT_DIR/.tmp/logs"

MODE="${1:-}"
shift || true

if [ -z "$MODE" ]; then
    echo "Usage: $0 <daily|finalize|publish|weekly-inspo> [extra-args]"
    exit 2
fi

load_dotenv() {
    if [ -f "$PROJECT_DIR/.env" ]; then
        while IFS= read -r line || [ -n "$line" ]; do
            [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
            if [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
                local var_name="${line%%=*}"
                local var_value="${line#*=}"
                # Strip surrounding single or double quotes
                var_value="${var_value#\'}" ; var_value="${var_value%\'}"
                var_value="${var_value#\"}" ; var_value="${var_value%\"}"
                export "$var_name=$var_value"
            fi
        done < "$PROJECT_DIR/.env"
    fi
}

run_nonfatal() {
    local label="$1"
    shift
    echo "$label..."
    if "$@"; then
        echo "  ✓ $label"
    else
        echo "  ⚠️  $label failed (non-fatal)"
    fi
}

run_finalize_steps() {
    local run_id="$1"

    # Preflight: check if any work is pending (VISUAL_READY with action or DRAFTED needing render)
    local WORK_PENDING
    WORK_PENDING=$("$VENV_PYTHON" -c "
from execution.utils.backlog_client import BacklogClient
c = BacklogClient()
drafted = c.get_blueprints_by_status('DRAFTED')
vr = c.get_blueprints_by_status('VISUAL_READY')
actionable = [r for r in vr if r.get('fields', {}).get('action_taken', '').strip()]
print(len(drafted) + len(actionable))
" 2>/dev/null || echo "1")

    if [ "$WORK_PENDING" = "0" ]; then
        echo "[preflight] No DRAFTED or actionable VISUAL_READY blueprints — skipping finalize"
        return 0
    fi

    run_nonfatal "[1/5] Processing visual review decisions" \
        "$VENV_PYTHON" "$PROJECT_DIR/execution/process_review.py" --run-id "$run_id" --no-backfill
    run_nonfatal "[2/5] Adapting for YouTube + X" \
        "$VENV_PYTHON" "$PROJECT_DIR/execution/adapt_for_platforms.py" --run-id "$run_id"
    run_nonfatal "[3/5] Fetching Pexels B-roll for non-BB niches" \
        "$VENV_PYTHON" "$PROJECT_DIR/execution/fetch_broll.py" --run-id "$run_id"
    run_nonfatal "[4/5] Rendering drafted creator videos" \
        "$VENV_PYTHON" "$PROJECT_DIR/execution/render_text_overlays.py" --run-id "$run_id"
    run_nonfatal "[5/5] Validating + auto-fixing rendered videos" \
        "$VENV_PYTHON" "$PROJECT_DIR/execution/validate_videos.py" --run-id "$run_id" --fix
}

cd "$PROJECT_DIR"
mkdir -p "$LOG_DIR"
load_dotenv

case "$MODE" in
    daily)
        exec "$SCRIPT_DIR/daily_intel.sh" "$@"
        ;;

    finalize)
        RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S_fin)}"
        LOG_FILE="$LOG_DIR/finalize_${RUN_ID}.log"
        exec > >(tee -a "$LOG_FILE") 2>&1
        echo "── Finalize Approved: $RUN_ID ($(date)) ──"
        run_finalize_steps "$RUN_ID"
        echo "── Finalize complete: $RUN_ID ($(date)) ──"
        ;;

    publish)
        RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S_pub)}"
        PUBLISH_MAX_BLUEPRINTS="${PUBLISH_MAX_BLUEPRINTS:-1}"
        PUBLISH_SKIP_FINALIZE="${PUBLISH_SKIP_FINALIZE:-0}"
        LOG_FILE="$LOG_DIR/publish_${RUN_ID}.log"
        exec > >(tee -a "$LOG_FILE") 2>&1
        echo "── Publisher Run: $RUN_ID ($(date)) ──"
        if [ "$PUBLISH_SKIP_FINALIZE" = "1" ]; then
            echo "[prepublish] finalize stage skipped (PUBLISH_SKIP_FINALIZE=1)"
        else
            run_finalize_steps "$RUN_ID"
        fi
        # Sprint 37: pass --niche for strict isolation (from NICHE_ID env or default ai_creators)
        PUBLISH_NICHE="${NICHE_ID:-ai_creators}"
        "$VENV_PYTHON" "$PROJECT_DIR/execution/publish_all_platforms.py" \
            --run-id "$RUN_ID" \
            --niche "$PUBLISH_NICHE" \
            --max-blueprints "$PUBLISH_MAX_BLUEPRINTS" \
            "$@"
        run_nonfatal "[post-publish] Fetching platform analytics" \
            "$VENV_PYTHON" "$PROJECT_DIR/execution/fetch_insights.py" --run-id "$RUN_ID"
        run_nonfatal "[post-publish] Writing run report" \
            "$VENV_PYTHON" "$PROJECT_DIR/execution/write_run_report.py" --run-id "$RUN_ID" --run-type on_demand
        echo "── Publisher complete: $RUN_ID ($(date)) ──"
        ;;

    weekly-inspo)
        if [ "${ENABLE_ARCHIVED_PIPELINES:-0}" != "1" ]; then
            echo "weekly-inspo is archived and disabled by default."
            echo "Set ENABLE_ARCHIVED_PIPELINES=1 to run it intentionally."
            exit 0
        fi
        exec "$SCRIPT_DIR/weekly_inspo.sh" "$@"
        ;;

    *)
        echo "Unknown mode: $MODE"
        echo "Usage: $0 <daily|finalize|publish|weekly-inspo> [extra-args]"
        exit 2
        ;;
esac

