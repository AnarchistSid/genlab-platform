"""Pin tests for ensemble_persist (L5, 2026-07-08 audit).

Covers the two contracts the module must uphold:
  * Fail-open: NEVER raise into the caller. Any failure returns False.
  * Flag gating: strict "1" match. "true" is NOT enabled — matches
    the L11 flag-state case-sensitivity guard.

Also pins the row shape sent to Postgres so a schema drift in the
migration surfaces here as a broken pin instead of a silent field-
count mismatch on prod.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
from genlab_core.scheduling.ensemble_decide import (
    EnsembleDecision,
    EnsembleVote,
)
from genlab_core.scheduling.ensemble_persist import (
    _ENABLE_ENV_VAR,
    _persist_enabled,
    record_decision,
)


def _mk_decision(*, n_votes: int = 3) -> EnsembleDecision:
    """Build an EnsembleDecision with `n_votes` votes.

    Every test builds its own decision — decoupling from real
    component adapters means these pins can't accidentally regress
    when a component's semantics change.
    """
    votes = [
        EnsembleVote(
            component=f"comp_{i}",
            score=0.5 + i * 0.1,
            weight=0.25,
            reason=f"reason {i}",
        )
        for i in range(n_votes)
    ]
    return EnsembleDecision(
        score=0.6,
        disagreement=0.02,
        n_voters=n_votes,
        votes=votes,
        recommendation="worth_your_look",
        reasons=["ok"],
    )


class TestPersistEnabled:
    """2026-07-14: unified to env_true() semantics — accepts the same
    truthy values as every other flag reader in the codebase. Prior
    tests locked strict ``"1"``-only which conflicted with sibling
    ensemble_decide's strict ``"true"``-only (see the module docstring
    for the class-of-bug context)."""

    def test_unset_is_disabled(self, monkeypatch):
        monkeypatch.delenv(_ENABLE_ENV_VAR, raising=False)
        assert _persist_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "True", "yes", "on", "y", "t"])
    def test_truthy_values_are_enabled(self, monkeypatch, value):
        """env_true() accepts these — any of them activates the flag."""
        monkeypatch.setenv(_ENABLE_ENV_VAR, value)
        assert _persist_enabled() is True, f"{value!r} should enable"

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "random"])
    def test_falsy_values_are_disabled(self, monkeypatch, value):
        monkeypatch.setenv(_ENABLE_ENV_VAR, value)
        assert _persist_enabled() is False, f"{value!r} should disable"

    def test_sibling_flag_consistency(self, monkeypatch):
        """Cross-flag pin: ensemble_decide's flag AND ensemble_persist's
        flag must accept the SAME set of activation values. Prior to the
        2026-07-14 unify PR, decide accepted only ``"true"`` while
        persist accepted only ``"1"`` — operators who set BOTH to the
        same value would activate only ONE and silently no-op the other."""
        from genlab_core.scheduling.ensemble_decide import (
            _ENABLE_ENV_VAR as decide_env,
            _integration_enabled,
        )

        # If both flags read the same value, they must both agree
        for value in ("true", "1", "TRUE", "yes"):
            monkeypatch.setenv(decide_env, value)
            monkeypatch.setenv(_ENABLE_ENV_VAR, value)
            assert _integration_enabled() == _persist_enabled(), (
                f"{value!r} activates one but not the other — silent no-op risk"
            )


class TestRecordDecisionDisabled:
    def test_disabled_returns_false_no_calls(self, monkeypatch):
        monkeypatch.delenv(_ENABLE_ENV_VAR, raising=False)
        # Even if the pool were somehow available, disabled means no-op.
        assert record_decision("bp1", "gaming", _mk_decision()) is False

    def test_no_votes_returns_false(self, monkeypatch):
        monkeypatch.setenv(_ENABLE_ENV_VAR, "1")
        empty = EnsembleDecision(
            score=None,
            disagreement=0.0,
            n_voters=0,
            votes=[],
            recommendation="disabled",
        )
        assert record_decision("bp1", "gaming", empty) is False


class TestRecordDecisionWrites:
    """Enabled + votes present → executemany() called with row shape."""

    def _install_pool(self, monkeypatch, cursor):
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        conn.cursor.return_value.__exit__.return_value = False
        pool = MagicMock()
        pool.connection.return_value.__enter__.return_value = conn
        pool.connection.return_value.__exit__.return_value = False

        # Patch the lazy-import path — record_decision imports get_pool
        # from genlab_core.storage inside the function, so patch that
        # namespace, not the caller's.
        get_pool = MagicMock(return_value=pool)
        monkeypatch.setattr(
            "genlab_core.storage.get_pool", get_pool, raising=False
        )
        return pool, conn, cursor

    def test_enabled_path_calls_executemany_with_correct_shape(
        self, monkeypatch
    ):
        monkeypatch.setenv(_ENABLE_ENV_VAR, "1")
        cursor = MagicMock()
        self._install_pool(monkeypatch, cursor)

        decision = _mk_decision(n_votes=3)
        assert record_decision("bp1", "gaming", decision) is True

        cursor.executemany.assert_called_once()
        sql, rows = cursor.executemany.call_args[0]

        # Row shape MUST match the migration columns in order —
        # locking this in so a schema edit forces a test update.
        assert "INSERT INTO ensemble_votes" in sql
        assert len(rows) == 3
        for i, row in enumerate(rows):
            assert row[0] == "bp1"  # blueprint_id
            assert row[1] == "gaming"  # niche_id
            assert row[2] == f"comp_{i}"  # component
            assert row[3] == pytest.approx(0.5 + i * 0.1)  # score
            assert row[4] == pytest.approx(0.25)  # weight
            assert row[5] == f"reason {i}"  # reason
            assert row[6] == pytest.approx(0.6)  # ensemble_score
            assert row[7] == pytest.approx(0.02)  # disagreement
            assert row[8] == "worth_your_look"  # recommendation
            assert row[9] == 3  # n_voters

    def test_abstain_vote_writes_null_score(self, monkeypatch):
        """A component that abstained (score=None) MUST persist as
        SQL NULL — downstream AVG/VAR will exclude it correctly.
        Locking this because a well-meaning coerce-to-0 would poison
        the aggregate."""
        monkeypatch.setenv(_ENABLE_ENV_VAR, "1")
        cursor = MagicMock()
        self._install_pool(monkeypatch, cursor)

        abstain_decision = EnsembleDecision(
            score=0.5,
            disagreement=0.0,
            n_voters=1,
            votes=[
                EnsembleVote("bandit", None, 0.30, "no arm_id"),
                EnsembleVote("hook_classifier", 0.5, 0.25, "ok"),
            ],
            recommendation="insufficient_data",
        )
        record_decision("bp2", "movies", abstain_decision)

        rows = cursor.executemany.call_args[0][1]
        # Vote #0 abstained → SQL NULL, not 0.0.
        assert rows[0][3] is None
        assert rows[1][3] == pytest.approx(0.5)

    def test_empty_reason_persists_as_null(self, monkeypatch):
        """Empty-string reason → NULL. TEXT column with NULL saves
        one byte per row over "" and is semantically clearer."""
        monkeypatch.setenv(_ENABLE_ENV_VAR, "1")
        cursor = MagicMock()
        self._install_pool(monkeypatch, cursor)

        decision = EnsembleDecision(
            score=0.5,
            disagreement=0.0,
            n_voters=1,
            votes=[EnsembleVote("bandit", 0.5, 0.30, "")],  # empty reason
            recommendation="disabled",
        )
        record_decision("bp3", "anime", decision)

        rows = cursor.executemany.call_args[0][1]
        assert rows[0][5] is None


class TestRecordDecisionFailOpen:
    """Contract: record_decision MUST NEVER raise. Any exception is
    logged + swallowed. The caller predicates its correctness on
    this."""

    def test_pool_unavailable_returns_false_no_raise(self, monkeypatch):
        monkeypatch.setenv(_ENABLE_ENV_VAR, "1")

        def raising_get_pool():
            raise RuntimeError("pool exhausted")

        monkeypatch.setattr(
            "genlab_core.storage.get_pool",
            raising_get_pool,
            raising=False,
        )

        # MUST NOT propagate — the ensemble decision itself still
        # has to reach the reviewer.
        result = record_decision("bp1", "gaming", _mk_decision())
        assert result is False

    def test_executemany_raises_returns_false_no_raise(self, monkeypatch):
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
            "genlab_core.storage.get_pool",
            lambda: pool,
            raising=False,
        )

        result = record_decision("bp1", "gaming", _mk_decision())
        assert result is False

    def test_import_failure_returns_false_no_raise(self, monkeypatch):
        """If genlab_core.storage is somehow unimportable (e.g. minimal
        deployment), record_decision must still degrade gracefully."""
        monkeypatch.setenv(_ENABLE_ENV_VAR, "1")

        # Patch the lazy-import statement itself by making get_pool
        # attribute raise on access. We patch storage.get_pool to a
        # side-effect that raises.
        def raising_get_pool():
            raise ImportError("simulated: psycopg not installed")

        monkeypatch.setattr(
            "genlab_core.storage.get_pool",
            raising_get_pool,
            raising=False,
        )

        result = record_decision("bp1", "gaming", _mk_decision())
        assert result is False

    def test_logs_warning_on_failure(self, monkeypatch, caplog):
        """Failure MUST be observable — otherwise operators debugging
        "why is the ensemble_votes table empty?" have nothing to look
        at. Locking in that the warning fires."""
        monkeypatch.setenv(_ENABLE_ENV_VAR, "1")
        monkeypatch.setattr(
            "genlab_core.storage.get_pool",
            lambda: (_ for _ in ()).throw(RuntimeError("db down")),
            raising=False,
        )

        with caplog.at_level(logging.WARNING):
            record_decision("bp1", "gaming", _mk_decision())

        assert any(
            "ensemble_persist" in r.message or "pool_unavailable" in r.message
            for r in caplog.records
        )


class TestEnsembleDecidePersistHook:
    """The hook inside ensemble_decide MUST call record_decision but
    MUST NOT propagate exceptions from it."""

    def test_ensemble_decide_calls_record_decision_when_enabled(
        self, monkeypatch
    ):
        # Enable both flags. The hook fires unconditionally on the
        # ensemble path; record_decision decides internally whether
        # persistence is on.
        monkeypatch.setenv("GENLAB_ENSEMBLE_DECISION_ENABLED", "true")
        monkeypatch.setenv(_ENABLE_ENV_VAR, "1")

        with patch(
            "genlab_core.scheduling.ensemble_persist.record_decision"
        ) as mock_record:
            from genlab_core.scheduling import ensemble_decide as ed

            # Minimal blueprint — bandit + others will mostly abstain,
            # but the hook still fires regardless of vote count.
            blueprint = {"id": "bp-hook-1"}
            ed.ensemble_decide(blueprint, "gaming")

            mock_record.assert_called_once()
            args = mock_record.call_args[0]
            assert args[0] == "bp-hook-1"
            assert args[1] == "gaming"

    def test_ensemble_decide_swallows_hook_exception(self, monkeypatch):
        """Even if record_decision misbehaves and raises (violating
        its own contract), ensemble_decide MUST still return the
        decision to the caller."""
        monkeypatch.setenv("GENLAB_ENSEMBLE_DECISION_ENABLED", "true")
        monkeypatch.setenv(_ENABLE_ENV_VAR, "1")

        with patch(
            "genlab_core.scheduling.ensemble_persist.record_decision",
            side_effect=RuntimeError("bad record_decision"),
        ):
            from genlab_core.scheduling import ensemble_decide as ed

            decision = ed.ensemble_decide({"id": "bp1"}, "gaming")
            # We got a real decision object back — recovery worked.
            assert decision is not None
            assert decision.recommendation != ""

    def test_missing_blueprint_id_passes_empty_string(self, monkeypatch):
        """A blueprint dict without an 'id' MUST NOT crash the hook.
        We pass empty string and the persist layer can decide what to
        do with it (currently: still write). Locks in the graceful
        fallback."""
        monkeypatch.setenv("GENLAB_ENSEMBLE_DECISION_ENABLED", "true")
        monkeypatch.setenv(_ENABLE_ENV_VAR, "1")

        with patch(
            "genlab_core.scheduling.ensemble_persist.record_decision"
        ) as mock_record:
            from genlab_core.scheduling import ensemble_decide as ed

            ed.ensemble_decide({}, "gaming")  # no 'id'
            args = mock_record.call_args[0]
            assert args[0] == ""
