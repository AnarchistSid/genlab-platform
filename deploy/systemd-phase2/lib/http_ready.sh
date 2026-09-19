#!/usr/bin/env bash
# Block until an HTTP service ANSWERS, or fail. Used as ExecStartPost.
#
# WHY A UNIT NEEDS THIS
# ---------------------
# `systemctl is-active` reports the PROCESS, not the SERVICE. On 2026-09-19
# gunicorn's master started, bound 127.0.0.1:5151 and logged "Listening at",
# while its gthread worker lost a fork race and blocked in futex_wait before
# booting. The unit was `active`. The socket ACCEPTED connections. Nothing
# answered any of them, for four hours, and every "is it up" check said yes.
#
# A listening socket is not a serving service. This probes what the service
# DOES, so a lost race fails the start and pages within the minute.
#
# Usage: http_ready.sh <url> [timeout_s] [accept_codes_regex]
set -uo pipefail
URL="${1:?usage: http_ready.sh <url> [timeout_s] [codes_regex]}"
DEADLINE=$(( SECONDS + ${2:-30} ))
OK_RE="${3:-^(2..|3..|401)$}"   # 401 = auth wall: the route is wired and answering

last=""
while [ "$SECONDS" -lt "$DEADLINE" ]; do
    # --max-time bounds EACH attempt: a socket that accepts and never speaks
    # would otherwise consume the whole window in one call.
    code=$(curl -sS --max-time 5 -o /dev/null -w "%{http_code}" "$URL" 2>/dev/null || true)
    code=${code:-000}
    last="$code"
    if [[ "$code" =~ $OK_RE ]]; then
        echo "http_ready: $URL answered $code"
        exit 0
    fi
    sleep 1
done
echo "http_ready: $URL NOT SERVING after ${2:-30}s (last code ${last}; 000 means the" >&2
echo "  port accepted the connection and nothing answered — check the worker booted," >&2
echo "  not merely that the unit is active)" >&2
exit 1
