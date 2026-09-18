#!/usr/bin/env bash
# baseline_compare.sh — compare a test suite across two refs, soundly.
#
# WHY THIS EXISTS
# ---------------
# "Do the existing tests still pass?" is unanswerable on a suite that already
# fails. The deployed tree carries ~201 pre-existing failures, so a raw count
# tells you nothing; you need the count from before, and the comparison is only
# meaningful if BOTH runs collected the same tests in the same environment.
#
# Five approaches were tried and measured before this one worked. They are
# documented in baseline_compare.md so nobody retries them. The short version:
# a git worktree reconstructs the REPO, not the MACHINE, and a venv synced in
# one worktree imports THAT worktree's code no matter which tree you run pytest
# from. Both failure modes produce numbers that look like results.
#
# USAGE
#   baseline_compare.sh <base-ref> <head-ref> [--parent DIR] [--collect-only]
#
# EXIT: 0 verdict PASS (set b empty) · 1 verdict FAIL · 2 unsound/aborted
set -uo pipefail

# T-56: derive the source repo from where this script lives, so the harness runs
# on any host. GENLAB_SRC still overrides. The old default was a hardcoded
# /opt/genlab, which aborted on the Mac with a message about a missing worktree
# rather than a missing repo.
_self="${BASH_SOURCE[0]}"
while [[ -L "$_self" ]]; do _self="$(readlink "$_self")"; done
SRC="${GENLAB_SRC:-$(cd "$(dirname "$_self")/.." && pwd -P)}"
PARENT="${SRC}"
COLLECT_ONLY=0
BASE=""; HEAD_REF=""
# T-56: a fixed /tmp/bc_*.log is world-writable; a stale root-owned file from a
# previous run silently poisons the next one. One mktemp dir per run instead.
RUNDIR="$(mktemp -d "${TMPDIR:-/tmp}/bc.XXXXXX")"
LOG_B="$RUNDIR/base.log"; LOG_H="$RUNDIR/head.log"
DESELECT_FILE="${SRC}/scripts/baseline_compare.deselect"

# Untracked state the suite's skipif conditions gate on. A worktree has none of
# it, which silently changes COLLECTION -- the defect that made four attempts
# unsound. Enumerated, not guessed: each entry was found by a skip-reason diff.
UNTRACKED_DIRS=(genlab-core/models .genlab .runtime .belt .inferencesh)
GITIGNORED_FILES=(genlab-core/config/affiliate_catalog.yaml BlackboxBrief/config/persona.yaml)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --parent) PARENT="$2"; shift 2 ;;
    --collect-only) COLLECT_ONLY=1; shift ;;
    *) if [[ -z "$BASE" ]]; then BASE="$1"; elif [[ -z "$HEAD_REF" ]]; then HEAD_REF="$1"; fi; shift ;;
  esac
done
[[ -n "$BASE" && -n "$HEAD_REF" ]] || { echo "usage: $0 <base-ref> <head-ref> [--parent DIR] [--collect-only]"; exit 2; }

WT_B="${PARENT}/.bc-base"; WT_H="${PARENT}/.bc-head"
cleanup() {
  # KILL THE DETACHED CHILDREN FIRST. `run_one` launches pytest under
  # setsid/nohup so it survives a hung driver -- which also means killing the
  # driver leaves it running. Measured 2026-09-18: a pytest orphaned from a
  # killed run was still alive nearly five hours later, and when the next
  # comparison reached its head side the two shared .bc-head and DEADLOCKED at
  # 59% with both processes at 0% CPU. The harness's own run_one comment says
  # "sequential only -- never concurrent"; nothing was enforcing it across runs.
  for _pidf in "$LOG_B.pid" "$LOG_H.pid"; do
    [[ -f "$_pidf" ]] && kill -9 "$(cat "$_pidf")" 2>/dev/null
  done
  pkill -9 -f "$WT_B/.venv/bin/python -m pytest" 2>/dev/null
  pkill -9 -f "$WT_H/.venv/bin/python -m pytest" 2>/dev/null
  git -C "$SRC" worktree remove --force "$WT_B" 2>/dev/null
  git -C "$SRC" worktree remove --force "$WT_H" 2>/dev/null
  rm -rf "$WT_B" "$WT_H" 2>/dev/null
}
trap cleanup EXIT

