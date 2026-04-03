#!/bin/bash
# Shared publisher entry point for ALL niche channels.
#
# Each channel's launchd plist calls this script with NICHE_ID set.
# This script delegates to the publish orchestrator (currently in
# BlackboxBrief, to be migrated to genlab-core in a future sprint).
#
# Usage (from launchd plist):
#   NICHE_ID=sports  scripts/publish.sh
#   NICHE_ID=movies  scripts/publish.sh
#   NICHE_ID=gaming  scripts/publish.sh
#
# The script auto-discovers the GenLab workspace root and sources
# credentials from BlackboxBrief/.env.

set -euo pipefail

GENLAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# The publish orchestrator currently lives in BlackboxBrief.
# This is a transitional arrangement — publishing infra should move
# to genlab-core. This wrapper isolates that dependency.
PUBLISHER_ROOT="$GENLAB_ROOT/BlackboxBrief"

if [ ! -d "$PUBLISHER_ROOT" ]; then
    echo "ERROR: Publisher root not found: $PUBLISHER_ROOT" >&2
    exit 1
fi

export GENLAB_PROJECT_DIR="$PUBLISHER_ROOT"
exec "$PUBLISHER_ROOT/runbooks/orchestrator.sh" publish "$@"
