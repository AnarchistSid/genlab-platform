#!/bin/bash
set -euo pipefail

# Weekly Learning Cycle — Auto-tune virality scoring weights
# Schedule: Sunday midnight UTC (crontab or launchd)
# Cron example: 0 0 * * 0 /path/to/runbooks/weekly_learning.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "Blackbox Brief — Weekly Learning Cycle"
echo "Started: $(date)"
echo "Project: $PROJECT_ROOT"

# Activate venv — prefer uv workspace venv, fall back to local venv
_WORKSPACE_VENV="$(cd "$PROJECT_ROOT/.." && pwd)/.venv/bin/activate"
_LOCAL_VENV="$PROJECT_ROOT/venv/bin/activate"
if [ -f "$_WORKSPACE_VENV" ]; then
    source "$_WORKSPACE_VENV"
elif [ -f "$_LOCAL_VENV" ]; then
    source "$_LOCAL_VENV"
fi

# Load env
if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a
    source "$PROJECT_ROOT/.env"
    set +a
fi

# Backup current weights
BACKUP_NAME="virality_scoring.yaml.bak.$(date +%Y%m%d)"
cp "$PROJECT_ROOT/config/virality_scoring.yaml" "$PROJECT_ROOT/config/$BACKUP_NAME"
echo "Backup: config/$BACKUP_NAME"

# Run learner
/Users/anarchistsid/.local/bin/uv run --package content-scraper python "$PROJECT_ROOT/execution/performance_learner.py" --lookback-days 7 --verbose

echo ""
echo "Learning cycle complete."
echo "Log: .tmp/learning_logs/"
echo "Backup: config/$BACKUP_NAME"
echo "Finished: $(date)"
