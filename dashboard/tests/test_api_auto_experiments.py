"""Tests for /api/v1/auto-experiments/summary.

Surfaces auto_experiments verdicts + counts to the Mission Control
card. Powers the "did the strategist's testable predictions get
confirmed?" operator loop.

Pins:
  Validation:
    - default niche_id = "all"
    - bad niche_id → 400
    - all 5 canonical + "all" accepted
    - limit capped at 100

  Response shape:
    - carries active_state ("active" | "observation_only")
    - carries flag_env_var name so the frontend can render it
    - counts always has pending/running/completed keys (zero-fill)
    - verdicts_last_30d always has 3 keys

  Fail-open:
    - missing DATABASE_URL → 503
    - DB raise → 200 + data=null

  Active state:
    - GENLAB_AUTO_EXPERIMENT_ENABLED=true → active_state = "active"
    - flag unset → active_state = "observation_only"
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import server.review_server as review_server_module
from server.review_server import app


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", False)


@pytest.fixture(autouse=True)
def _set_dsn(monkeypatch):
    # Fake DSN so the 503 path doesn't fire on every test. Tests
    # that need the 503 path override this locally.
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake@localhost/fake")


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _stub_pg_connect_returning(recent, counts, verdicts):
    """Build a stub for pg_connect that returns the shapes we need.

    The endpoint uses:
      1. list_experiments(conn, ...) — patched separately
      2. conn.execute("SELECT status, COUNT(*)...") — for counts
      3. conn.execute("SELECT (result->>'met_threshold')...") — verdicts

    We stub pg_connect as a context manager whose conn.execute
    returns rows appropriate to the SQL text.
    """
    mock_conn = MagicMock()

    def _execute(sql, *_args, **_kwargs):
        m = MagicMock()
        if "GROUP BY status" in sql:
            m.fetchall.return_value = [
                {"status": s, "n": n} for s, n in counts.items() if n > 0
            ]
        elif "met_threshold" in sql:
            # Expand into individual rows: met=True suff=True, etc.
            rows = []
            rows += [{"met": True, "suff": True}] * verdicts.get("met_threshold", 0)
            rows += [{"met": False, "suff": True}] * verdicts.get("unmet_threshold", 0)
            rows += [{"met": False, "suff": False}] * verdicts.get(
                "insufficient_samples", 0
            )
            m.fetchall.return_value = rows
        else:
            m.fetchall.return_value = []
        return m

    mock_conn.execute.side_effect = _execute
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_conn
    mock_cm.__exit__.return_value = False
    return mock_cm


class TestValidation:
    def test_default_niche_is_all(self, client, monkeypatch):
        # Default niche_id → 'all' must be accepted.
        with (
            patch(
                "server.api.auto_experiments.pg_connect",
                return_value=_stub_pg_connect_returning([], {}, {}),
            ),
            patch(
                "genlab_core.scheduling.auto_experiment.list_experiments",
                return_value=[],
            ),
        ):
            resp = client.get("/api/v1/auto-experiments/summary")
        assert resp.status_code == 200
        assert resp.json["data"]["niche_id"] == "all"

    def test_bad_niche_returns_400(self, client):
        resp = client.get("/api/v1/auto-experiments/summary?niche_id=not_real")
        assert resp.status_code == 400

    @pytest.mark.parametrize(
        "niche_id", ["all", "ai_creators", "gaming", "sports", "movies", "anime"]
    )
    def test_valid_niches_accepted(self, client, niche_id):
        with (
            patch(
                "server.api.auto_experiments.pg_connect",
                return_value=_stub_pg_connect_returning([], {}, {}),
            ),
            patch(
                "genlab_core.scheduling.auto_experiment.list_experiments",
                return_value=[],
            ),
        ):
            resp = client.get(
                f"/api/v1/auto-experiments/summary?niche_id={niche_id}"
            )
        assert resp.status_code == 200

    def test_limit_capped_at_100(self, client):
        captured = {}

        def _spy(_conn, *, status=None, niche_id=None, limit=50):
            captured["limit"] = limit
            return []

        with (
            patch(
                "server.api.auto_experiments.pg_connect",
                return_value=_stub_pg_connect_returning([], {}, {}),
            ),
            patch(
                "genlab_core.scheduling.auto_experiment.list_experiments",
                side_effect=_spy,
            ),
        ):
            resp = client.get(
                "/api/v1/auto-experiments/summary?limit=99999"
            )
        assert resp.status_code == 200
        assert captured["limit"] == 100


class TestResponseShape:
    def test_active_state_when_flag_on(self, client, monkeypatch):
        monkeypatch.setenv("GENLAB_AUTO_EXPERIMENT_ENABLED", "true")
        with (
            patch(
                "server.api.auto_experiments.pg_connect",
                return_value=_stub_pg_connect_returning([], {}, {}),
            ),
            patch(
                "genlab_core.scheduling.auto_experiment.list_experiments",
                return_value=[],
            ),
        ):
            resp = client.get("/api/v1/auto-experiments/summary")
        assert resp.json["data"]["active_state"] == "active"

    def test_observation_only_when_flag_off(self, client, monkeypatch):
        monkeypatch.delenv("GENLAB_AUTO_EXPERIMENT_ENABLED", raising=False)
        with (
            patch(
                "server.api.auto_experiments.pg_connect",
                return_value=_stub_pg_connect_returning([], {}, {}),
            ),
            patch(
                "genlab_core.scheduling.auto_experiment.list_experiments",
                return_value=[],
            ),
        ):
            resp = client.get("/api/v1/auto-experiments/summary")
        assert resp.json["data"]["active_state"] == "observation_only"

    def test_flag_env_var_name_carried(self, client):
        with (
            patch(
                "server.api.auto_experiments.pg_connect",
                return_value=_stub_pg_connect_returning([], {}, {}),
            ),
            patch(
                "genlab_core.scheduling.auto_experiment.list_experiments",
                return_value=[],
            ),
        ):
            resp = client.get("/api/v1/auto-experiments/summary")
        assert resp.json["data"]["flag_env_var"] == "GENLAB_AUTO_EXPERIMENT_ENABLED"

    def test_counts_zero_fill(self, client):
        # DB returns nothing → counts must still have all 3 keys.
        with (
            patch(
                "server.api.auto_experiments.pg_connect",
                return_value=_stub_pg_connect_returning([], {}, {}),
            ),
            patch(
                "genlab_core.scheduling.auto_experiment.list_experiments",
                return_value=[],
            ),
        ):
            resp = client.get("/api/v1/auto-experiments/summary")
        counts = resp.json["data"]["counts"]
        assert set(counts.keys()) == {"pending", "running", "completed"}
        assert counts == {"pending": 0, "running": 0, "completed": 0}

    def test_counts_populated(self, client):
        with (
            patch(
                "server.api.auto_experiments.pg_connect",
                return_value=_stub_pg_connect_returning(
                    [], {"pending": 2, "running": 3, "completed": 7}, {}
                ),
            ),
            patch(
                "genlab_core.scheduling.auto_experiment.list_experiments",
                return_value=[],
            ),
        ):
            resp = client.get("/api/v1/auto-experiments/summary")
        assert resp.json["data"]["counts"]["pending"] == 2
        assert resp.json["data"]["counts"]["running"] == 3
        assert resp.json["data"]["counts"]["completed"] == 7

    def test_verdicts_populated(self, client):
        with (
            patch(
                "server.api.auto_experiments.pg_connect",
                return_value=_stub_pg_connect_returning(
                    [],
                    {},
                    {
                        "met_threshold": 4,
                        "unmet_threshold": 2,
                        "insufficient_samples": 1,
                    },
                ),
            ),
            patch(
                "genlab_core.scheduling.auto_experiment.list_experiments",
                return_value=[],
            ),
        ):
            resp = client.get("/api/v1/auto-experiments/summary")
        v = resp.json["data"]["verdicts_last_30d"]
        assert v["met_threshold"] == 4
        assert v["unmet_threshold"] == 2
        assert v["insufficient_samples"] == 1


class TestFailOpen:
    def test_missing_dsn_returns_503(self, client, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        resp = client.get("/api/v1/auto-experiments/summary")
        assert resp.status_code == 503

    def test_db_raise_returns_data_null(self, client):
        with patch(
            "server.api.auto_experiments.pg_connect",
            side_effect=RuntimeError("boom"),
        ):
            resp = client.get("/api/v1/auto-experiments/summary")
        assert resp.status_code == 200
        assert resp.json["data"] is None
