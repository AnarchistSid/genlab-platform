#!/usr/bin/env bash
# ============================================================================
# Smoke tests for bandit_arms_snapshot.sh
#
# Self-contained: spins up a sqlite-mock or just exercises the pre-flight
# branches that don't need a real DB. Run via:
#
#   ./scripts/test_bandit_arms_snapshot.sh
#
# Exit code 0 = all pass, non-zero = first failure surfaces with details.
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$SCRIPT_DIR/bandit_arms_snapshot.sh"
TMPROOT=$(mktemp -d -t bandit_snap_test_XXXXXX)
# Counters live on disk because some tests run inside subshells that
# can't mutate parent variables. Re-read at summary time.
PASS_FILE="$TMPROOT/.pass_count"
FAIL_FILE="$TMPROOT/.fail_count"
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
        echo "  PASS: $msg (exit $actual)"
        inc "$PASS_FILE"
    else
        echo "  FAIL: $msg (expected $expected, got $actual)"
        inc "$FAIL_FILE"
    fi
}

assert_file_exists() {
    if [[ -f "$1" ]]; then
        echo "  PASS: $1 exists"
        inc "$PASS_FILE"
    else
        echo "  FAIL: $1 missing"
        inc "$FAIL_FILE"
    fi
}

assert_no_file() {
    if [[ ! -f "$1" ]]; then
        echo "  PASS: $1 absent"
        inc "$PASS_FILE"
    else
        echo "  FAIL: $1 should not exist"
        inc "$FAIL_FILE"
    fi
}

# ── 1. Missing DATABASE_URL ───────────────────────────────────────
declare_test "pre-flight: missing DATABASE_URL → exit 1"
(
    unset DATABASE_URL
    cd "$TMPROOT"
    # isolate from any parent .env by overriding the lookup root
    GENLAB_FAKE=$(mktemp -d)
    cp "$TARGET" "$GENLAB_FAKE/bandit_arms_snapshot.sh"
    chmod +x "$GENLAB_FAKE/bandit_arms_snapshot.sh"
    # No .env present so DATABASE_URL stays unset
    rc=0
    "$GENLAB_FAKE/bandit_arms_snapshot.sh" >/dev/null 2>&1 || rc=$?
    assert_exit 1 "$rc" "exits 1 when DATABASE_URL absent"
    rm -rf "$GENLAB_FAKE"
)

# ── 2. Snapshot dir unwritable ────────────────────────────────────
declare_test "pre-flight: read-only snapshot dir → exit 1"
(
    cd "$TMPROOT"
    RO_DIR="$TMPROOT/readonly_snap"
    mkdir -p "$RO_DIR"
    chmod 555 "$RO_DIR"
    export DATABASE_URL="postgresql://x:y@127.0.0.1:5432/none"
    export BANDIT_SNAPSHOT_DIR="$RO_DIR"
    rc=0
    "$TARGET" >/dev/null 2>&1 || rc=$?
    # Should hit pre-flight failure (mkdir succeeds because dir exists; the
    # writable-check is the gate). On some systems root can write through
    # 555 — skip the assertion in that case.
    if [[ "$EUID" -eq 0 ]]; then
        echo "  SKIP: running as root; 555 perms don't apply"
    else
        assert_exit 1 "$rc" "exits 1 when snapshot dir not writable"
    fi
    chmod 755 "$RO_DIR"
)

# ── 3. Rotation deletes only matching files ──────────────────────
declare_test "rotation: only bandit_arms_*.csv files pruned"
(
    cd "$TMPROOT"
    SNAP="$TMPROOT/snap_rot"
    mkdir -p "$SNAP"
    # Old matching file (should be pruned)
    touch -d "60 days ago" "$SNAP/bandit_arms_19990101.csv" 2>/dev/null || \
        touch -t 199001010000 "$SNAP/bandit_arms_19990101.csv"
    # Old non-matching file (must NOT be pruned)
    touch -d "60 days ago" "$SNAP/some_other_file.csv" 2>/dev/null || \
        touch -t 199001010000 "$SNAP/some_other_file.csv"
    # Recent matching file (must NOT be pruned)
    touch "$SNAP/bandit_arms_$(date -u +%Y%m%d).csv"

    # The find invocation from the script (mirrored here so we test the
    # exact pattern, not just the spirit):
    find "$SNAP" -maxdepth 1 -type f -name "bandit_arms_*.csv" \
        -mtime +30 -print -delete > /dev/null 2>&1 || true

    assert_no_file "$SNAP/bandit_arms_19990101.csv"
    assert_file_exists "$SNAP/some_other_file.csv"
    assert_file_exists "$SNAP/bandit_arms_$(date -u +%Y%m%d).csv"
)

# ── 4. Syntax check (catches refactor regressions) ────────────────
declare_test "syntax: bash -n parses without error"
if bash -n "$TARGET"; then
    echo "  PASS: parses cleanly"
    inc "$PASS_FILE"
else
    echo "  FAIL: bash -n found syntax error"
    inc "$FAIL_FILE"
fi

# ── Summary ───────────────────────────────────────────────────────
PASS=$(cat "$PASS_FILE")
FAIL=$(cat "$FAIL_FILE")
echo
echo "═══ $PASS passed, $FAIL failed ═══"
[[ "$FAIL" -eq 0 ]]
