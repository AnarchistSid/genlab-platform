"""Pin the Postgres implementations of MetricSnapshotProvider and
VerificationRecordStore. Uses mocks — real Postgres integration is
covered by post-deploy smoke.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from genlab_core.scheduling.outcome_verifier import Verdict, VerificationRecord
from genlab_core.scheduling.outcome_verifier_postgres import (
    PostgresMetricSnapshotProvider,
    PostgresVerificationRecordStore,
)


def _mock_conn(fetchone_val=None, fetchall_val=None):
    cur = MagicMock()
    cur.fetchone.return_value = fetchone_val
    cur.fetchall.return_value = fetchall_val or []
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=None)
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=None)
    return conn


class TestMetricSnapshotProvider:
    def test_arm_reward_returns_beta_posterior_mean(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")
        p = PostgresMetricSnapshotProvider()
        # α=3, β=7 → mean = 0.3
        with patch(
            "genlab_core.storage.tenant_context.pg_connect",
            return_value=_mock_conn(fetchone_val=(3.0, 7.0, 10)),
        ):
            val = p.snapshot("anime", "arm_reward:anime:hook_type:anime:character_debate")
        assert val == pytest.approx(0.3)

    def test_arm_reward_returns_none_when_arm_missing(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")
        p = PostgresMetricSnapshotProvider()
        with patch(
            "genlab_core.storage.tenant_context.pg_connect",
            return_value=_mock_conn(fetchone_val=None),
        ):
            val = p.snapshot("anime", "arm_reward:anime:nonexistent_arm")
        assert val is None

    def test_arm_reward_niche_mismatch_returns_none(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")
        p = PostgresMetricSnapshotProvider()
        # Metric target says "anime" but caller passes "gaming"
        val = p.snapshot("gaming", "arm_reward:anime:hook_type:anime:x")
        assert val is None  # short-circuits before DB call

    def test_platform_reward_returns_7d_average(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")
        p = PostgresMetricSnapshotProvider()
        with patch(
            "genlab_core.storage.tenant_context.pg_connect",
            return_value=_mock_conn(fetchone_val=(0.42,)),
        ):
            val = p.snapshot("anime", "platform_reward:anime:facebook")
        assert val == pytest.approx(0.42)

    def test_bandit_coverage_fraction(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")
        p = PostgresMetricSnapshotProvider()
        with patch(
            "genlab_core.storage.tenant_context.pg_connect",
            return_value=_mock_conn(fetchone_val=(75.0, 100.0)),
        ):
            val = p.snapshot("gaming", "bandit_coverage:gaming")
        assert val == pytest.approx(0.75)

    def test_unknown_metric_prefix_returns_none(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")
        p = PostgresMetricSnapshotProvider()
        val = p.snapshot("gaming", "some_new_metric:foo:bar")
        assert val is None  # short-circuits, no DB call

    def test_no_dsn_returns_none(self):
        p = PostgresMetricSnapshotProvider(dsn="")
        assert p.snapshot("gaming", "arm_reward:gaming:x") is None

    def test_db_error_returns_none(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")
        p = PostgresMetricSnapshotProvider()
        with patch(
            "genlab_core.storage.tenant_context.pg_connect",
            side_effect=RuntimeError("db down"),
        ):
            val = p.snapshot("anime", "arm_reward:anime:x")
        assert val is None


class TestVerificationRecordStore:
    def _record(self, verdict=Verdict.PENDING):
        return VerificationRecord(
            proposal_id="uuid:3",
            proposal_type="arm_add",
            proposal_target="anime.arms",
            niche_id="anime",
            applied_at=datetime.now(UTC),
            metric_name="arm_reward:anime:hook_type:anime:x",
            baseline_value=0.25,
            t_plus_48h_value=None,
            verdict=verdict,
            rollback_recommended=False,
        )

    def test_insert_uses_on_conflict_do_nothing(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")
        s = PostgresVerificationRecordStore()
        captured = {}

        def _capture_execute(sql, params):
            captured["sql"] = sql
            captured["params"] = params

        mock_conn = _mock_conn()
        mock_conn.execute = _capture_execute
        mock_conn.commit = MagicMock()
        with patch(
            "genlab_core.storage.tenant_context.pg_connect",
            return_value=mock_conn,
        ):
            s.insert(self._record())
        assert "INSERT INTO strategist_outcome_verification" in captured["sql"]
        assert "ON CONFLICT (proposal_id) DO NOTHING" in captured["sql"]
        assert captured["params"][0] == "uuid:3"

    def test_insert_swallows_db_error(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")
        s = PostgresVerificationRecordStore()
        with patch(
            "genlab_core.storage.tenant_context.pg_connect",
            side_effect=RuntimeError("db down"),
        ):
            s.insert(self._record())  # must not raise

    def test_update_verdict_writes_all_fields(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")
        s = PostgresVerificationRecordStore()
        captured = {}

        def _capture_execute(sql, params):
            captured["sql"] = sql
            captured["params"] = params

        mock_conn = _mock_conn()
        mock_conn.execute = _capture_execute
        mock_conn.commit = MagicMock()
        with patch(
            "genlab_core.storage.tenant_context.pg_connect",
            return_value=mock_conn,
        ):
            s.update_verdict("uuid:3", 0.35, Verdict.IMPROVED, False)
        assert "UPDATE strategist_outcome_verification" in captured["sql"]
        assert captured["params"] == (0.35, "improved", False, "uuid:3")

    def test_list_pending_returns_records(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")
        s = PostgresVerificationRecordStore()
        applied = datetime.now(UTC) - timedelta(hours=50)
        row = ("uuid:1", "arm_add", "anime.arms", "anime", applied,
               "arm_reward:anime:x", 0.2, None, "pending", False, "")
        with patch(
            "genlab_core.storage.tenant_context.pg_connect",
            return_value=_mock_conn(fetchall_val=[row]),
        ):
            records = s.list_pending(datetime.now(UTC))
        assert len(records) == 1
        r = records[0]
        assert r.proposal_id == "uuid:1"
        assert r.niche_id == "anime"
        assert r.verdict == Verdict.PENDING
        assert r.baseline_value == 0.2

    def test_list_pending_empty_on_db_error(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")
        s = PostgresVerificationRecordStore()
        with patch(
            "genlab_core.storage.tenant_context.pg_connect",
            side_effect=RuntimeError("db down"),
        ):
            assert s.list_pending(datetime.now(UTC)) == []
