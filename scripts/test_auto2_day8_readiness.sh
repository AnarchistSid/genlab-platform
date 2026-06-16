#!/usr/bin/env bash
# ============================================================================
# Smoke tests for auto2_day8_readiness.sh — parse + flag validation only.
#
# The real script hits a live DB + SSH'es to prod; we can't reproduce that
# in CI. These tests cover the argument parsing surface + the failure-
# path output format so a refactor doesn't silently change the
# operator-visible report shape.
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$SCRIPT_DIR/auto2_day8_readiness.sh"
TMPROOT=$(mktemp -d -t day8_test_XXXXXX)
PASS_FILE="$TMPROOT/.pass"
FAIL_FILE="$TMPROOT/.fail"
echo 0 > "$PASS_FILE"
echo 0 > "$FAIL_FILE"
trap 'rm -rf "$TMPROOT"' EXIT

inc() {
    local f="$1" cur
    cur=$(cat "$f")
    echo $((cur + 1)) > "$f"
}

declare_test() {
    echo
    echo "── TEST: $* ──"
}

assert_exit() {
    local expected="$1" actual="$2" msg="$3"
    if [[ "$expected" == "$actual" ]]; then
        echo "  PASS: $msg"
        inc "$PASS_FILE"
    else
        echo "  FAIL: $msg (expected exit $expected, got $actual)"
        inc "$FAIL_FILE"
    fi
}

assert_stdout_contains() {
    local needle="$1" stdout="$2" msg="$3"
    if grep -q "$needle" <<<"$stdout"; then
        echo "  PASS: $msg"
        inc "$PASS_FILE"
    else
        echo "  FAIL: $msg (needle: $needle)"
        inc "$FAIL_FILE"
    fi
}

# ── 1. Missing --niche fails with exit 1 ──────────────────────────
declare_test "missing --niche → exit 1 with clear error"
rc=0
out=$("$TARGET" 2>&1) || rc=$?
assert_exit 1 "$rc" "exits 1"
assert_stdout_contains "niche is required" "$out" "error mentions --niche"

# ── 2. Unknown flag fails ─────────────────────────────────────────
declare_test "unknown flag → exit 1"
rc=0
out=$("$TARGET" --bogus-flag value 2>&1) || rc=$?
assert_exit 1 "$rc" "exits 1"

# ── 3. --help prints usage ────────────────────────────────────────
declare_test "--help prints usage"
rc=0
out=$("$TARGET" --help 2>&1) || rc=$?
assert_exit 0 "$rc" "exits 0"
assert_stdout_contains "niche" "$out" "usage mentions --niche"

# ── 4. Syntax (catches refactor regressions) ──────────────────────
declare_test "bash -n parses"
if bash -n "$TARGET"; then
    echo "  PASS: parses cleanly"
    inc "$PASS_FILE"
else
    echo "  FAIL: bash -n found syntax error"
    inc "$FAIL_FILE"
fi

# ── 5. Script is executable ───────────────────────────────────────
declare_test "executable bit set"
if [[ -x "$TARGET" ]]; then
    echo "  PASS: executable"
    inc "$PASS_FILE"
else
    echo "  FAIL: not executable"
    inc "$FAIL_FILE"
fi

PASS=$(cat "$PASS_FILE")
FAIL=$(cat "$FAIL_FILE")
echo
echo "═══ $PASS passed, $FAIL failed ═══"
[[ "$FAIL" -eq 0 ]]
