#!/bin/bash
# GenLab database backup — creates timestamped pg_dump in .tmp/backups/
#
# Connection resolution (in order):
#   1. DATABASE_URL env var (preferred — works for local + prod uniformly)
#   2. Sourced from $GENLAB_ROOT/.env if present
#   3. Bare `pg_dump genlab` fallback (local-socket auth as OS user)
#
# The fallback existed historically but breaks on prod (deploy.sh runs
# as root; the `root` Postgres role doesn't exist; pg_dump errors with
# `FATAL: role "root" does not exist`). Sourcing .env makes the script
# self-sufficient when invoked from deploy.sh in an environment that
# doesn't have DATABASE_URL exported.
set -euo pipefail

GENLAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="$GENLAB_ROOT/.tmp/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/genlab_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

# Resolve DATABASE_URL — prefer already-exported, fall back to .env.
# `set -a` exports every assigned variable, so sourcing .env makes the
# DSN visible to pg_dump as an env var (defense-in-depth even though we
# also pass it positionally below).
if [[ -z "${DATABASE_URL:-}" && -f "$GENLAB_ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$GENLAB_ROOT/.env"
    set +a
fi

echo "[backup] Starting database backup..."
if [[ -n "${DATABASE_URL:-}" ]]; then
    # pg_dump accepts a libpq URL as its positional `dbname` arg since
    # PostgreSQL 9.6. This bypasses the local-socket + OS-user-as-role
    # default that breaks for `root`-invoked deploy.sh on prod.
    pg_dump "$DATABASE_URL" | gzip > "$BACKUP_FILE"
else
    # Legacy fallback — only works when the invoking OS user matches a
    # Postgres role (typical for local dev where the dev runs as their
    # own user and a matching role exists).
    echo "[backup] WARN: DATABASE_URL not set — falling back to socket auth as $(id -un)" >&2
    pg_dump genlab | gzip > "$BACKUP_FILE"
fi

SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "[backup] Done: $BACKUP_FILE ($SIZE)"

# Keep only last 7 backups
cd "$BACKUP_DIR"
ls -t genlab_*.sql.gz 2>/dev/null | tail -n +8 | xargs rm -f 2>/dev/null || true
echo "[backup] Retention: kept last 7 backups"
