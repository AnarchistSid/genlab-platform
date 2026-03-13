#!/bin/bash
# Start Postiz for shadow evaluation
# Prerequisites: Docker Desktop must be running

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "Starting Postiz containers..."
docker compose up -d

echo "Waiting for Postiz to be ready..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:5100/api/health > /dev/null 2>&1; then
        echo "Postiz is ready at http://localhost:5100"
        exit 0
    fi
    sleep 2
done

echo "WARNING: Postiz did not become ready within 60 seconds."
echo "Check logs: docker compose -f $SCRIPT_DIR/docker-compose.yaml logs -f postiz"
exit 1