say() { printf '%s\n' "$*"; }
die() { say "ABORT: $*"; exit 2; }

# ---------------------------------------------------------------- worktrees
say "=== worktrees ==="
# A stale pytest from a previous run poisons this one: same worktree, same
# .tmp, and the deadlock looks like a slow suite rather than a collision.
for _wt in "$WT_B" "$WT_H"; do
  if pgrep -f "$_wt/.venv/bin/python -m pytest" >/dev/null 2>&1; then
    say "  stale pytest still running on $(basename "$_wt") — killing it before we start"
    pkill -9 -f "$_wt/.venv/bin/python -m pytest" 2>/dev/null
    sleep 2
  fi
done
cleanup
git -C "$SRC" worktree add -q --detach "$WT_B" "$BASE" || die "cannot create base worktree at $BASE"
git -C "$SRC" worktree add -q --detach "$WT_H" "$HEAD_REF" || die "cannot create head worktree at $HEAD_REF"
say "  base $(git -C "$WT_B" rev-parse --short HEAD)   head $(git -C "$WT_H" rev-parse --short HEAD)"

for WT in "$WT_B" "$WT_H"; do
  ( cd "$WT" && UV_PROJECT_ENVIRONMENT="$WT/.venv" uv sync --frozen >/dev/null 2>&1 ) \
    || die "uv sync --frozen failed in $WT"
done

