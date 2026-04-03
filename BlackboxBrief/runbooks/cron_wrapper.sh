#!/bin/bash
# Compatibility wrapper for cron/launchd — delegates to unified orchestrator.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/orchestrator.sh" daily "$@"
