"""Pin Phase 5.B session 2 autonomous-reviewer endpoint:

  * Cold-start returns {"data": null}
  * DB error returns null
  * flag_enabled reflects env
  * per_type structure with success_rate + is_mature + meta_grade
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from server.api.autonomous_reviewer import bp


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(bp)
    return app.test_client()


class TestColdStart:
    def test_no_dsn(self, client, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert client.get("/api/v1/autonomous-reviewer/status").get_json()["data"] is None

    def test_query_error_returns_null(self, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://unreachable:1/x")
        assert client.get("/api/v1/autonomous-reviewer/status").get_json()["data"] is None


class TestStatus:
    @patch("psycopg.connect")
    def test_flag_off_default(self, mock_connect, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/x")
        monkeypatch.delenv("GENLAB_AUTONOMOUS_REVIEWER_ENABLED", raising=False)
        conn_ctx = MagicMock()
        conn_ctx.__enter__.return_value = conn_ctx
        conn_ctx.__exit__.return_value = False
        # First execute = history query, second = meta grade
        history_result = MagicMock()
        history_result.fetchall.return_value = []
        meta_result = MagicMock()
        meta_result.fetchone.return_value = None
        conn_ctx.execute.side_effect = [history_result, meta_result]
        mock_connect.return_value = conn_ctx

        body = client.get("/api/v1/autonomous-reviewer/status").get_json()
        assert body["data"]["flag_enabled"] is False
        assert body["data"]["per_type"] == []

    @patch("psycopg.connect")
    def test_per_type_with_history_and_grade(self, mock_connect, client, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/x")
        monkeypatch.setenv("GENLAB_AUTONOMOUS_REVIEWER_ENABLED", "1")
        conn_ctx = MagicMock()
        conn_ctx.__enter__.return_value = conn_ctx
        conn_ctx.__exit__.return_value = False
        history_result = MagicMock()
        history_result.fetchall.return_value = [
            {
                "proposal_type": "arm_add",
                "n": 12, "n_imp": 8, "n_unc": 2, "n_reg": 2,
            },
        ]
        meta_result = MagicMock()
        meta_result.fetchone.return_value = {
            "per_type_grades": {"arm_add": "A", "reward_weight": "C"},
        }
        conn_ctx.execute.side_effect = [history_result, meta_result]
        mock_connect.return_value = conn_ctx

        body = client.get("/api/v1/autonomous-reviewer/status").get_json()
        assert body["data"]["flag_enabled"] is True
        assert len(body["data"]["per_type"]) == 1
        row = body["data"]["per_type"][0]
        assert row["proposal_type"] == "arm_add"
        assert row["n_verdicts_8wk"] == 12
        assert row["success_rate"] == pytest.approx(0.8)
        assert row["is_mature"] is True
        assert row["meta_grade"] == "A"

    @patch("psycopg.connect")
    def test_meta_grade_as_string_jsonb_parsed(self, mock_connect, client, monkeypatch):
        import json
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake/x")
        conn_ctx = MagicMock()
        conn_ctx.__enter__.return_value = conn_ctx
        conn_ctx.__exit__.return_value = False
        history_result = MagicMock()
        history_result.fetchall.return_value = [
            {"proposal_type": "arm_add", "n": 5,
             "n_imp": 4, "n_unc": 0, "n_reg": 1},
        ]
        meta_result = MagicMock()
        meta_result.fetchone.return_value = {
            "per_type_grades": json.dumps({"arm_add": "B"}),
        }
        conn_ctx.execute.side_effect = [history_result, meta_result]
        mock_connect.return_value = conn_ctx

        body = client.get("/api/v1/autonomous-reviewer/status").get_json()
        assert body["data"]["per_type"][0]["meta_grade"] == "B"