# ------------------------------------------------- THE CHECK THAT MATTERS
# A venv synced in worktree X imports X's code even when pytest runs elsewhere.
# Four earlier attempts tested one tree's FILES against the other tree's CODE
# and produced plausible, wrong numbers. Assert isolation before anything else.
say "=== import isolation (the check four attempts lacked) ==="
for WT in "$WT_B" "$WT_H"; do
  got=$( cd "$WT/genlab-core" && "$WT/.venv/bin/python" -c 'import genlab_core;print(genlab_core.__file__)' 2>/dev/null )
  say "  $(basename "$WT") -> ${got:-<import failed>}"
  # T-56: compare RESOLVED paths. Python reports the realpath, so on macOS a
  # --parent under /tmp yields /private/tmp/... and a raw prefix match FALSE-ABORTS
  # on a correctly isolated venv.
  _wt_real=$( cd "$WT" && pwd -P )
  _got_real=$( [[ -n "$got" ]] && cd "$(dirname "$got")" && pwd -P )/$( basename "${got:-x}" )
  [[ -n "$got" && "$_got_real" == "$_wt_real"/* ]] \
    || die "$(basename "$WT") venv imports outside its worktree: ${got:-<import failed>} (resolved: ${_got_real:-n/a} vs $_wt_real)"
done

# ------------------------------------------------------------------ mirror
say "=== mirror prod state into both ==="
DIRTY=$( cd "$SRC" && git status --porcelain | awk '/^ ?M/{print $2}' )   # enumerated, never hardcoded
for WT in "$WT_B" "$WT_H"; do
  for f in $DIRTY; do mkdir -p "$WT/$(dirname "$f")"; cp -f "$SRC/$f" "$WT/$f"; done
  for d in "${UNTRACKED_DIRS[@]}"; do
    [[ -d "$SRC/$d" ]] && { mkdir -p "$WT/$d"; cp -an "$SRC/$d/." "$WT/$d/" 2>/dev/null; }
  done
  for f in "${GITIGNORED_FILES[@]}"; do
    [[ -f "$SRC/$f" ]] && { mkdir -p "$WT/$(dirname "$f")"; cp -f "$SRC/$f" "$WT/$f"; }
  done
  rm -rf "$WT/.tmp"; mkdir -p "$WT/.tmp"      # own .tmp; a shared mount deadlocks concurrent runs
done
for f in $DIRTY "${GITIGNORED_FILES[@]}"; do
  [[ -f "$SRC/$f" ]] || continue
  a=$(sha256sum "$SRC/$f" | cut -c1-16); b=$(sha256sum "$WT_B/$f" 2>/dev/null | cut -c1-16); c=$(sha256sum "$WT_H/$f" 2>/dev/null | cut -c1-16)
  [[ "$a" == "$b" && "$a" == "$c" ]] || die "mirror mismatch on $f"
done
say "  $(echo "$DIRTY" | grep -c .) dirty + ${#UNTRACKED_DIRS[@]} dirs + ${#GITIGNORED_FILES[@]} gitignored — sha256 verified"

# ------------------------------------------------------------------- runs
DES=()
[[ -f "$DESELECT_FILE" ]] && while read -r l; do [[ -n "$l" && "$l" != \#* ]] && DES+=(--ignore="$l"); done < "$DESELECT_FILE"

# T-21 guard. The deselect file was seeded by grepping for literal /opt/genlab
# paths plus write verbs (B.12). That scan cannot see a test which writes to
# PRODUCTION through a configured DSN -- no path literal, no write verb. One
# such test drove RunReport().execute() for real and planted a $10.00 fixture
# row in pipeline_run_costs on every suite run: 32x the real daily spend, and
# invisible because $10.32 reads as a busy day rather than an error.
# Warn on any test that imports a production storage/persist entry point
# without a mock in the same file. Advisory, not fatal -- a false positive here
# must not block a comparison.
say "=== prod-storage write scan (T-21) ==="
_unmocked=0
while read -r tf; do
  [[ -z "$tf" ]] && continue
  grep -qE "monkeypatch|unittest\.mock|mocker|patch\(" "$WT_H/genlab-core/$tf" 2>/dev/null && continue
  say "  WARN unmocked prod-storage import: $tf"; _unmocked=$((_unmocked+1))
done < <(cd "$WT_H/genlab-core" 2>/dev/null && grep -rlE "persist_run_cost|PostgresBackend\(|cost_persist" tests/ 2>/dev/null)
say "  $_unmocked file(s) import prod storage with no mock in-file"
# T-61: this warning was advisory, printed on every run, and read past TWICE
# while 11,208 tests ran against prod on the VPS. One of those runs wrote a
# run_id='test_run' row at $10.00 into production pipeline_run_costs -- 34x a
# real day's spend -- under niche_id='gaming', corrupting that niche's figure
# until it was deleted by primary key. An advisory warning on a destructive
# vector is not a control. It aborts now.
#
# The suite-wide block lives in genlab-core/tests/conftest.py
# (_block_prod_cost_telemetry_writes). This check verifies that block is present
# in the HEAD worktree; it is not a substitute for it.
if ! grep -q "_block_prod_cost_telemetry_writes" "$WT_H/genlab-core/tests/conftest.py" 2>/dev/null; then
  if [[ "${BC_ALLOW_PROD_STORAGE:-0}" == "1" ]]; then
    say "  !! OVERRIDE: BC_ALLOW_PROD_STORAGE=1 — proceeding without the suite-wide write block"
  else
    die "head worktree has no _block_prod_cost_telemetry_writes fixture in tests/conftest.py — the suite can write to PROD telemetry (T-61). Set BC_ALLOW_PROD_STORAGE=1 to override deliberately."
  fi
fi
# NETWORK-BOUND TESTS ARE DESELECTED, not endured. At --timeout=300 each test
# that blocks on a socket or a thread waiting for one costs five minutes; the
# 2026-09-17 comparison reached 46% of the BASE suite in three hours with both
# pytest processes at 0.0% CPU. The count is printed on each side below, so a
# marker drifting onto (or off) a test is visible rather than silent.
# BOTH terms, because a command-line -m REPLACES the one in addopts. genlab-core's
# pyproject carries `addopts = "-m 'not integration' ..."`, so passing just
# "not network" here would silently re-admit the whole integration suite —
# a filter that widens what it is meant to narrow.
MARKEXPR="${BC_MARKEXPR:-not integration and not network}"
# PER-SIDE TIMEOUTS. A HANG IS A FAILURE AT ANY TIMEOUT, so the side carrying
# the hangs can be bounded without changing set (a) membership — only the wall
# clock moves. The 2026-09-17 base ref predates the fail-fast pool fix and hangs
# 108 times; at 300 s that is ~3.8 hours of waiting to learn what 15 s tells you.
# Head keeps the full 300 s so a genuine slow test there is not misread as a hang.
BASE_TIMEOUT="${BC_BASE_TIMEOUT:-300}"
HEAD_TIMEOUT="${BC_HEAD_TIMEOUT:-300}"
_pyargs() {   # $1 = timeout seconds
  if [[ "$COLLECT_ONLY" == 1 ]]; then
    PYARGS=(-q -p no:cacheprovider --collect-only -m "$MARKEXPR" "${DES[@]}")
  else
    PYARGS=(-q -p no:cacheprovider --timeout="$1" -m "$MARKEXPR" "${DES[@]}")
  fi
}
_pyargs "$BASE_TIMEOUT"
say "=== marker filter: -m '$MARKEXPR' ==="
for WT in "$WT_B" "$WT_H"; do
  _all=$( cd "$WT/genlab-core" && "$WT/.venv/bin/python" -m pytest -q -p no:cacheprovider \
            --collect-only "${DES[@]}" 2>/dev/null | grep -cE '::' )
  _kept=$( cd "$WT/genlab-core" && "$WT/.venv/bin/python" -m pytest -q -p no:cacheprovider \
            --collect-only -m "$MARKEXPR" "${DES[@]}" 2>/dev/null | grep -cE '::' )
  say "  $(basename "$WT"): collected ${_all}, kept ${_kept}, deselected by marker $(( _all - _kept ))"
done

# T-56: setsid is Linux-only. On macOS its absence made `run_one` fail before
# pytest ever started, and every downstream count read 0 from an empty log --
# which the harness then reported as PASS (T-55). Use it when present, plain
# nohup when not.
SETSID=""; command -v setsid >/dev/null 2>&1 && SETSID="setsid"

run_one() {  # $1=worktree $2=logfile ; sequential only — never concurrent
  # T-61, second instance. The conftest write-block only exists at HEAD, and the
  # BASE worktree is an OLD commit whose tests predate it — so the base run wrote
  # a run_id='test_run' $10.00 row into PRODUCTION pipeline_run_costs at
  # 10:57:28Z on 2026-09-12, while the abort added earlier checked only $WT_H.
  # Guarding the head side alone is guarding the wrong side: the harness runs
  # BOTH suites, and the older one has fewer protections by construction.
  #
  # So the block is environmental, not code-level: strip DATABASE_URL from the
  # pytest environment. persist_run_cost already returns False without it, and
  # tests that genuinely need a DSN set their own fake via monkeypatch. This
  # holds for any base ref, however old, which a conftest check never can.
  ( cd "$1/genlab-core" && env -u DATABASE_URL $SETSID nohup "$1/.venv/bin/python" -m pytest "${PYARGS[@]}" > "$2" 2>&1 < /dev/null & echo $! > "$2.pid" )
  local p; p=$(cat "$2.pid")
  while kill -0 "$p" 2>/dev/null; do sleep 10; done   # PID-captured; a pgrep -f pattern matches the waiter itself
  # T-55: prove pytest actually ran. A log with no summary line means the
  # launcher failed, and every count derived from it is a false zero.
  if ! grep -qE '[0-9]+ (passed|failed|error)' "$2"; then
    say "  !! no pytest summary in $(basename "$2") — first line: $(head -1 "$2")"
    VERDICT_INVALID=1
  fi
  # A collection abort runs NOTHING. pytest still prints a summary ("18 errors"),
  # and errors+skips sum to a non-zero "collected" -- so counting them lets an
  # interrupted run masquerade as a real one. Measured on the Mac 2026-09-12:
  # "1 skipped, 46 deselected, 18 errors" summed to 19 and passed the first
  # version of this very guard.
  if grep -qE '^!+ Interrupted:' "$2"; then
    say "  !! collection INTERRUPTED in $(basename "$2") — $(grep -oE 'Interrupted:.*' "$2" | head -1)"
    VERDICT_INVALID=1
  fi
}
say "=== runs (sequential) ==="
VERDICT_INVALID=0
_t0=$SECONDS
_pyargs "$BASE_TIMEOUT"
run_one "$WT_B" "$LOG_B"
_base_secs=$((SECONDS - _t0))
say "  base: $(grep -cE '^(FAILED|ERROR) ' "$LOG_B") failing ids  (timeout ${BASE_TIMEOUT}s, ${_base_secs}s wall)"
_t1=$SECONDS
_pyargs "$HEAD_TIMEOUT"
run_one "$WT_H" "$LOG_H"
_head_secs=$((SECONDS - _t1))
say "  head: $(grep -cE '^(FAILED|ERROR) ' "$LOG_H") failing ids  (timeout ${HEAD_TIMEOUT}s, ${_head_secs}s wall)"

if [[ "$COLLECT_ONLY" == 1 ]]; then
  cb=$(grep -cE '::' "$LOG_B"); ch=$(grep -cE '::' "$LOG_H")
  added=$(git -C "$SRC" diff --name-only --diff-filter=A "$BASE" "$HEAD_REF" -- 'genlab-core/tests/*' | wc -l)
  say "=== collect-only soundness ==="
  say "  collected: base=$cb head=$ch delta=$((ch-cb))   head adds $added new test file(s)"
  say "  skipped delta: n/a in collect-only"
  [[ $((ch-cb)) -ge 0 ]] && { say "VERDICT: collection reproducible"; exit 0; } || exit 2
fi

# -------------------------------------------------------------- soundness
tot() { local f=$1 s=0 n; for k in passed failed skipped errors; do
  n=$(grep -oE "[0-9]+ $k" "$f" | tail -1 | grep -oE '[0-9]+'); s=$((s+${n:-0})); done; echo $s; }
# T-55: tests that actually EXECUTED. Skips were collected but not run; errors
# are collection failures. Only passed+failed proves the suite ran.
ran() { local f=$1 s=0 n; for k in passed failed; do
  n=$(grep -oE "[0-9]+ $k" "$f" | tail -1 | grep -oE '[0-9]+'); s=$((s+${n:-0})); done; echo $s; }
sk()  { grep -oE '[0-9]+ skipped' "$1" | tail -1 | grep -oE '[0-9]+'; }
CB=$(tot "$LOG_B"); CH=$(tot "$LOG_H")
RB=$(ran "$LOG_B"); RH=$(ran "$LOG_H")
SB=$(sk "$LOG_B"); SH=$(sk "$LOG_H")
ADDED_TESTS=$(( CH - CB ))
say "=== soundness ==="
say "  collected: base=$CB head=$CH delta=$ADDED_TESTS"
# T-55 SELF-CONTROL. The harness exists to catch false greens; on 2026-09-12 it
# produced one, printing PASS after `setsid: command not found` meant pytest never
# started. Zero executed tests is INVALID, never PASS -- an empty set trivially
# satisfies "set (b) is empty".
say "  executed:  base=$RB head=$RH   (passed+failed; skips and collection errors excluded)"
if [[ "${VERDICT_INVALID:-0}" == 1 || "${RB:-0}" -eq 0 || "${RH:-0}" -eq 0 ]]; then
  say ""
  say "VERDICT: INVALID — no tests executed (base ran=$RB head ran=$RH; collected=$CB/$CH). This is NOT a pass."
  say "  base log: $LOG_B"
  say "  head log: $LOG_H"
  say "  first line of base log: $(head -1 "$LOG_B" 2>/dev/null)"
  exit 2
fi
say "  skipped:   base=$SB head=$SH delta=$(( ${SH:-0} - ${SB:-0} ))"
[[ "${SB:-0}" == "${SH:-0}" ]] || die "skipped delta $(( ${SH:-0} - ${SB:-0} )) != 0 — a collection dependency is unmirrored; diff -rs skip reasons and add it to GITIGNORED_FILES/UNTRACKED_DIRS"
[[ $ADDED_TESTS -ge 0 ]] || die "head collected FEWER tests than base ($ADDED_TESTS) — refusing to compute sets"

# ------------------------------------------------------------------- sets
ids() { grep -E '^(FAILED|ERROR) ' "$1" | awk '{print $2}' | grep '::' | sort -u; }  # '::' filter: ERROR-level LOG lines start at col 0 and are not node ids
ids "$LOG_B" > "$RUNDIR/b.ids"; ids "$LOG_H" > "$RUNDIR/h.ids"
A=$(comm -12 "$RUNDIR/b.ids" "$RUNDIR/h.ids" | wc -l)
comm -13 "$RUNDIR/b.ids" "$RUNDIR/h.ids" > "$RUNDIR/setb.ids"
C=$(comm -23 "$RUNDIR/b.ids" "$RUNDIR/h.ids" | wc -l)
B=$(wc -l < "$RUNDIR/setb.ids")
say "=== sets ==="
say "  (a) fail in both: $A   (b) head-only: $B   (c) base-only: $C"

# inversion guard: a test that fails "only with head" while living in a file
# head CREATED is proof the comparison is backwards, not proof of a regression.
NEWFILES=$(git -C "$SRC" diff --name-only --diff-filter=A "$BASE" "$HEAD_REF" -- 'genlab-core/tests/*' | sed 's|genlab-core/||')
if [[ -n "$NEWFILES" ]]; then
  while read -r nf; do
    if grep -q "$nf" "$RUNDIR/setb.ids" 2>/dev/null; then
      say "  INVERSION GUARD TRIPPED — set (b) contains tests from a file head created:"; grep "$nf" "$RUNDIR/setb.ids" | sed 's/^/    /'
      die "comparison is inverted (head's own tests cannot fail at base — they do not exist there)"
    fi
  done <<< "$NEWFILES"
fi

# ------------------------------------------------------- T-60 stability check
# The set difference attributes EVERY in-suite failure to the diff. That is only
# valid for order-independent tests. Measured 2026-09-12: a VPS run reported
# "FAIL — 1 regression" where the flagged test passed 6/6 STANDALONE at head, and
# a set (c) test failed 6/6 standalone while passing in-suite. Neither was
# reachable from the diff, and the Mac run over the identical range had set (b)=0.
# One order-dependent test manufactures a regression, so isolate before judging.
comm -23 "$RUNDIR/b.ids" "$RUNDIR/h.ids" > "$RUNDIR/setc.ids"
ISO_RUNS="${BC_ISOLATION_RUNS:-3}"

isolate() {   # $1=worktree $2=node-id ; echo "pass" if it passes every isolated run
  local wt=$1 nid=$2 i rc
  for ((i=0; i<ISO_RUNS; i++)); do
    ( cd "$wt/genlab-core" && "$wt/.venv/bin/python" -m pytest -q -p no:cacheprovider \
        --timeout=300 "$nid" >/dev/null 2>&1 )
    rc=$?
    [[ $rc -ne 0 ]] && { echo "fail"; return; }
  done
  echo "pass"
}

REAL_REGRESSIONS="$RUNDIR/real_regressions.ids"; : > "$REAL_REGRESSIONS"
UNSTABLE="$RUNDIR/unstable.ids"; : > "$UNSTABLE"

if [[ "$B" -gt 0 ]]; then
  say "=== T-60 isolation: re-running set (b) standalone at head, n=$ISO_RUNS ==="
  while read -r nid; do
    [[ -z "$nid" ]] && continue
    if [[ "$(isolate "$WT_H" "$nid")" == "pass" ]]; then
      say "  UNSTABLE  $nid  (passes standalone, failed in-suite)"
      echo "$nid" >> "$UNSTABLE"
    else
      say "  REGRESSION $nid  (fails standalone at head too)"
      echo "$nid" >> "$REAL_REGRESSIONS"
    fi
  done < "$RUNDIR/setb.ids"
fi

if [[ "$C" -gt 0 ]]; then
  say "=== T-60 isolation: re-running set (c) standalone at head, n=$ISO_RUNS ==="
  while read -r nid; do
    [[ -z "$nid" ]] && continue
    # set (c) claims "failed at base, passes at head". If it FAILS standalone at
    # head, the in-suite pass was order-luck, not a recovery.
    if [[ "$(isolate "$WT_H" "$nid")" == "fail" ]]; then
      say "  UNSTABLE  $nid  (fails standalone at head, passed in-suite — not a recovery)"
      echo "$nid" >> "$UNSTABLE"
    fi
  done < "$RUNDIR/setc.ids"
fi

NU=$(wc -l < "$UNSTABLE" | tr -d ' '); NR=$(wc -l < "$REAL_REGRESSIONS" | tr -d ' ')
if [[ "$NU" -gt 0 ]]; then
  say ""; say "  $NU UNSTABLE (order-dependent, excluded from the verdict):"
  sed 's/^/    /' "$UNSTABLE"
fi

if [[ "$NR" -eq 0 ]]; then
  say ""
  if [[ "$NU" -gt 0 ]]; then say "VERDICT: PASS with $NU UNSTABLE — no test went passing -> failing"
  else say "VERDICT: PASS — no test went passing -> failing"; fi
  exit 0
fi
say ""; say "VERDICT: FAIL — $NR regression(s) confirmed standalone:"; sed 's/^/  /' "$REAL_REGRESSIONS"; exit 1
