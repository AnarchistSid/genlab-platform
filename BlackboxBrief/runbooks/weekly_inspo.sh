#!/bin/bash
# Weekly Inspo Run — Ingest library → Build templates → Eval
#
# Usage:
#   Manual:  ./runbooks/weekly_inspo.sh
#   Cron:    0 10 * * 1 "$GENLAB_PROJECT_DIR/runbooks/weekly_inspo.sh"
#
# This script is cron-safe: uses absolute paths, activates venv, logs output.

set -euo pipefail

# Archived pipeline guard: disabled unless explicitly enabled.
if [ "${ENABLE_ARCHIVED_PIPELINES:-0}" != "1" ]; then
    echo "weekly_inspo is archived and disabled by default."
    echo "Set ENABLE_ARCHIVED_PIPELINES=1 to run it intentionally."
    exit 0
fi

# ── Paths (absolute for cron safety) ──────────────────────────
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

# ── Setup ─────────────────────────────────────────────────────
cd "$PROJECT_DIR"
mkdir -p "$LOG_DIR"

# Source .env for API keys (same loader as cron_wrapper.sh)
if [ -f "$PROJECT_DIR/.env" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        if [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
            var_name="${line%%=*}"
            var_value="${line#*=}"
            # Strip surrounding single or double quotes
            var_value="${var_value#\'}" ; var_value="${var_value%\'}"
            var_value="${var_value#\"}" ; var_value="${var_value%\"}"
            export "$var_name=$var_value"
        fi
    done < "$PROJECT_DIR/.env"
fi

RUN_ID="inspo_$(date +%Y%m%d_%H%M%S)"
LOG_FILE="$LOG_DIR/run_${RUN_ID}.log"

# Log to both file and stdout
exec > >(tee -a "$LOG_FILE") 2>&1

echo "═══════════════════════════════════════════════════"
echo "  Weekly Inspo Run: $RUN_ID"
echo "  Started: $(date)"
echo "═══════════════════════════════════════════════════"
echo ""

# ── Pipeline ──────────────────────────────────────────────────

echo "[1/3] Ingesting inspo library..."
"$VENV_PYTHON" execution/ingest_inspo_library.py --run-id "$RUN_ID"
echo ""

echo "[2/3] Building inspo pack..."
"$VENV_PYTHON" execution/build_inspo_pack.py --run-id "$RUN_ID"
echo ""

echo "[3/3] Running evaluation..."
"$VENV_PYTHON" execution/eval_pipeline.py --quick
echo ""

echo "═══════════════════════════════════════════════════"
echo "  Inspo run complete: $RUN_ID"
echo "  Finished: $(date)"
echo "  Log: $LOG_FILE"
echo "═══════════════════════════════════════════════════"
