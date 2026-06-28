"""PR W — Pin the lowercase ``monetisationprogress`` table-name contract.

Audit ref: C4/C6 (2026-03-31).

The original audit flagged that ``BacklogClient`` looked for table
``MonetisationProgress`` (CamelCase) but the live Postgres table is
``monetisationprogress`` (all lowercase, no underscore). The dashboard
endpoint has since been repointed at the live table via
``server.core.monetisation_progress_pg.fetch_progress`` (Sprint 65
Postgres migration). This test pins:

  1. The SELECT query targets the lowercase ``monetisationprogress``
     table — a future renamer who picks ``MonetisationProgress`` or
     ``monetisation_progress`` (snake_case) trips a red test instead
     of silently returning zero rows.
  2. The ``_COLUMNS`` list matches the live schema (12 columns from
     ``adopt_handcreated_tables.py``).
  3. The MonetisationCompact + MonetisationProgress frontend cards
     receive grouped niche → platform → metric rows when the
     endpoint runs against a populated table.

Mocks psycopg.connect — no live DB needed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import server.review_server as review_server_module
from server.core import monetisation_progress_pg
from server.review_server import app


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(review_server_module, "_AUTH_ENABLED", False)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _make_mock_conn(rows: list[dict]) -> MagicMock:
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = False

    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    return conn


# ---------------------------------------------------------------------------
# Table-name pins (audit C4/C6 root cause)
# ---------------------------------------------------------------------------


class TestTableNameContract:
    def test_select_targets_lowercase_table_name(self) -> None:
        """Audit C4/C6: BacklogClient's CamelCase ``MonetisationProgress``
        was the wrong table; live Postgres is lowercase
        ``monetisationprogress`` (one word, no separator). This pins
        the SELECT to that exact spelling so a future renamer who
        picks ``monetisation_progress`` (snake) or
        ``MonetisationProgress`` (camel) trips this test."""
        conn = _make_mock_conn([])
        with patch("psycopg.connect", return_value=conn):
            monetisation_progress_pg.fetch_progress()

        cursor = conn.cursor.return_value
        select_calls = [
            c
            for c in cursor.execute.call_args_list
            if "FROM" in c[0][0].upper() and "monetisationprogress" in c[0][0]
        ]
        assert select_calls, (
            "expected SELECT FROM monetisationprogress; "
            "lowercase one-word spelling is the live table name "
            "(audit C4/C6, 2026-03-31)"
        )
        sql = select_calls[0][0][0]
        # Negative pins — these spellings WOULD silently return zero rows
        # against the live table; trip the test rather than let it ship.
        assert "MonetisationProgress" not in sql, (
            "CamelCase MonetisationProgress is wrong — that's the legacy "
            "SharePoint list name, NOT the Postgres table"
        )
        assert "monetisation_progress" not in sql, (
            "monetisation_progress (snake_case) is wrong — the live table has no underscore"
        )

    def test_columns_match_live_schema(self) -> None:
        """Pins the 12 columns the dashboard reads. If a migration
        renames any of these (e.g. ``pct_complete`` → ``progress_pct``)
        the test trips and forces the frontend to be updated in lock-
        step."""
        expected_columns = {
            "niche_id",
            "platform",
            "metric_name",
            "current_value",
            "target_value",
            "pct_complete",
            "delta_7d",
            "days_to_threshold_est",
            "is_threshold_met",
            "data_source",
            "as_of_date",
            "error_log",
        }
        assert set(monetisation_progress_pg._COLUMNS) == expected_columns


# ---------------------------------------------------------------------------
# End-to-end: the endpoint groups rows by niche → platform → metrics
# ---------------------------------------------------------------------------


SAMPLE_PROD_ROWS = [
    {
        "niche_id": "ai_creators",
        "platform": "facebook",
        "metric_name": "followers",
        "current_value": 10060.0,
        "target_value": 10000.0,
        "pct_complete": 1.006,
        "delta_7d": -8.0,
        "days_to_threshold_est": None,
        "is_threshold_met": True,
        "data_source": "api",
        "as_of_date": "2026-03-31",
        "error_log": None,
    },
    {
        "niche_id": "ai_creators",
        "platform": "youtube",
        "metric_name": "subscribers",
        "current_value": 4.0,
        "target_value": 1000.0,
        "pct_complete": 0.004,
        "delta_7d": 0.0,
        "days_to_threshold_est": None,
        "is_threshold_met": False,
        "data_source": "api",
        "as_of_date": "2026-03-31",
        "error_log": None,
    },
]


class TestEndpointGroupsCorrectly:
    @patch(
        "server.api.monetisation._fetch_progress",
        return_value=SAMPLE_PROD_ROWS,
    )
    def test_endpoint_returns_grouped_data_when_table_populated(self, _mock_fetch, client):
        """C4/C6 was the user-visible symptom: dashboard cards said
        'No data' even when prod had real rows. Pin: a populated table
        produces a grouped envelope the frontend can consume."""
        resp = client.get("/api/v1/monetisation/progress")
        assert resp.status_code == 200
        envelope = json.loads(resp.data)
        assert envelope["status"] == "success"
        data = envelope["data"]

        # MonetisationCompact reads ``progressData?.[niche.id]`` and then
        # iterates ``platform → metrics``; pin that exact shape.
        assert "ai_creators" in data
        assert "facebook" in data["ai_creators"]
        assert "youtube" in data["ai_creators"]
        fb_metrics = data["ai_creators"]["facebook"]["metrics"]
        assert len(fb_metrics) == 1
        assert fb_metrics[0]["metric_name"] == "followers"
        assert fb_metrics[0]["pct_complete"] == 1.006
        assert data["ai_creators"]["facebook"]["is_monetised"] is True

    @patch("server.api.monetisation._fetch_progress", return_value=[])
    def test_endpoint_returns_empty_dict_when_table_empty(self, _mock_fetch, client):
        """Cold-start contract: empty table → empty dict, never 500.
        The frontend then renders 'No data' per row."""
        resp = client.get("/api/v1/monetisation/progress")
        assert resp.status_code == 200
        envelope = json.loads(resp.data)
        assert envelope["data"] == {}
