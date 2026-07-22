#!/usr/bin/env bash
# Verify the [meta_usage] hook fired during the morning publisher run.
#
# Context: 2026-07-22 shipped `1b25aff0` (meta_http hook) + `d6f5222a`
# (metric-collector adoption). Earlier verification via retry-only fires
# was inconclusive because R-21 ambiguous-failure skipped every candidate.
# This script exercises the FRESH publisher path — 12:05 IST fire that
# actually calls the Meta Graph API.
#
# Report shape:
#   * Count of [meta_usage] lines by (service, platform)
#   * Max app_usage % (should stay < 15% at baseline)
#   * Sample first 3 lines for visual sanity
#   * Alarm if 0 lines when fresh publisher fired successfully
#
# Exit 0 always (rule #26) — this is a data-side signal, operator reads
# stdout / dashboard. If truly broken, run again in isolation with
# --strict to exit non-zero on 0 lines.
set -uo pipefail

STRICT=0
if [[ "${1:-}" == "--strict" ]]; then
  STRICT=1
fi

SINCE="${MU_SINCE:-2 hours ago}"

echo "=== [meta_usage] verification ($(date -u +'%Y-%m-%d %H:%M UTC')) ==="
echo "Window: since '${SINCE}'"
echo

# 1. Aggregate counts across the 3 primary services that call Meta APIs.
echo "--- lines per service ---"
for svc in genlab-publisher.service genlab-metric-collector.service genlab-insights-collector.service; do
  count=$(journalctl -u "$svc" --since "$SINCE" 2>/dev/null | grep -c "\[meta_usage\]")
  printf "%-40s %d\n" "$svc" "$count"
done
echo

# 2. Max app_usage % across all lines. Meta returns a JSON blob; grab
#    call_count and total_cputime as the two dimensions that matter.
echo "--- max app_usage % (should stay < 15% at baseline) ---"
max_pct=$(journalctl -u genlab-publisher.service -u genlab-metric-collector.service -u genlab-insights-collector.service --since "$SINCE" 2>/dev/null \
  | grep "\[meta_usage\]" \
  | grep -oE 'max_app_pct=[0-9]+' \
  | sort -t= -k2 -n \
  | tail -1)
echo "${max_pct:-max_app_pct=0 (no meta_usage lines in window)}"
echo

# 3. Sample the first 3 lines from the publisher fire.
echo "--- sample publisher fire (first 3 lines) ---"
journalctl -u genlab-publisher.service --since "$SINCE" 2>/dev/null \
  | grep "\[meta_usage\]" \
  | head -3
echo

# 4. Publisher fire status — did it actually run tonight? Rule #26 exit-code check.
echo "--- publisher service run state ---"
systemctl show genlab-publisher.service \
  --property=ActiveEnterTimestamp,InactiveEnterTimestamp,ExecMainStatus,Result 2>/dev/null
echo

# 5. Alarm — if publisher ran but 0 meta_usage lines, that's a real regression.
publisher_lines=$(journalctl -u genlab-publisher.service --since "$SINCE" 2>/dev/null | grep -c "\[meta_usage\]")
publisher_ran=$(systemctl show genlab-publisher.service --property=ExecMainStatus | grep -c "ExecMainStatus=0")

if [[ "$publisher_ran" -gt 0 && "$publisher_lines" -eq 0 ]]; then
  echo "ALARM: publisher exited 0 but 0 [meta_usage] lines — hook regression suspected"
  echo "       investigate meta_http.py:145 log statement + _META_SESSION adoption"
  if [[ "$STRICT" -eq 1 ]]; then
    exit 2
  fi
else
  echo "OK: publisher exited $publisher_ran, $publisher_lines [meta_usage] lines captured"
fi

# Always exit 0 per rule #26 unless --strict.
exit 0
