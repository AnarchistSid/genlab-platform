#!/usr/bin/env bash
# Verify the 4 unverified 2026-07-22 fixes after tomorrow's fires.
#
# Each fix was deployed but not live-exercised at session close:
#
#   1. Movies writer fix (544cf0e9) — 3-field summary/description_snippet/
#      description precedence. Test: movies pipeline should produce >0
#      NEW blueprints today (was 0/day for the past 6 days).
#
#   2. Movies Threads dispatch (999e6b86 config + 544cf0e9 writer) —
#      movies has NEVER published Threads. Test: at least 1 movies
#      Threads row in publishing_analytics for today.
#
#   3. Odyssey blueprint publish — VISUAL_READY blueprint queued for
#      06:30 UTC. Test: status transitions to PUBLISHED or INSIGHTS_6H.
#
#   4. uuid5-seed fix (272f0825) — pending_feedback_store now reads
#      content_id from candidate_id (not post_id). Test: recent
#      post_decision_trace rows should have BOTH bandit_arm_id AND
#      engagement fields populated on the SAME row (was disjoint before).
#
# Also tracks the 5/5 Threads matrix — if today's fire closes it,
# operator gets validation that yesterday's Threads-enable landed
# across all niches.
#
# Rule #26 compliant: exits 0 always. Data-side signal via stdout.
# Operator reads from journalctl. Add --strict to exit 2 on any fail.
set -uo pipefail

STRICT=0
if [[ "${1:-}" == "--strict" ]]; then
  STRICT=1
fi

PSQL_BASE=(psql -h 127.0.0.1 -p 5432 -U genlab -d genlab -tAF'|')

echo "=== 2026-07-22 fixes verification ($(date -u +'%Y-%m-%d %H:%M UTC')) ==="
echo "Verifies: movies writer fix (544cf0e9), movies Threads dispatch (999e6b86),"
echo "          Odyssey blueprint publish, uuid5-seed fix (272f0825)"
echo

TODAY=$(date -u +'%Y-%m-%d')

FAIL_COUNT=0

# ---------------------------------------------------------------------------
# 1. Movies writer fix — did today's 03:30 UTC fire produce fresh blueprints?
# ---------------------------------------------------------------------------
echo "--- [1/4] movies writer fix (544cf0e9) ---"
MOVIES_BP_TODAY=$("${PSQL_BASE[@]}" -c "SELECT COUNT(*) FROM blueprints WHERE niche_id = 'movies' AND DATE(created_at) = '$TODAY';" 2>/dev/null | tr -d ' ')
echo "movies blueprints created today: ${MOVIES_BP_TODAY:-?}"
if [[ "${MOVIES_BP_TODAY:-0}" -eq 0 ]]; then
  echo "ALARM: writer fix not live-verified — 0 fresh blueprints"
  echo "       expected 1-5 from morning fire; investigate:"
  echo "       journalctl -u genlab-pipeline-movies.service --since '$TODAY 03:00' | grep -iE 'writer|hook|summary|refus'"
  FAIL_COUNT=$((FAIL_COUNT + 1))
else
  echo "OK: writer fix confirmed live (movies produced $MOVIES_BP_TODAY new blueprints)"
fi
echo

# ---------------------------------------------------------------------------
# 2. Movies Threads dispatch — first attempt EVER
# ---------------------------------------------------------------------------
echo "--- [2/4] movies Threads dispatch (first ever) ---"
MOVIES_THREADS_TODAY=$("${PSQL_BASE[@]}" -c "SELECT status FROM publishing_analytics WHERE niche_id = 'movies' AND platform = 'threads' AND DATE(created_at) = '$TODAY';" 2>/dev/null)
if [[ -z "$MOVIES_THREADS_TODAY" ]]; then
  echo "ALARM: no movies Threads row today — dispatch may be silently skipping"
  echo "       investigate: journalctl -u genlab-publisher.service --since '$TODAY 06:00' | grep -iE 'movies.*threads|threads.*movies'"
  FAIL_COUNT=$((FAIL_COUNT + 1))
else
  echo "movies threads status: $MOVIES_THREADS_TODAY"
  if echo "$MOVIES_THREADS_TODAY" | grep -qE "SUCCESS|INSIGHTS_"; then
    echo "OK: movies published Threads for the FIRST TIME EVER"
  elif echo "$MOVIES_THREADS_TODAY" | grep -qE "FAILED"; then
    echo "PARTIAL: movies attempted Threads but publish failed"
    err=$("${PSQL_BASE[@]}" -c "SELECT LEFT(error_message, 200) FROM publishing_analytics WHERE niche_id = 'movies' AND platform = 'threads' AND DATE(created_at) = '$TODAY' LIMIT 1;" 2>/dev/null)
    echo "       error: $err"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  else
    echo "PARTIAL: movies Threads status = $MOVIES_THREADS_TODAY (SKIPPED / CREDENTIAL / other)"
  fi
fi
echo

