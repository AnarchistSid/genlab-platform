"""Pins for ``scripts/nightly_schedule_top_per_niche.py`` +
``genlab-nightly-schedule.{service,timer}`` (Path B autonomous
publishing safety net, 2026-07-06).

If these pins regress:

* Removing the LLM-refusal hook filter would let Claude refusal text
  ("I need to stop here...") get auto-scheduled — happened to 2 of 5
  top anime blueprints on 2026-07-05, caught by manual review.
* Dropping ``niches_needing_scheduling`` idempotency check would
  double-schedule when auto-approver already handled a niche.
* Removing the target-slot computation would break the "tomorrow's
  publisher fire" timing contract.
* Removing OnFailure hook silences the empty-queue alert.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "nightly_schedule_top_per_niche.py"
SERVICE = REPO_ROOT / "deploy" / "systemd-phase2" / "genlab-nightly-schedule.service"
TIMER = REPO_ROOT / "deploy" / "systemd-phase2" / "genlab-nightly-schedule.timer"


@pytest.fixture(scope="module")
def script_module():
    spec = importlib.util.spec_from_file_location("nightly_schedule_top_per_niche", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ── niche inventory pin ──────────────────────────────────────────────


def test_all_5_niches_in_scope(script_module):
    """Every activated niche must be a candidate for scheduling.
    Dropping one = that niche silently gets 0 publishes on days the
    auto-approver misses it."""
    assert set(script_module.NICHES) == {
        "ai_creators",
        "gaming",
        "sports",
        "movies",
        "anime",
    }


# ── target-slot computation pin ─────────────────────────────────────


def test_compute_target_slot_is_tomorrow_06_utc(script_module):
    """Publisher fires 06:35 UTC — we schedule for 06:00 UTC to give a
    35-minute buffer so publisher sees it as already-past-due."""
    fixed_now = datetime(2026, 7, 5, 18, 0, 0, tzinfo=UTC)
    slot = script_module.compute_target_slot(fixed_now)
    assert slot == datetime(2026, 7, 6, 6, 0, 0, tzinfo=UTC)


def test_compute_target_slot_uses_utc_not_local(script_module):
    """The slot must be UTC — matches Postgres's UTC default so
    ``scheduled_for::date`` comparisons in downstream queries line up.
    """
    slot = script_module.compute_target_slot()
    assert slot.tzinfo == UTC
    assert slot.hour == 6 and slot.minute == 0 and slot.second == 0


# ── SQL filter pin — the LLM-refusal guard ──────────────────────────


def test_sql_filters_llm_refusal_hooks(script_module):
    """This filter caught 2 of 5 top anime blueprints on 2026-07-05.
    Regression here would silently ship Claude refusal text as reel hooks.
    """
    source = SCRIPT_PATH.read_text()
    for pattern in [
        "I need to stop",
        "I cannot",
        "I can''t",
        "I am unable",
        "I''m sorry",
        "I apologize",
    ]:
        assert pattern in source, (
            f"LLM-refusal filter missing pattern {pattern!r} — "
            "would let refusal text get auto-scheduled as reel hook."
        )


def test_sql_filters_hook_length(script_module):
    """Hooks shorter than 15 chars are usually degenerate; longer than
    100 chars violate CLAUDE.md's ≤60 char rule + Instagram caption
    guidance. Between is the safe zone."""
    source = SCRIPT_PATH.read_text()
    assert "length(hook) BETWEEN 15 AND 100" in source


# ── publisher-visibility pin ────────────────────────────────────────


def test_schedule_blueprints_does_not_touch_status(script_module):
    """LIVE-FIRE BUG 2026-07-06: setting ``status='SCHEDULED'`` made the
    blueprints INVISIBLE to publisher (which queries VISUAL_READY only).
    Publisher published 1 gaming reel out of 5 that Monday. The nightly
    script must NEVER emit a ``status =`` assignment in the UPDATE."""
    import inspect
    import re

    body = inspect.getsource(script_module.schedule_blueprints)

    # Strip docstrings + comments before checking — status may appear
    # in explanation text without regressing behavior.
    # Simple approach: check for the specific SQL SET column pattern
    # "status =" or "status=" (SQL assignment shape), not the word
    # "status" alone.
    sql_status_set = re.compile(r"\bstatus\s*=\s*['\"]", re.IGNORECASE)
    matches = sql_status_set.findall(body)
    # Filter out any that appear inside a docstring or comment — the
    # inspect.getsource output preserves those. Cheap check: if the
    # match is on a line that starts with # or is between triple-quotes,
    # it's not a real SQL assignment. Since we can't easily parse quotes
    # here, use a stricter constraint: only fail if the match is
    # inside an SQL string literal ending with ", " AND followed by
    # another SQL assignment.
    # Simplest: assert no line in the function body outside of
    # docstrings contains "status = '"
    lines = body.splitlines()
    in_docstring = False
    live_matches = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            in_docstring = not in_docstring
            continue
        if in_docstring:
            continue
        # Skip Python-level comments
        code_part = line.split("#", 1)[0]
        if sql_status_set.search(code_part):
            live_matches.append(line.strip())
    assert not live_matches, (
        "schedule_blueprints must not set status column — publisher "
        "queries status='VISUAL_READY' only. Setting to SCHEDULED "
        "makes blueprint invisible to publisher (Mon 2026-07-06 bug). "
        f"Offending line(s): {live_matches}"
    )


def test_schedule_blueprints_sets_action_taken_approved(script_module):
    """The other half of the auto-approver contract: publisher's
    approval_gate checks action_taken == 'approved'. Missing this
    means blueprint fails the approval gate and never publishes."""
    import inspect

    body = inspect.getsource(script_module.schedule_blueprints)
    assert "action_taken = 'approved'" in body, (
        "schedule_blueprints must set action_taken='approved' — "
        "publisher's approval_gate rejects anything else."
    )


# ── idempotency pin ─────────────────────────────────────────────────


def test_idempotency_via_niches_needing_scheduling(script_module):
    """Presence of this function is what makes the script safe to
    re-run and safe to compose with the auto-approver. Removing it
    would let two systems fight for the same slot."""
    assert hasattr(script_module, "niches_needing_scheduling")
    # And it must return a set (not a list) so set arithmetic works
    # in main().
    import inspect

    src = inspect.getsource(script_module.niches_needing_scheduling)
    assert "return set(" in src or "- already" in src


def test_idempotency_covers_visual_ready_plus_action_taken(script_module):
    """2026-07-08 LIVE-FIRE BUG: task #528 (2026-07-06) changed the
    write side of ``schedule_blueprints`` to LEAVE status='VISUAL_READY'
    untouched so publisher's ``blueprint_selector.select_blueprint``
    would still see the row. But the read side (this idempotency
    query) still only looked at ``status IN ('SCHEDULED',
    'PUBLISHED')`` — invisible to fresh nightly-cron picks. Result:
    on 2026-07-07 22:00 IST the cron scheduled a stale 11-day-old
    Getafe-Barcelona blueprint on top of Arsenal-Newcastle, which had
    been scheduled earlier that day with the new shape. Operator had
    to demote by hand.

    The pin asserts the WHERE clause explicitly names BOTH shapes:
    * Legacy: ``status IN ('SCHEDULED', 'PUBLISHED')``
    * Current: ``status = 'VISUAL_READY'`` + ``action_taken = 'approved'``

    If a future refactor drops either branch, this test fires
    immediately rather than after a live-fire dupe."""
    import inspect

    src = inspect.getsource(script_module.niches_needing_scheduling)

    # Legacy branch — historical rows with SCHEDULED/PUBLISHED status.
    assert "'SCHEDULED'" in src and "'PUBLISHED'" in src, (
        "niches_needing_scheduling must still catch the legacy "
        "status='SCHEDULED'/'PUBLISHED' rows — otherwise re-running "
        "the cron on a day with historical scheduled posts would "
        "duplicate them."
    )

    # Current branch — VISUAL_READY + action_taken='approved' is what
    # both this script's schedule_blueprints AND the auto-approver
    # actually write.
    assert "'VISUAL_READY'" in src, (
        "niches_needing_scheduling must include status='VISUAL_READY' "
        "in its idempotency check — task #528 (2026-07-06) changed "
        "the write side to leave status='VISUAL_READY' untouched. "
        "Missing this catches nothing scheduled by the current path."
    )
    assert "'approved'" in src, (
        "niches_needing_scheduling must scope the VISUAL_READY case "
        "to action_taken='approved' — otherwise it would catch every "
        "VISUAL_READY blueprint with a scheduled_for date even if "
        "the operator explicitly rejected it."
    )
    assert "action_taken" in src, (
        "niches_needing_scheduling must reference the action_taken "
        "column — the 'approved' literal alone isn't enough proof "
        "the column is checked (could be in a comment)."
    )


# ── systemd unit pins ───────────────────────────────────────────────


def test_service_file_present():
    assert SERVICE.is_file()


def test_timer_file_present():
    assert TIMER.is_file()


def test_service_has_on_failure_hook():
    """Exit code 1 (empty VISUAL_READY queue) fires OnFailure →
    Mission Control alert. Without the hook, an empty-queue day
    passes silently and publisher publishes nothing tomorrow."""
    content = SERVICE.read_text()
    assert "OnFailure=genlab-service-failure-alert@" in content


def test_service_uses_file_path_invocation():
    """scripts/ is not a Python package — must invoke by file path.
    Sibling gotcha [[systemd-scripts-invocation-gotcha]]."""
    content = SERVICE.read_text()
    assert "scripts/nightly_schedule_top_per_niche.py" in content
    assert "-m scripts." not in content


def test_service_sources_env_file():
    """DATABASE_URL lives in /opt/genlab/.env — must be sourced."""
    content = SERVICE.read_text()
    assert "EnvironmentFile=/opt/genlab/.env" in content


def test_timer_fires_at_1630_utc():
    """22:00 IST = 16:30 UTC. Timing is deliberate — after last pipeline
    (sports 10:00 UTC), well before next publisher (06:35 UTC next day)."""
    content = TIMER.read_text()
    assert "OnCalendar=*-*-* 16:30:00 UTC" in content


def test_timer_persistent_true():
    """Downtime shouldn't drop a day's scheduling. Persistent catch-up."""
    content = TIMER.read_text()
    assert "Persistent=true" in content


# ── Path A companion pin — auto-approver enforce mode ────────────────


def test_auto_approver_no_longer_dry_run():
    """Path A — enforce mode enabled by removing --dry-run. Per-niche
    safety maintained by publishing.yaml's auto_publish.enabled +
    rollout_pct. If someone re-adds --dry-run, they've silently
    disabled all approval work for ai_creators."""
    approver = REPO_ROOT / "deploy" / "systemd-phase2" / "genlab-auto-approver.service"
    exec_start_lines = [
        line for line in approver.read_text().splitlines() if line.strip().startswith("ExecStart=")
    ]
    assert exec_start_lines, "auto-approver service missing ExecStart"
    assert not any("--dry-run" in line for line in exec_start_lines), (
        "auto-approver.service ExecStart still has --dry-run — Path A "
        "regression. If enforce is genuinely broken, revert via env var "
        "GENLAB_AUTO_APPROVE_DISABLED=1 instead."
    )
