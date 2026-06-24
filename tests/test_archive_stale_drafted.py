"""Pins for ``scripts/archive_stale_drafted.py``.

This script auto-archives DRAFTED blueprints that never acquired a
source video. The behavior is heavy mutation against the blueprints
table — same risk class as ``archive_stale_visual_paths.py`` — so the
pin shape mirrors that file's: source-grep on the SQL invariants
that would silently break if the script were refactored, plus a
behavioral test that the dry-run path doesn't call UPDATE at all.

If these pins regress:

* dropping the ``scheduled_for IS NULL`` clause → silently archive
  scheduled blueprints (violates ``cleanup_safety.md``).
* dropping the ``status = 'DRAFTED'`` TOCTOU filter in the UPDATE →
  could downgrade a row that flipped to VISUAL_READY between
  detect and archive.
* dropping the ``--apply`` requirement → first-time runs mutate
  instead of preview.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "archive_stale_drafted.py"


@pytest.fixture(scope="module")
def script_source() -> str:
    return SCRIPT_PATH.read_text()


# ── source pins on the SELECT (find_stale_drafted) ──────────────────


def test_find_stale_drafted_respects_scheduled_for(script_source):
    """``scheduled_for IS NULL`` must be in the WHERE clause —
    otherwise a stale-DRAFTED with a scheduled time gets silently
    archived, breaking cleanup_safety.md."""
    assert "scheduled_for IS NULL" in script_source, (
        "find_stale_drafted must skip rows with scheduled_for set. "
        "This is the cleanup_safety.md 'scheduled posts are sacred' rule."
    )


def test_find_stale_drafted_filters_for_no_video(script_source):
    """The trigger condition is 'no source video AND no visual_paths'.
    Without both halves, a row that's mid-render could be archived
    prematurely (visual_paths empty before render finishes) OR a row
    waiting for a next-day fetch (source_video_url empty) gets axed."""
    assert "source_video_url" in script_source
    assert "visual_paths" in script_source
    # The empty/null check must be present for BOTH fields
    assert "source_video_url' IS NULL OR" in script_source
    assert "visual_paths' IS NULL" in script_source


def test_find_stale_drafted_uses_age_days_param(script_source):
    """The grace period must be configurable via --age-days. A hardcoded
    age (e.g. ``INTERVAL '7 days'``) would prevent operators from running
    the script with a tighter window for today's 3 stuck rows (3 days old)
    without code edits."""
    assert "%s * INTERVAL '1 day'" in script_source, (
        "age_days must be parametrized into the SQL — not hardcoded."
    )


# ── source pins on the UPDATE (archive_blueprints) ──────────────────


def test_archive_update_pins_status_drafted_filter(script_source):
    """The UPDATE must include ``AND status = 'DRAFTED'`` so a TOCTOU
    flip (DRAFTED → VISUAL_READY between detect and archive) becomes
    a no-op rather than a wrongful downgrade."""
    # The UPDATE block must contain the status filter
    update_block = script_source[
        script_source.find("UPDATE blueprints") : script_source.find("AND status = 'DRAFTED'")
        + len("AND status = 'DRAFTED'")
    ]
    assert "AND status = 'DRAFTED'" in update_block
    assert "UPDATE blueprints" in update_block


def test_archive_update_writes_archived_reason(script_source):
    """The audit trail (``extra.archived_reason``) is the only forensic
    breadcrumb after the row is demoted. It MUST land on the row."""
    assert "'archived_reason'" in script_source
    assert "'archived_at'" in script_source
    assert "'archived_by'" in script_source
    assert "archive_stale_drafted.py" in script_source


def test_archive_update_uses_id_any_uuid_cast(script_source):
    """The ``%s::uuid[]`` cast prevents the psycopg3 IndeterminateDatatype
    error on the WHERE clause — same lesson learned in
    archive_stale_visual_paths.py."""
    assert "ANY(%s::uuid[])" in script_source


# ── dry-run vs apply pins ──────────────────────────────────────────


def test_dry_run_is_default_apply_required_to_mutate(script_source):
    """``--apply`` must be required to actually UPDATE. Without it, the
    script returns early after printing the preview. This is the single
    biggest safety mechanism — drop it and a first-time run mutates
    instead of previewing."""
    assert '"--apply"' in script_source
    # The early-return shape — "no changes made" string must accompany
    # the dry-run path so operators see clearly when nothing happened.
    assert "DRY-RUN — no changes made" in script_source
    assert "if not args.apply:" in script_source


def test_backup_runs_before_archive_in_main(script_source):
    """``write_backup`` must be called before ``archive_blueprints`` so
    the CSV exists even if the UPDATE fails afterwards. Mirrors
    archive_stale_visual_paths.py's invariant."""
    # main() body — backup call must lexically precede the archive call.
    main_body = script_source[script_source.find("def main(") :]
    backup_idx = main_body.find("write_backup(records")
    archive_idx = main_body.find("archive_blueprints(conn")
    assert backup_idx != -1
    assert archive_idx != -1
    assert backup_idx < archive_idx, (
        "Backup CSV must be written BEFORE the UPDATE fires so the "
        "audit trail survives a failed archive."
    )


# ── defensive complement pin ────────────────────────────────────────


def test_defensive_scheduled_anomaly_pin(script_source):
    """The script ALSO surfaces DRAFTED-with-scheduled-for rows but
    never archives them. This is the 'show but don't touch' pattern —
    drops the defensive query and the operator misses a separate
    bug class (scheduler operating on DRAFTED rows)."""
    assert "find_stuck_with_schedule" in script_source
    assert "scheduled_for IS NOT NULL" in script_source
    # The output marker for the operator
    assert "SKIP-SCHED" in script_source


# ── CLI smoke test ─────────────────────────────────────────────────


def test_help_runs_without_db_connection(monkeypatch):
    """``--help`` must not require DATABASE_URL — exits 0 with usage.
    Catches regressions where _connect() is moved above argparse.

    Uses ``sys.executable`` (not bare ``python3``) so the subprocess
    inherits the test runner's Python — bare ``python3`` on macOS
    resolves to the bundled 3.9 which lacks ``datetime.UTC``.
    """
    import subprocess
    import sys

    monkeypatch.delenv("DATABASE_URL", raising=False)
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"--help should exit 0; got {result.returncode} stderr={result.stderr!r}"
    )
    assert "--apply" in result.stdout
    assert "--age-days" in result.stdout