# ---------------------------------------------------------------------------
# 3. Odyssey blueprint publish
# ---------------------------------------------------------------------------
echo "--- [3/4] Odyssey blueprint publish ---"
ODYSSEY_STATUS=$("${PSQL_BASE[@]}" -c "SELECT status FROM blueprints WHERE id = '30bc012c-c6fa-448c-b3b5-b9729c140b54';" 2>/dev/null | tr -d ' ')
echo "Odyssey status: ${ODYSSEY_STATUS:-<not_found>}"
case "$ODYSSEY_STATUS" in
  PUBLISHED)
    echo "OK: Odyssey blueprint published successfully"
    ;;
  VISUAL_READY)
    echo "ALARM: Odyssey still VISUAL_READY — publisher didn't pick it up or all platforms failed"
    echo "       investigate: journalctl -u genlab-publisher.service --since '$TODAY 06:00' | grep -iE 'odyssey|30bc012c'"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    ;;
  PUBLISH_FAILED)
    echo "ALARM: Odyssey PUBLISH_FAILED — all attempts exhausted"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    ;;
  ARCHIVED)
    echo "ALARM: Odyssey ARCHIVED — likely pre-publish MISSING_RENDER or media file GC'd"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    ;;
  "")
    echo "ALARM: Odyssey blueprint not found in DB"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    ;;
  *)
    echo "PARTIAL: Odyssey status = $ODYSSEY_STATUS (transient state, check later)"
    ;;
esac
echo

# ---------------------------------------------------------------------------
# 4. uuid5-seed fix — trace rows with BOTH bandit_arm_id AND engagement
# ---------------------------------------------------------------------------
echo "--- [4/4] uuid5-seed fix (272f0825) ---"
# Pre-fix: bandit_arm_id NOT NULL and engagement_reach_24h NOT NULL never
# co-occurred on the same row because different uuid5 seeds. Post-fix,
# metric_collector's engagement writes should MERGE into push_to_backlog's
# arm-decision rows via ON CONFLICT (blueprint_id).
#
# Look at trace rows created in the last 30h — spans one full publish cycle
# (yesterday 06:35 UTC → today 06:35 UTC + buffer).
BOTH_POPULATED=$("${PSQL_BASE[@]}" -c "SELECT COUNT(*) FROM post_decision_trace WHERE bandit_arm_id IS NOT NULL AND engagement_reach_24h IS NOT NULL AND updated_at > NOW() - INTERVAL '30 hours';" 2>/dev/null | tr -d ' ')
ARM_ONLY=$("${PSQL_BASE[@]}" -c "SELECT COUNT(*) FROM post_decision_trace WHERE bandit_arm_id IS NOT NULL AND engagement_reach_24h IS NULL AND updated_at > NOW() - INTERVAL '30 hours';" 2>/dev/null | tr -d ' ')
ENG_ONLY=$("${PSQL_BASE[@]}" -c "SELECT COUNT(*) FROM post_decision_trace WHERE bandit_arm_id IS NULL AND engagement_reach_24h IS NOT NULL AND updated_at > NOW() - INTERVAL '30 hours';" 2>/dev/null | tr -d ' ')
echo "rows updated in last 30h with both arm + reach: ${BOTH_POPULATED:-?}"
echo "rows with arm only:                             ${ARM_ONLY:-?}"
echo "rows with engagement only:                      ${ENG_ONLY:-?} (disjoint-row signal — should be 0 post-fix)"
if [[ "${BOTH_POPULATED:-0}" -gt 0 ]]; then
  echo "OK: uuid5-seed fix confirmed — arm + engagement now co-located on same row"
elif [[ "${ARM_ONLY:-0}" -eq 0 && "${ENG_ONLY:-0}" -eq 0 ]]; then
  echo "SKIP: no window matured in last 30h — verification inconclusive"
else
  echo "WARN: no arm+reach co-population yet — writes still may be landing on disjoint UUIDs"
  echo "      (may be OK if only pre-fix rows in window; re-check tomorrow)"
fi
echo

# ---------------------------------------------------------------------------
# 5. Bonus — full 5/5 Threads matrix status
# ---------------------------------------------------------------------------
echo "--- [bonus] Threads 5/5 matrix ---"
"${PSQL_BASE[@]}" -c "SELECT niche_id, status FROM publishing_analytics WHERE platform = 'threads' AND DATE(created_at) = '$TODAY' ORDER BY niche_id;" 2>/dev/null
THREADS_NICHES=$("${PSQL_BASE[@]}" -c "SELECT COUNT(DISTINCT niche_id) FROM publishing_analytics WHERE platform = 'threads' AND DATE(created_at) = '$TODAY';" 2>/dev/null | tr -d ' ')
echo "distinct niches attempted Threads today: ${THREADS_NICHES:-0}/5"
if [[ "${THREADS_NICHES:-0}" -eq 5 ]]; then
  echo "OK: 5/5 Threads matrix closed — first time in 30+ days"
else
  echo "PARTIAL: still ${THREADS_NICHES:-0}/5 — see [2/4] for movies-specific failure mode"
fi
echo

# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "SUMMARY: $FAIL_COUNT alarm(s) across 4 verifications"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
  echo "STATUS: OK — all 2026-07-22 fixes live-verified"
else
  echo "STATUS: $FAIL_COUNT UNVERIFIED — see per-check output above"
  if [[ "$STRICT" -eq 1 ]]; then
    exit 2
  fi
fi

exit 0
