#!/usr/bin/env bash
set -euo pipefail

DASHBOARD_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GENLAB_ROOT="$(cd "$DASHBOARD_ROOT/.." && pwd)"

# Load environment from root .env + BlackboxBrief .env (platform credentials)
for envfile in "$GENLAB_ROOT/.env" "$GENLAB_ROOT/BlackboxBrief/.env"; do
if [ -f "$envfile" ]; then
    while IFS= read -r line; do
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        key="${line%%=*}"
        value="${line#*=}"
        key=$(echo "$key" | xargs)
        value="${value#\'}" ; value="${value%\'}"
        value="${value#\"}" ; value="${value%\"}"
        [ -z "$key" ] && continue
        export "$key=$value"
    done < "$envfile"
fi
done

export GENLAB_PROJECT_ROOT="$GENLAB_ROOT"
export BACKLOG_CONFIG_PATH="$GENLAB_ROOT/genlab-core/config/lists_config.yaml"

WORKSPACE_VENV="$GENLAB_ROOT/.venv/bin"
if [ -d "$WORKSPACE_VENV" ]; then
    export PATH="$WORKSPACE_VENV:$PATH"
fi

DIST="$DASHBOARD_ROOT/frontend/dist"
if [ ! -d "$DIST" ] || [ "$(find "$DASHBOARD_ROOT/frontend/src" -newer "$DIST/index.html" 2>/dev/null | head -1)" ]; then
    echo "[$(date)] Building dashboard..."
    cd "$DASHBOARD_ROOT/frontend"
    npm run build
fi

LOG_DIR="$GENLAB_ROOT/.logs"
mkdir -p "$LOG_DIR"

# Run from the GenLab workspace root so uv can find all workspace members.
cd "$GENLAB_ROOT"

# psycopg2 is fully synchronous — no event loop conflicts with eventlet.
# GENLAB_USE_POSTGRES=true (from .env) routes all queries to PostgreSQL.
exec /Users/anarchistsid/.local/bin/uv run --project "$DASHBOARD_ROOT" \
    gunicorn \
    --worker-class eventlet \
    --workers 1 \
    --timeout 120 \
    --max-requests 500 \
    --max-requests-jitter 50 \
    --bind 0.0.0.0:5151 \
    --access-logfile "$LOG_DIR/review_server_access.log" \
    --error-logfile "$LOG_DIR/review_server_error.log" \
    --capture-output \
    "server.review_server:app"
