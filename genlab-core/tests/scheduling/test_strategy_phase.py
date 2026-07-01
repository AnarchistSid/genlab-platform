"""Tests for PR Strategist-3 integration surfaces.

Covers:
- strategy_phase.get_phase_config caching + fail-closed semantics
- Feature-flag gating (integration OFF by default)
- Proposal materialisation (accepted → PhaseConfig fields)
- Consumer-side integration test that the auto_approval_gate honors
  a Strategist gate_threshold override
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from genlab_core.intelligence.proposal_schema import StrategicPhase
from genlab_core.scheduling import strategy_phase


@pytest.fixture(autouse=True)
def _clear_cache():
    strategy_phase.clear_cache()
    yield
    strategy_phase.clear_cache()


@pytest.fixture
def _enable_integration(monkeypatch):
    monkeypatch.setenv("GENLAB_STRATEGIST_INTEGRATION_ENABLED", "true")
    yield
    monkeypatch.delenv("GENLAB_STRATEGIST_INTEGRATION_ENABLED", raising=False)


@pytest.fixture
def _disable_integration(monkeypatch):
    monkeypatch.delenv("GENLAB_STRATEGIST_INTEGRATION_ENABLED", raising=False)


class TestFeatureFlag:
    def test_disabled_returns_default_without_touching_db(self, _disable_integration):
        with patch("psycopg.connect") as mock_connect:
            config = strategy_phase.get_phase_config("ai_creators")
        assert config.gate_threshold_override is None
        assert config.reward_weight_overrides == {}
        assert config.active_findings == []
        # Critical: DB never touched when flag is off
        mock_connect.assert_not_called()

    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("GENLAB_STRATEGIST_INTEGRATION_ENABLED", raising=False)
        assert not strategy_phase._integration_enabled()

    def test_enabled_only_on_exact_true(self, monkeypatch):
        for value in ("true", "TRUE", "True"):
            monkeypatch.setenv("GENLAB_STRATEGIST_INTEGRATION_ENABLED", value)
            assert strategy_phase._integration_enabled()
        for value in ("1", "yes", "on", "", "false"):
            monkeypatch.setenv("GENLAB_STRATEGIST_INTEGRATION_ENABLED", value)
            assert not strategy_phase._integration_enabled(), f"unexpected truthy for {value!r}"


class TestFailClosed:
    def test_db_error_returns_default(self, _enable_integration, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")
        with patch("psycopg.connect", side_effect=RuntimeError("boom")):
            config = strategy_phase.get_phase_config("ai_creators")
        assert config.niche_id == "__default__"
        assert config.gate_threshold_override is None

    def test_no_database_url_returns_default(self, _enable_integration, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        config = strategy_phase.get_phase_config("ai_creators")
        assert config.gate_threshold_override is None


class TestProposalMaterialisation:
    def _mock_conn_with_row(self, row, findings=None):
        """Build a psycopg connection mock that returns the given proposal row."""
        conn = MagicMock()
        conn.__enter__ = lambda self: conn
        conn.__exit__ = lambda *args: None

        # First execute() returns the proposals row; second returns findings.
        def _execute(sql, params=None):
            result = MagicMock()
            if "learning_findings" in sql:
                result.fetchall.return_value = findings or []
            else:
                result.fetchone.return_value = row
            return result

        conn.execute.side_effect = _execute
        return conn

    def test_gate_threshold_proposal_becomes_override(self, _enable_integration, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")
        row = {
            "id": "abc-123",
            "run_at": "2026-06-29T02:00:00Z",
            "detected_phase": "GROWTH",
            "proposals": [
                {
                    "type": "gate_threshold",
                    "target": "ai_creators.gate.composite_threshold",
                    "current": 0.30,
                    "proposed": 0.45,
                }
            ],
            "proposals_accepted": [0],
        }
        conn = self._mock_conn_with_row(row)

        with patch("psycopg.connect", return_value=conn):
            config = strategy_phase.get_phase_config("ai_creators")

        assert config.gate_threshold_override == pytest.approx(0.45)
        assert config.detected_phase == StrategicPhase.GROWTH
        assert config.source_report_id == "abc-123"

    def test_novelty_rate_proposal_becomes_override(self, _enable_integration, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")
        row = {
            "id": "def-456",
            "run_at": "2026-06-29T02:00:00Z",
            "detected_phase": "OPTIMIZE",
            "proposals": [
                {
                    "type": "novelty_rate",
                    "target": "ai_creators.novelty_rate",
                    "current": 0.10,
                    "proposed": 0.20,
                }
            ],
            "proposals_accepted": [0],
        }
        conn = self._mock_conn_with_row(row)
        with patch("psycopg.connect", return_value=conn):
            config = strategy_phase.get_phase_config("ai_creators")
        assert config.novelty_rate_override == pytest.approx(0.20)

    def test_out_of_range_gate_threshold_rejected(self, _enable_integration, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")
        row = {
            "id": "ghi-789",
            "run_at": "2026-06-29T02:00:00Z",
            "detected_phase": "BOOTSTRAP",
            "proposals": [
                {
                    "type": "gate_threshold",
                    "target": "ai_creators.gate.composite_threshold",
                    "current": 0.30,
                    "proposed": 0.99,  # out of clamp range [0.05, 0.85]
                }
            ],
            "proposals_accepted": [0],
        }
        conn = self._mock_conn_with_row(row)
        with patch("psycopg.connect", return_value=conn):
            config = strategy_phase.get_phase_config("ai_creators")
        # Refused: too extreme
        assert config.gate_threshold_override is None

    def test_rejected_proposal_indices_do_not_apply(self, _enable_integration, monkeypatch):
        """Proposals at indices NOT in proposals_accepted must not affect
        PhaseConfig even if the operator rejected them explicitly."""
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")
        row = {
            "id": "jkl-012",
            "run_at": "2026-06-29T02:00:00Z",
            "detected_phase": "GROWTH",
            "proposals": [
                {
                    "type": "gate_threshold",
                    "target": "ai_creators.gate.composite_threshold",
                    "current": 0.30,
                    "proposed": 0.55,
                },
                {
                    "type": "novelty_rate",
                    "target": "ai_creators.novelty_rate",
                    "current": 0.10,
                    "proposed": 0.25,
                },
            ],
            "proposals_accepted": [1],  # only novelty_rate accepted
        }
        conn = self._mock_conn_with_row(row)
        with patch("psycopg.connect", return_value=conn):
            config = strategy_phase.get_phase_config("ai_creators")
        assert config.gate_threshold_override is None
        assert config.novelty_rate_override == pytest.approx(0.25)

    def test_findings_populated_from_learning_findings_table(
        self, _enable_integration, monkeypatch
    ):
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")
        conn = self._mock_conn_with_row(
            row=None,
            findings=[
                {"finding_text": "Posts with concrete numbers get 2x engagement"},
                {"finding_text": "Question hooks work better than statement hooks"},
            ],
        )
        with patch("psycopg.connect", return_value=conn):
            config = strategy_phase.get_phase_config("gaming")
        assert len(config.active_findings) == 2
        assert "concrete numbers" in config.active_findings[0]


class TestPhaseParser:
    def test_valid_phase_string(self):
        assert strategy_phase._parse_phase("GROWTH") == StrategicPhase.GROWTH

    def test_lowercase_upcased(self):
        assert strategy_phase._parse_phase("growth") == StrategicPhase.GROWTH

    def test_unknown_phase_fails_closed_to_bootstrap(self):
        assert strategy_phase._parse_phase("UNKNOWN") == StrategicPhase.BOOTSTRAP

    def test_none_fails_closed_to_bootstrap(self):
        assert strategy_phase._parse_phase(None) == StrategicPhase.BOOTSTRAP


class TestAutoApprovalGateWiring:
    """Verify the auto_approval_gate actually consults strategy_phase.

    This is the smoke test that the wire from PR Strategist-3 works —
    when integration is enabled and a Strategist proposal sets a
    threshold override, the gate uses it.
    """

    def test_gate_uses_strategist_override_when_enabled(self, _enable_integration, monkeypatch):
        from genlab_core.scheduling import auto_approval_gate

        # Blueprint with composite=0.4 — should be REJECTED at 0.5 override
        # but ACCEPTED at the default 0.3
        blueprint = {
            "niche_id": "ai_creators",
            "hook_text": "Test hook here",
            "extra": {
                "composite_score": 0.4,
                "virality_score": 0.05,
                "visual_paths": '["/tmp/test.mp4"]',
                "validation_status": {"all_passed": True},
            },
        }

        # Mock get_phase_config to return an override at 0.5
        from genlab_core.scheduling.strategy_phase import PhaseConfig

        mock_config = PhaseConfig(
            niche_id="ai_creators",
            gate_threshold_override=0.5,
        )

        with patch(
            "genlab_core.scheduling.strategy_phase.get_phase_config",
            return_value=mock_config,
        ):
            with patch(
                "genlab_core.learning.gate_tuner.get_overrides_for_niche",
                return_value=(None, None),
            ):
                decision = auto_approval_gate.evaluate(blueprint)

        # 0.4 < 0.5 override → should be gated
        assert decision.approved is False, "gate should reject 0.4 vs Strategist 0.5"
