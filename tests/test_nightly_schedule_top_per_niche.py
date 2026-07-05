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
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "nightly_schedule_top_per_niche.py"
SERVICE = REPO_ROOT / "deploy" / "systemd-phase2" / "genlab-nightly-schedule.service"
TIMER = REPO_ROOT / "deploy" / "systemd-phase2" / "genlab-nightly-schedule.timer"


@pytest.fixture(scope="module")
def script_module():
    spec = importlib.util.spec_from_file_location(
        "nightly_schedule_top_per_niche", SCRIPT_PATH
    )
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
        "ai_creators", "gaming", "sports", "movies", "anime",
    }


# ── target-slot computation pin ─────────────────────────────────────


def test_compute_target_slot_is_tomorrow_06_utc(script_module):
    """Publisher fires 06:35 UTC — we schedule for 06:00 UTC to give a
    35-minute buffer so publisher sees it as already-past-due."""
    fixed_now = datetime(2026, 7, 5, 18, 0, 0, tzinfo=timezone.utc)
    slot = script_module.compute_target_slot(fixed_now)
    assert slot == datetime(2026, 7, 6, 6, 0, 0, tzinfo=timezone.utc)


def test_compute_target_slot_uses_utc_not_local(script_module):
    """The slot must be UTC — matches Postgres's UTC default so
    ``scheduled_for::date`` comparisons in downstream queries line up.
    """
    slot = script_module.compute_target_slot()
    assert slot.tzinfo == timezone.utc
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
    approver = (
        REPO_ROOT / "deploy" / "systemd-phase2" / "genlab-auto-approver.service"
    )
    exec_start_lines = [
        line for line in approver.read_text().splitlines()
        if line.strip().startswith("ExecStart=")
    ]
    assert exec_start_lines, "auto-approver service missing ExecStart"
    assert not any("--dry-run" in line for line in exec_start_lines), (
        "auto-approver.service ExecStart still has --dry-run — Path A "
        "regression. If enforce is genuinely broken, revert via env var "
        "GENLAB_AUTO_APPROVE_DISABLED=1 instead."
    )
