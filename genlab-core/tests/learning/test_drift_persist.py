"""Pin tests for drift_persist (L7, 2026-07-08 audit).

Sibling suite to `test_ensemble_persist.py` (L5). Covers:
  * Fail-open: never raises
  * Flag gating: strict "1"
  * Row shape locked to migration column order
  * Unparseable computed_at drops the row (not the whole batch)
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from genlab_core.learning.drift_detector import DriftSignal
from genlab_core.learning.drift_persist import (
    _ENABLE_ENV_VAR,
    _persist_enabled,
    record_signals,
)


def _mk_signal(
    *,
    arm_id: str = "style:gaming:question",
    niche_id: str = "gaming",
    z_score: float = -3.5,
    computed_at: str = "2026-07-08T16:00:00+00:00",
) -> DriftSignal:
    return DriftSignal(
        arm_id=arm_id,
        niche_id=niche_id,
        recent_rate=0.20,
        baseline_rate=0.35,
        z_score=z_score,
        recent_obs=15,
        baseline_obs=42,
        computed_at=computed_at,
    )


class TestPersistEnabled:
    def test_unset_is_disabled(self, monkeypatch):
        monkeypatch.delenv(_ENABLE_ENV_VAR, raising=False)
        assert _persist_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", "y", "t"])
    def test_env_true_values_enable(self, monkeypatch, value):
        """2026-07-14: migrated to env_true (accepts 1|true|yes|on|y|t).
        Prior strict-'1' was inconsistent with the rest of the codebase
        — matching class-of-bug fix from ensemble_persist migration."""
        monkeypatch.setenv(_ENABLE_ENV_VAR, value)
        assert _persist_enabled() is True, f"{value!r} should enable"

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
    def test_falsy_values_disable(self, monkeypatch, value):
        monkeypatch.setenv(_ENABLE_ENV_VAR, value)
        assert _persist_enabled() is False, f"{value!r} should disable"


class TestRecordSignalsEarlyExits:
    def test_disabled_returns_zero(self, monkeypatch):
        monkeypatch.delenv(_ENABLE_ENV_VAR, raising=False)
        assert record_signals([_mk_signal()]) == 0

    def test_empty_list_returns_zero(self, monkeypatch):
        monkeypatch.setenv(_ENABLE_ENV_VAR, "1")
        assert record_signals([]) == 0


class TestRecordSignalsWrites:
    def _install_pool(self, monkeypatch, cursor):
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        conn.cursor.return_value.__exit__.return_value = False
        pool = MagicMock()
        pool.connection.return_value.__enter__.return_value = conn
        pool.connection.return_value.__exit__.return_value = False
        monkeypatch.setattr(
            "genlab_core.storage.get_pool", lambda: pool, raising=False
        )
        return cursor

    def test_row_shape_matches_migration_columns(self, monkeypatch):
        """Locks in the tuple order sent to executemany. A migration
        column-order edit surfaces here as a broken pin instead of a
        silent field-count mismatch on prod."""
        monkeypatch.setenv(_ENABLE_ENV_VAR, "1")
        cursor = MagicMock()
        self._install_pool(monkeypatch, cursor)

        n = record_signals(
            [
                _mk_signal(arm_id="a1", z_score=-3.0),
                _mk_signal(arm_id="a2", z_score=+2.5),
            ]
        )
        assert n == 2

        cursor.executemany.assert_called_once()
        sql, rows = cursor.executemany.call_args[0]
        assert "INSERT INTO drift_signals" in sql
        assert len(rows) == 2

        # Column order: arm_id, niche_id, recent_rate, baseline_rate,
        # z_score, recent_obs, baseline_obs, is_regression, computed_at
        first = rows[0]
        assert first[0] == "a1"  # arm_id
        assert first[1] == "gaming"  # niche_id
        assert first[2] == pytest.approx(0.20)  # recent_rate
        assert first[3] == pytest.approx(0.35)  # baseline_rate
        assert first[4] == pytest.approx(-3.0)  # z_score
        assert first[5] == 15  # recent_obs
        assert first[6] == 42  # baseline_obs
        assert first[7] is True  # is_regression (negative + significant)
        # computed_at parsed to datetime, not raw string
        from datetime import datetime

        assert isinstance(first[8], datetime)

        # Positive z-score → is_regression=False. Locking this because
        # the module's is_regression property depends on both sign
        # AND significance — a future significance-threshold shift
        # would flip this.
        second = rows[1]
        assert second[7] is False

    def test_unparseable_computed_at_drops_row_but_keeps_batch(
        self, monkeypatch
    ):
        """A single malformed timestamp shouldn't fail the whole
        batch — the writer drops the row and inserts the rest."""
        monkeypatch.setenv(_ENABLE_ENV_VAR, "1")
        cursor = MagicMock()
        self._install_pool(monkeypatch, cursor)

        n = record_signals(
            [
                _mk_signal(arm_id="good_a", computed_at="2026-07-08T16:00:00"),
                _mk_signal(arm_id="bad_ts", computed_at="not-a-timestamp"),
                _mk_signal(arm_id="good_b", computed_at="2026-07-08T16:05:00"),
            ]
        )
        assert n == 2
        rows = cursor.executemany.call_args[0][1]
        arm_ids = [r[0] for r in rows]
        assert "good_a" in arm_ids
        assert "good_b" in arm_ids
        assert "bad_ts" not in arm_ids

    def test_all_unparseable_returns_zero_without_calling_pool(
        self, monkeypatch
    ):
        """If EVERY row drops, we shouldn't touch the DB at all —
        `pool.connection()` context manager is never entered."""
        monkeypatch.setenv(_ENABLE_ENV_VAR, "1")
        cursor = MagicMock()
        pool = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        conn.cursor.return_value.__exit__.return_value = False
        pool.connection.return_value.__enter__.return_value = conn
        pool.connection.return_value.__exit__.return_value = False
        monkeypatch.setattr(
            "genlab_core.storage.get_pool", lambda: pool, raising=False
        )

        n = record_signals(
            [_mk_signal(computed_at="garbage-1"), _mk_signal(computed_at="")]
        )
        assert n == 0
        pool.connection.assert_not_called()
        cursor.executemany.assert_not_called()


class TestRecordSignalsFailOpen:
    def test_pool_unavailable_returns_zero_no_raise(self, monkeypatch):
        monkeypatch.setenv(_ENABLE_ENV_VAR, "1")

        def raising_get_pool():
            raise RuntimeError("pool exhausted")

        monkeypatch.setattr(
            "genlab_core.storage.get_pool",
            raising_get_pool,
            raising=False,
        )
        assert record_signals([_mk_signal()]) == 0

    def test_executemany_raises_returns_zero_no_raise(self, monkeypatch):
        monkeypatch.setenv(_ENABLE_ENV_VAR, "1")
        cursor = MagicMock()
        cursor.executemany.side_effect = RuntimeError("connection reset")
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        conn.cursor.return_value.__exit__.return_value = False
        pool = MagicMock()
        pool.connection.return_value.__enter__.return_value = conn
        pool.connection.return_value.__exit__.return_value = False
        monkeypatch.setattr(
            "genlab_core.storage.get_pool", lambda: pool, raising=False
        )
        assert record_signals([_mk_signal()]) == 0

    def test_logs_warning_on_failure(self, monkeypatch, caplog):
        monkeypatch.setenv(_ENABLE_ENV_VAR, "1")

        def raising():
            raise RuntimeError("db down")

        monkeypatch.setattr(
            "genlab_core.storage.get_pool", raising, raising=False
        )
        with caplog.at_level(logging.WARNING):
            record_signals([_mk_signal()])
        assert any(
            "drift_persist" in r.message
            for r in caplog.records
        )


class TestDriftDetectorPersistHook:
    """The hook inside `detect_all_drift` MUST call record_signals but
    MUST NOT propagate exceptions from it."""

    def test_detect_all_drift_swallows_hook_exception(self, monkeypatch):
        """Locking in the defense-in-depth wrapping — even if
        record_signals raises (violating its own contract),
        detect_all_drift's in-memory return value is unaffected."""
        # We stub detect_all_drift's snapshot loading so the whole
        # function returns a known signal list, then force the hook
        # to raise, then verify the return value is intact.
        from genlab_core.learning import drift_detector as dd

        signal = _mk_signal(arm_id="hook_test")

        def _fake_load(_dir, lookback_days):  # noqa: ARG001
            return {("gaming", "hook_test"): [{"a": 1}]}

        def _fake_compute(*args, **kwargs):  # noqa: ARG001
            return signal

        def _raising_record(_signals):
            raise RuntimeError("record_signals misbehaved")

        monkeypatch.setattr(dd, "load_snapshots", _fake_load)
        monkeypatch.setattr(dd, "compute_arm_drift", _fake_compute)
        # Patch the module used by the import inside detect_all_drift
        # — the hook uses `from genlab_core.learning.drift_persist
        # import record_signals` inline, so patch at the module.
        monkeypatch.setattr(
            "genlab_core.learning.drift_persist.record_signals",
            _raising_record,
        )

        # threshold_z=0.1 so our small |z|=3.5 signal passes the gate.
        result = dd.detect_all_drift(threshold_z=0.1)
        assert len(result) == 1
        assert result[0].arm_id == "hook_test"
