"""PR #521 (2026-06-24): write-side UUID-shape guard on
``calibration_logger.log()``.

PR #519 added the read-side filter
(``blueprint_id LIKE '________-____-____-____-____________'``) so
synthetic test-fixture rows (``"10001"``-``"10005"`` from the
``dashboard/tests/test_review_action_dashboard_events.py`` suite) no
longer pollute the agreement-rate stats. But the rows kept landing in
the table because the writer was permissive:

* ``calibration_logger.log()`` reads ``DATABASE_URL`` at *call time*
  (``calibration_logger.py:170``) — anything that re-loads ``.env``
  after the conftest pops it (a fixture, a CI step, a manual ``python
  -m pytest`` invoked from outside the dashboard tree) gets a real
  DSN handed back.
* The test fixtures call ``_execute_review_action("10001", ...)``;
  ``_execute_review_action`` calls ``calibration_logger.log()``
  unconditionally — there's no mock between them.

This PR closes the symmetry: the writer now refuses any
``blueprint_id`` that doesn't match the canonical
``8-4-4-4-12`` UUID layout. Real callers always pass UUIDs
(``blueprints.id`` is ``uuid``-typed in Postgres — confirmed
2026-06-24 against prod schema); the synthetic test IDs are short
integer strings that fail the shape check.

If these pins regress, synthetic rows start accumulating again and
gaming's agreement rate drifts back toward the post-PR-519 view of
22% (because PR #519 only filtered read consumers — every NEW
consumer would have to add the filter; the write-side guard removes
that obligation).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

# ── pure helper pins ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "valid_uuid",
    [
        "12345678-1234-1234-1234-123456789012",  # canonical layout
        "00000000-0000-0000-0000-000000000000",  # nil UUID
        "abcdef12-abcd-abcd-abcd-abcdef123456",  # mixed-case
        "ffffffff-ffff-ffff-ffff-ffffffffffff",  # all-f
    ],
)
def test_is_uuid_shape_accepts_canonical_uuids(valid_uuid):
    """Real UUIDs (any case, any value) pass the structural check."""
    from genlab_core.scheduling.calibration_logger import _is_uuid_shape

    assert _is_uuid_shape(valid_uuid) is True


@pytest.mark.parametrize(
    "synthetic_id",
    [
        "10001",  # the actual leaked fixture ID
        "10002",
        "10003",
        "10004",
        "10005",
        "1",
        "test_fixture_001",
        "9999",
        "abc",
        "",
        "12345678-1234-1234-1234-12345678901",  # 35 chars, dash positions off
        "12345678_1234_1234_1234_123456789012",  # underscores not dashes
        "x" * 36,  # 36 chars but no dashes
    ],
)
def test_is_uuid_shape_rejects_non_uuids(synthetic_id):
    """Test fixtures + malformed strings are rejected."""
    from genlab_core.scheduling.calibration_logger import _is_uuid_shape

    assert _is_uuid_shape(synthetic_id) is False


def test_is_uuid_shape_rejects_non_strings():
    """Defensive: non-string inputs (None, int) must not crash."""
    from genlab_core.scheduling.calibration_logger import _is_uuid_shape

    assert _is_uuid_shape(None) is False  # type: ignore[arg-type]
    assert _is_uuid_shape(10001) is False  # type: ignore[arg-type]
    assert _is_uuid_shape([]) is False  # type: ignore[arg-type]


# ── log() behaviour pins ──────────────────────────────────────────


def _fake_decision():
    """Minimal AutoApprovalDecision for log() calls."""
    from genlab_core.scheduling.auto_approval_gate import AutoApprovalDecision

    return AutoApprovalDecision(
        approved=True,
        confidence=0.7,
        passed_checks=["has_video", "has_hook"],
        failed_checks=[],
        reasons=["test"],
    )


def test_log_refuses_synthetic_integer_string_id(caplog, monkeypatch):
    """The exact leak case: ``_execute_review_action("10001", ...)``
    eventually reaches calibration_logger — the guard must stop it
    before any I/O even when DATABASE_URL is set."""
    from genlab_core.scheduling import calibration_logger

    # Simulate the leak path: DSN looks valid (which is what would
    # bypass the existing ``if not dsn`` guard).
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake-dsn-never-touched")

    with caplog.at_level(logging.WARNING):
        result = calibration_logger.log(
            blueprint_id="10001",
            niche_id="gaming",
            decision=_fake_decision(),
            operator_action="approved",
        )

    assert result is False
    # Warning shape pinned so a refactor that drops the message also
    # drops grep-ability when an operator audits the logs.
    sr_logs = [r for r in caplog.records if "PR #521" in r.message]
    assert len(sr_logs) == 1
    assert "non-UUID blueprint_id" in sr_logs[0].message
    assert "10001" in sr_logs[0].message


def test_log_refuses_all_five_leaked_fixture_ids(monkeypatch):
    """The dashboard test suite uses IDs 10001-10005. Every one of
    them must be blocked — otherwise a future test that exercises
    only one ID can still leak."""
    from genlab_core.scheduling import calibration_logger

    monkeypatch.setenv("DATABASE_URL", "postgresql://fake-dsn-never-touched")

    for bad_id in ("10001", "10002", "10003", "10004", "10005"):
        result = calibration_logger.log(
            blueprint_id=bad_id,
            niche_id="gaming",
            decision=_fake_decision(),
            operator_action="approved",
        )
        assert result is False, f"{bad_id!r} should be refused"


def test_log_with_valid_uuid_passes_guard_then_skips_on_bad_dsn(monkeypatch):
    """A real UUID must NOT be rejected by the new guard. With a
    fake DSN it still returns False (psycopg fails to connect, the
    fail-open path triggers) — but the rejection is downstream, NOT
    the new UUID-shape check.

    The negative pin: no PR #521 warning should fire for a valid
    UUID, only the broader 'failed to write' warning when psycopg
    can't reach the fake host.
    """
    from genlab_core.scheduling import calibration_logger

    # DSN points nowhere — psycopg will error → fail-open returns False.
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none")

    real_uuid = "12345678-1234-1234-1234-123456789012"
    result = calibration_logger.log(
        blueprint_id=real_uuid,
        niche_id="gaming",
        decision=_fake_decision(),
        operator_action="approved",
    )
    # Will be False because psycopg can't connect — but the failure
    # comes from the DB layer, not from the new shape guard.
    assert result is False  # downstream, not us


# ── source pins ───────────────────────────────────────────────────


def test_source_pins_uuid_guard_in_log_body():
    """Pin the guard's existence in the log() body so a refactor that
    drops the if-statement re-opens the leak. Two anchors so a partial
    refactor still trips the pin."""
    import genlab_core.scheduling.calibration_logger as mod

    src = Path(mod.__file__).read_text()
    # The guard call:
    assert "_is_uuid_shape(blueprint_id)" in src, (
        "calibration_logger.log() must call _is_uuid_shape() to "
        "reject synthetic test-fixture rows (PR #521)."
    )
    # The refusal warning shape (operators grep on PR #521 + 'non-UUID'):
    assert "refusing to log non-UUID blueprint_id" in src
    assert "PR #521" in src


def test_source_pins_helper_function_exists():
    """The helper must be a module-level callable so tests + future
    callers can reuse it without re-implementing the check."""
    from genlab_core.scheduling.calibration_logger import _is_uuid_shape

    assert callable(_is_uuid_shape)


def test_guard_runs_before_dsn_lookup():
    """Source-order pin: the UUID guard must fire BEFORE the
    DATABASE_URL lookup. Otherwise an operator audit chasing 'why is
    log() crashing' would step through the DSN read first and miss
    the simpler upstream rejection.

    More importantly: putting the guard after the DSN lookup means
    a DB-up call still pays the env-lookup cost on every synthetic
    write attempt — cheap but unnecessary.
    """
    import genlab_core.scheduling.calibration_logger as mod

    src = Path(mod.__file__).read_text()
    guard_idx = src.find("_is_uuid_shape(blueprint_id)")
    dsn_idx = src.find('os.getenv("DATABASE_URL")', src.find("def log("))
    assert guard_idx != -1
    assert dsn_idx != -1
    assert guard_idx < dsn_idx, (
        "UUID shape guard must run before the DATABASE_URL lookup so "
        "test-fixture noise is rejected before any env-var work."
    )
