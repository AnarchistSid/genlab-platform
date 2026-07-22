#!/usr/bin/env bash
# Verify the whisper_sync canary produced captioned renders in today's BB fire.
#
# Context: 2026-07-22 flipped `whisper_sync.enabled: true` on BlackboxBrief
# only (ai_creators canary) after 40 days disabled. The RenderWhisperCaptions
# stage should now:
#   1. Have real timing (transcription takes 15-90s per clip)
#   2. Produce ``<stem>_captioned.mp4`` files in visuals/
# If either signal is missing, whisper_sync silently no-op'd — either the
# flag didn't propagate through niche_loader OR Whisper transcription failed
# and the stage swallowed the error.
#
# Exit 0 always (rule #26) — this is a data-side signal, operator reads
# stdout / dashboard. Add --strict to exit 2 if wiring into pipeline_alerts.
set -uo pipefail

STRICT=0
if [[ "${1:-}" == "--strict" ]]; then
  STRICT=1
fi

RUNS_DIR="${GENLAB_RUNS_DIR:-/opt/genlab/.tmp/runs}"

echo "=== whisper_sync canary verification ($(date -u +'%Y-%m-%d %H:%M UTC')) ==="

# 1. Find today's most-recent BB run.
today_stamp="$(date -u +'%Y%m%d')"
latest_run="$(ls -td "$RUNS_DIR"/ai_creators_${today_stamp}_* 2>/dev/null | head -1)"
if [[ -z "$latest_run" ]]; then
  echo "SKIP: no ai_creators run for ${today_stamp} yet — pipeline hasn't fired"
  exit 0
fi
echo "run: $latest_run"

report="$latest_run/run_report.json"
if [[ ! -f "$report" ]]; then
  echo "SKIP: run_report.json missing in $latest_run — pipeline still running or crashed"
  exit 0
fi
echo

# 2. Blueprints count — if 0, nothing to caption, canary inconclusive.
blueprints=$(python3 -c "import json; print(json.load(open('$report'))['metrics'].get('blueprints_count', 0))" 2>/dev/null)
echo "--- pipeline production ---"
echo "blueprints produced: $blueprints"
if [[ "${blueprints:-0}" -eq 0 ]]; then
  echo "SKIP: 0 blueprints — no work for RenderWhisperCaptions to do (upstream failure)"
  exit 0
fi
echo

# 3. RenderWhisperCaptions timing — the definitive skip-vs-active signal.
#    Pre-flip: ~0.0005 sec (no-op). Post-flip: >= 15 sec per clip (transcription).
whisper_time=$(python3 -c "import json; print(json.load(open('$report'))['stage_timings'].get('RenderWhisperCaptions', 0))" 2>/dev/null)
echo "--- RenderWhisperCaptions stage ---"
echo "stage timing: ${whisper_time}s"
if awk "BEGIN {exit !($whisper_time < 5)}"; then
  echo "WARN: timing < 5s suggests whisper_sync stage no-op'd or short-circuited"
  echo "      expected 15-90s per clip for real Whisper transcription"
  if [[ "$STRICT" -eq 1 ]]; then exit 2; fi
fi
echo

# 4. Look for _captioned.mp4 artifacts — the whisper stage's output shape.
echo "--- _captioned.mp4 artifacts ---"
captioned_count=$(find "$latest_run/visuals" -name "*_captioned.mp4" 2>/dev/null | wc -l)
echo "captioned files: $captioned_count"
if [[ "${captioned_count:-0}" -eq 0 ]]; then
  echo "ALARM: 0 _captioned.mp4 files produced despite ${blueprints} blueprints"
  echo "       whisper_sync flag flipped but RenderWhisperCaptions produced nothing"
  echo "       investigate: journalctl -u genlab-pipeline-ai-creators.service --since '2h ago' | grep -i 'whisper\\|caption'"
  if [[ "$STRICT" -eq 1 ]]; then exit 2; fi
else
  first_captioned=$(find "$latest_run/visuals" -name "*_captioned.mp4" 2>/dev/null | head -1)
  echo "sample: $first_captioned"
  # File size sanity — <100KB likely means empty/broken output.
  if [[ -f "$first_captioned" ]]; then
    bytes=$(stat -c %s "$first_captioned" 2>/dev/null || echo 0)
    echo "size: ${bytes} bytes"
    if [[ "$bytes" -lt 102400 ]]; then
      echo "WARN: file size < 100KB — likely empty/broken caption render"
      if [[ "$STRICT" -eq 1 ]]; then exit 2; fi
    fi
  fi
fi
echo

# 5. Video validation summary — did the captioned output pass VMAF/bt709 gates?
echo "--- video validation ---"
python3 -c "
import json
r = json.load(open('$report'))
vv = r['metrics'].get('video_validation', {})
if not vv:
    print('no video_validation section (upstream produced 0 videos)')
else:
    print(f\"passed={vv.get('passed', 0)} failed={vv.get('failed', 0)} fixed={vv.get('fixed', 0)}\")
" 2>&1
echo

# 6. Overall verdict.
if [[ "${captioned_count:-0}" -gt 0 ]] && awk "BEGIN {exit !($whisper_time >= 5)}"; then
  echo "OK: whisper_sync canary produced $captioned_count captioned file(s) in ${whisper_time}s"
else
  echo "REGRESSION: whisper_sync canary silently skipped work — investigate"
fi

exit 0
