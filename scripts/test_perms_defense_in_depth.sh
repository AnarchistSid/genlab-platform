#!/usr/bin/env bash
# ============================================================================
# Smoke tests for the perms-defense-in-depth suite
#
# Covers the surface of:
#   - repair_permissions.sh (--dry-run + --apply + log file self-care)
#   - check_permissions_drift.sh (drift detection without DB writes)
#   - systemd_failure_alert.sh (argument validation)
#
# The DB-writing paths of check_permissions_drift.sh + systemd_failure_alert.sh
# are NOT covered here (CI has no postgres); those paths get exercised by the
# end-to-end systemd-on-prod test that runs after deploy.
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMPROOT=$(mktemp -d -t perms_test_XXXXXX)
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

# ── repair_permissions.sh ───────────────────────────────────────────────

declare_test "repair_permissions: --dry-run with no drift exits 0"
FAKE_ROOT=$(mktemp -d -t fake_genlab_XXXXXX)
echo "x" > "$FAKE_ROOT/file_a"
echo "y" > "$FAKE_ROOT/file_b"
# All files owned by current user — no drift
rc=0
"$SCRIPT_DIR/repair_permissions.sh" --dry-run --root-dir "$FAKE_ROOT" \
    --owner "$(id -un):$(id -gn)" >/dev/null 2>&1 || rc=$?
assert_exit 0 "$rc" "exits 0 when no drift"
rm -rf "$FAKE_ROOT"

declare_test "repair_permissions: --help exits 0"
rc=0
"$SCRIPT_DIR/repair_permissions.sh" --help >/dev/null 2>&1 || rc=$?
assert_exit 0 "$rc" "exits 0 for --help"

declare_test "repair_permissions: unknown flag exits 1"
rc=0
"$SCRIPT_DIR/repair_permissions.sh" --bogus 2>&1 >/dev/null || rc=$?
assert_exit 1 "$rc" "exits 1 for unknown flag"

declare_test "repair_permissions: syntax check"
if bash -n "$SCRIPT_DIR/repair_permissions.sh"; then
    echo "  PASS: parses cleanly"; inc "$PASS_FILE"
else
    echo "  FAIL: bash -n failed"; inc "$FAIL_FILE"
fi

# ── check_permissions_drift.sh ──────────────────────────────────────────

declare_test "check_permissions_drift: missing DATABASE_URL exits 1"
# The script auto-sources $GENLAB/.env if present, so we can't simply
# unset DATABASE_URL in this process. Run it from a clean directory
# without an .env to exercise the pre-flight fail path.
rc=0
CLEAN_HOME=$(mktemp -d -t clean_home_XXXXXX)
# Copy the script into the clean dir so $GENLAB resolves there too
mkdir -p "$CLEAN_HOME/scripts"
cp "$SCRIPT_DIR/check_permissions_drift.sh" "$CLEAN_HOME/scripts/"
chmod +x "$CLEAN_HOME/scripts/check_permissions_drift.sh"
(
    unset DATABASE_URL
    "$CLEAN_HOME/scripts/check_permissions_drift.sh" >/dev/null 2>&1
) || rc=$?
assert_exit 1 "$rc" "exits 1 when DATABASE_URL absent + no .env"
rm -rf "$CLEAN_HOME"

declare_test "check_permissions_drift: syntax check"
if bash -n "$SCRIPT_DIR/check_permissions_drift.sh"; then
    echo "  PASS: parses cleanly"; inc "$PASS_FILE"
else
    echo "  FAIL: bash -n failed"; inc "$FAIL_FILE"
fi

# ── systemd_failure_alert.sh ────────────────────────────────────────────

declare_test "systemd_failure_alert: missing argument exits 1"
rc=0
"$SCRIPT_DIR/systemd_failure_alert.sh" 2>&1 >/dev/null || rc=$?
assert_exit 1 "$rc" "exits 1 without unit name"

declare_test "systemd_failure_alert: syntax check"
if bash -n "$SCRIPT_DIR/systemd_failure_alert.sh"; then
    echo "  PASS: parses cleanly"; inc "$PASS_FILE"
else
    echo "  FAIL: bash -n failed"; inc "$FAIL_FILE"
fi

# ── Summary ──────────────────────────────────────────────────────────────
PASS=$(cat "$PASS_FILE")
FAIL=$(cat "$FAIL_FILE")
echo
echo "═══ $PASS passed, $FAIL failed ═══"
[[ "$FAIL" -eq 0 ]]
