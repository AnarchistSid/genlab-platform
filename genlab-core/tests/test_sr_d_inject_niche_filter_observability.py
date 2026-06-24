"""SR-D (PR #520, 2026-06-24): observability + opt-in strict mode for
``_inject_niche_filter``.

Background: SYSTEM-RESEARCH §9 SR-D flagged that ``BacklogClient.find_*``
methods silently return cross-tenant rows when callers forget the
``niche_id=`` kwarg. The filter helper ``_inject_niche_filter()``
fails-open on ``niche_id=None`` — returning the unfiltered formula.

This PR ships the 3-step migration mechanism:

  1. WARNING log on every None call (operators can grep call sites)
  2. Counter ``_SR_D_NONE_CALL_COUNT`` (measure frequency in prod)
  3. Opt-in strict mode via ``GENLAB_REQUIRE_NICHE_FILTER=1`` env var
     (when set, raises ValueError instead of returning unfiltered)

These tests pin all three modes.

If the env-var flip pattern regresses, the call-site audit that was
supposed to follow becomes invisible — the operator can't tell which
sites still rely on the fallthrough, and the prod-flip can't safely
happen without an outage.
"""

from __future__ import annotations

import logging

import pytest
from genlab_core.http.backlog_client import (
    _inject_niche_filter,
    _sr_d_none_call_count,
    _sr_d_reset_counter_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_counter_and_env(monkeypatch):
    """Reset counter + clear env between tests so order is irrelevant."""
    _sr_d_reset_counter_for_tests()
    monkeypatch.delenv("GENLAB_REQUIRE_NICHE_FILTER", raising=False)
    yield


# ── happy path: niche_id provided ───────────────────────────────────


def test_with_niche_id_no_warning_no_counter_bump(caplog):
    """Normal case: caller passes niche_id. No warning, no counter."""
    with caplog.at_level(logging.WARNING):
        result = _inject_niche_filter(None, "gaming")
    assert result == "{niche_id}='gaming'"
    assert _sr_d_none_call_count() == 0
    assert not any("SR-D" in r.message for r in caplog.records)


def test_with_niche_id_and_existing_formula_wraps_in_and():
    """Existing formula wrapped in AND() — pre-PR-#520 behaviour
    preserved when niche_id is provided."""
    result = _inject_niche_filter("{status}='INTAKE'", "gaming")
    assert result == "AND({status}='INTAKE', {niche_id}='gaming')"


# ── default mode (None falls through with warning + counter) ───────


def test_none_niche_id_emits_warning_and_increments_counter(caplog):
    """Pre-#520 silent fallthrough is now a loud warning."""
    with caplog.at_level(logging.WARNING):
        result = _inject_niche_filter("{status}='X'", None)
    # Legacy behaviour preserved: returns the unfiltered formula
    assert result == "{status}='X'"
    # Counter incremented
    assert _sr_d_none_call_count() == 1
    # Warning logged
    sr_d_logs = [r for r in caplog.records if "SR-D" in r.message]
    assert len(sr_d_logs) == 1
    assert "called without niche_id" in sr_d_logs[0].message
    assert "cross-tenant leak risk" in sr_d_logs[0].message


def test_empty_string_niche_id_treated_as_none(caplog):
    """Empty string is the same trap as None — caller forgot kwarg or
    passed a defaulted-empty value. Both must fire the warning."""
    with caplog.at_level(logging.WARNING):
        _inject_niche_filter(None, "")
    assert _sr_d_none_call_count() == 1
    assert any("SR-D" in r.message for r in caplog.records)


def test_counter_accumulates_across_multiple_calls(caplog):
    """The counter is process-local + monotonic. Operators tail it
    to see migration progress (counter should drop toward 0 as
    call sites get fixed)."""
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            _inject_niche_filter(None, None)
    assert _sr_d_none_call_count() == 5


# ── strict mode (env var set → raises) ────────────────────────────


def test_strict_mode_raises_on_none(monkeypatch):
    """When ``GENLAB_REQUIRE_NICHE_FILTER=1``, None is no longer
    fallthrough — it raises a clear ValueError that names the bug
    class + the env var + the audit reference."""
    monkeypatch.setenv("GENLAB_REQUIRE_NICHE_FILTER", "1")
    with pytest.raises(ValueError, match="SR-D"):
        _inject_niche_filter(None, None)


@pytest.mark.parametrize("env_value", ["1", "true", "TRUE", "yes", "Yes"])
def test_strict_mode_accepts_multiple_truthy_env_values(monkeypatch, env_value):
    """Operators may set the flag in any common truthy form."""
    monkeypatch.setenv("GENLAB_REQUIRE_NICHE_FILTER", env_value)
    with pytest.raises(ValueError):
        _inject_niche_filter(None, None)


@pytest.mark.parametrize("env_value", ["0", "false", "no", "", "  "])
def test_strict_mode_off_for_falsy_env_values(monkeypatch, env_value, caplog):
    """Falsy / empty values keep legacy behaviour (warning only)."""
    monkeypatch.setenv("GENLAB_REQUIRE_NICHE_FILTER", env_value)
    with caplog.at_level(logging.WARNING):
        result = _inject_niche_filter("{status}='X'", None)
    assert result == "{status}='X'"  # no raise
    # Warning still fires (helpful for measuring even when not enforced)
    assert any("SR-D" in r.message for r in caplog.records)


def test_strict_mode_still_works_for_real_niche_id(monkeypatch):
    """Strict mode only raises on None — real values still pass."""
    monkeypatch.setenv("GENLAB_REQUIRE_NICHE_FILTER", "1")
    result = _inject_niche_filter(None, "gaming")
    assert result == "{niche_id}='gaming'"


# ── source-level pin ───────────────────────────────────────────────


def test_source_pins_sr_d_mechanism():
    """Pin the 3-step migration mechanism (warning + counter + env)
    so a refactor that drops one re-introduces the silent fall-through.
    """
    from pathlib import Path

    import genlab_core.http.backlog_client as mod

    src = Path(mod.__file__).read_text()
    # Counter exists
    assert "_SR_D_NONE_CALL_COUNT" in src
    # WARNING log shape exists
    assert "_inject_niche_filter called without niche_id" in src
    assert "[SR-D]" in src
    # Env var name pinned (typos would silently disable the flag)
    assert "GENLAB_REQUIRE_NICHE_FILTER" in src
    # ValueError raise path exists
    assert "Cross-tenant leak" in src
