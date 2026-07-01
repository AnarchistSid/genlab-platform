"""Tests for strategist_actions — write-side materialisation of accepted proposals."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.scheduling import strategist_actions


class TestFeatureFlag:
    def test_disabled_returns_empty_dict(self, monkeypatch):
        monkeypatch.delenv("GENLAB_STRATEGIST_INTEGRATION_ENABLED", raising=False)
        result = strategist_actions.apply_pending_actions()
        assert result == {}

    def test_no_database_url_returns_empty(self, monkeypatch):
        monkeypatch.setenv("GENLAB_STRATEGIST_INTEGRATION_ENABLED", "true")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        result = strategist_actions.apply_pending_actions()
        assert result == {}


class TestArmAddApplication:
    def _make_conn(self, reports, arm_add_will_return_row=True):
        """Build a psycopg mock that returns the given reports + inserts arms."""
        conn = MagicMock()
        conn.__enter__ = lambda self: conn
        conn.__exit__ = lambda *args: None

        # execute() results depend on the SQL
        def _execute(sql, params=None):
            result = MagicMock()
            if "SELECT id, niche_id, proposals" in sql:
                result.fetchall.return_value = reports
            elif "INSERT INTO bandit_arms" in sql:
                # Simulate whether the row was created (RETURNING id vs None)
                result.fetchone.return_value = (
                    {"id": "new-arm-id"} if arm_add_will_return_row else None
                )
            else:
                result.fetchone.return_value = None
                result.fetchall.return_value = []
            return result

        conn.execute.side_effect = _execute
        return conn

    def test_accepted_arm_add_creates_new_arm(self, monkeypatch):
        monkeypatch.setenv("GENLAB_STRATEGIST_INTEGRATION_ENABLED", "true")
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")

        reports = [
            {
                "id": "report-1",
                "niche_id": "gaming",
                "proposals": [
                    {
                        "type": "arm_add",
                        "target": "gaming.arms",
                        "proposed": {
                            "arm_id": "style:speedrun",
                            "prior_alpha": 1,
                            "prior_beta": 2,
                        },
                    }
                ],
                "proposals_accepted": [0],
                "extra": {},
            }
        ]
        conn = self._make_conn(reports, arm_add_will_return_row=True)

        with patch("psycopg.connect", return_value=conn):
            result = strategist_actions.apply_pending_actions()

        assert result["arm_add"] == 1
        assert result["reports_scanned"] == 1
        assert result["errors"] == 0

    def test_already_applied_is_skipped(self, monkeypatch):
        """Idempotency — a proposal at index 0 with applied_indices=[0] is skipped."""
        monkeypatch.setenv("GENLAB_STRATEGIST_INTEGRATION_ENABLED", "true")
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")

        reports = [
            {
                "id": "report-idem",
                "niche_id": "gaming",
                "proposals": [
                    {
                        "type": "arm_add",
                        "target": "gaming.arms",
                        "proposed": {"arm_id": "style:speedrun"},
                    }
                ],
                "proposals_accepted": [0],
                "extra": {"applied_indices": [0]},
            }
        ]
        conn = self._make_conn(reports)
        with patch("psycopg.connect", return_value=conn):
            result = strategist_actions.apply_pending_actions()
        assert result["arm_add"] == 0

    def test_malformed_proposal_shape_does_not_crash(self, monkeypatch):
        monkeypatch.setenv("GENLAB_STRATEGIST_INTEGRATION_ENABLED", "true")
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")

        reports = [
            {
                "id": "report-bad",
                "niche_id": "gaming",
                "proposals": [
                    {"type": "arm_add", "proposed": "not-a-dict"},
                ],
                "proposals_accepted": [0],
                "extra": {},
            }
        ]
        conn = self._make_conn(reports)
        with patch("psycopg.connect", return_value=conn):
            result = strategist_actions.apply_pending_actions()
        # No new arm — proposal shape was invalid
        assert result["arm_add"] == 0
        # But also no error count — malformed shape is silently skipped
        # (proposal-level defensive parsing catches it before _apply_arm_add)
        assert result["errors"] == 0

    def test_non_arm_types_are_not_processed_here(self, monkeypatch):
        """gate_threshold and reward_weight are READ-path — no side-effect."""
        monkeypatch.setenv("GENLAB_STRATEGIST_INTEGRATION_ENABLED", "true")
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")

        reports = [
            {
                "id": "report-rd",
                "niche_id": "ai_creators",
                "proposals": [
                    {
                        "type": "gate_threshold",
                        "target": "ai_creators.gate",
                        "proposed": 0.45,
                    }
                ],
                "proposals_accepted": [0],
                "extra": {},
            }
        ]
        conn = self._make_conn(reports)
        with patch("psycopg.connect", return_value=conn):
            result = strategist_actions.apply_pending_actions()
        # gate_threshold has no side-effect; arm_add counter stays 0
        assert result["arm_add"] == 0
        assert result["errors"] == 0

    def test_multiple_niches_processed_in_one_run(self, monkeypatch):
        monkeypatch.setenv("GENLAB_STRATEGIST_INTEGRATION_ENABLED", "true")
        monkeypatch.setenv("DATABASE_URL", "postgres://fake")

        reports = [
            {
                "id": "r1",
                "niche_id": "gaming",
                "proposals": [
                    {
                        "type": "arm_add",
                        "proposed": {"arm_id": "style:speedrun"},
                    }
                ],
                "proposals_accepted": [0],
                "extra": {},
            },
            {
                "id": "r2",
                "niche_id": "anime",
                "proposals": [
                    {
                        "type": "arm_add",
                        "proposed": {"arm_id": "style:season_recap"},
                    }
                ],
                "proposals_accepted": [0],
                "extra": {},
            },
        ]
        conn = self._make_conn(reports)
        with patch("psycopg.connect", return_value=conn):
            result = strategist_actions.apply_pending_actions()
        assert result["arm_add"] == 2
        assert result["reports_scanned"] == 2

    def test_alpha_beta_clamped_to_sane_bounds(self, monkeypatch):
        """A proposal with alpha=999999 would seed a near-degenerate
        posterior. Clamp keeps things sane."""
        from genlab_core.scheduling.strategist_actions import _apply_arm_add

        conn = MagicMock()
        # Verify INSERT was called with clamped values
        mock_result = MagicMock()
        mock_result.fetchone.return_value = {"id": "new"}
        conn.execute.return_value = mock_result

        _apply_arm_add(
            conn,
            "gaming",
            {
                "type": "arm_add",
                "proposed": {
                    "arm_id": "test",
                    "prior_alpha": 999999,
                    "prior_beta": 0.001,
                },
            },
        )

        # Check the INSERT params — alpha clamped to 100, beta to 0.5
        call_args = conn.execute.call_args_list[0]
        _sql, params = call_args[0]
        _niche, _arm, alpha, beta = params
        assert alpha == 100.0
        assert beta == 0.5
