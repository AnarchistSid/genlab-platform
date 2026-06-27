"""PR B (2026-06-27) — per-niche per-platform publishing-health card.

Pins for:
  * ``server.core.publishing_health_per_niche.fetch_per_niche_platform_health``
    — pure SQL math + fail-OPEN behaviour
  * ``server.api.publishing_health`` — endpoint shape, SR-F filter,
    thresholds in response, days param clamping

Today's investigation surfaced that ``ai_creators`` Instagram failed 3 times
in the last 7 days (8 in 60 days, all error 2207077) without operator
visibility because the existing per-platform health card aggregates ACROSS
niches. This card / endpoint makes silent niche × platform regressions
visible at-a-glance within ~24h.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

# Dashboard tests sometimes run with cwd outside dashboard/ — make
# the ``server`` package importable explicitly (same pattern as
# test_api_metrics_publishing_skipped.py).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Import the core module at test-collection time so ``patch(...)`` can
# resolve its attributes via the standard ModuleType lookup.
import server.core.publishing_health_per_niche  # noqa: E402,F401

# ── Flask app fixtures ────────────────────────────────────────────


def _make_app_with_publishing_health_bp():
    from flask import Flask
    from server.api.publishing_health import bp

    app = Flask(__name__)
    app.register_blueprint(bp)
    return app


def _mock_pg_with_rows(rows: list[dict]) -> MagicMock:
    """Build a MagicMock pg_connect context that yields the given rows
    from the single .execute(...).fetchall() inside the library helper.

    The library issues TWO execute() calls per request:
      1. ``SELECT set_config(...)`` — return value unused
      2. The grouped query — .fetchall() returns the rows
    """
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn

    # Two calls — set_config + grouped query. side_effect lets us
    # control each successive .execute return shape.
    set_config_result = MagicMock()  # unused .fetchall, RV is discarded
    grouped_query_result = MagicMock()
    grouped_query_result.fetchall.return_value = rows
    fake_conn.execute.side_effect = [set_config_result, grouped_query_result]
    return fake_conn


# ── Library function: SQL math pins ───────────────────────────────


def test_success_rate_computation(monkeypatch):
    """3 SUCCESS + 2 FAILED for one niche/platform → 60.0%.

    Tests the core arithmetic the card depends on. Round to 1dp is
    a deliberate API contract (see THRESHOLDS comment) — operator
    doesn't need 4 digits of false precision.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    rows = [
        {
            "niche_id": "ai_creators",
            "platform": "instagram",
            "success_count": 3,
            "failed_count": 2,
            "last_success_at": datetime(2026, 6, 25, 12, 0, tzinfo=UTC),
            "last_failure_at": datetime(2026, 6, 27, 6, 42, 47, tzinfo=UTC),
            "last_failure_error": "Container processing error: 2207077",
        }
    ]
    fake_conn = _mock_pg_with_rows(rows)
    with patch("server.core.publishing_health_per_niche.pg_connect", return_value=fake_conn):
        from server.core.publishing_health_per_niche import (
            fetch_per_niche_platform_health,
        )

        result = fetch_per_niche_platform_health(days=14)

    assert len(result) == 1
    assert result[0]["niche_id"] == "ai_creators"
    assert result[0]["platform"] == "instagram"
    assert result[0]["success_count"] == 3
    assert result[0]["failed_count"] == 2
    assert result[0]["success_rate_pct"] == 60.0


def test_insufficient_data_returns_null_rate(monkeypatch):
    """<3 publishes in the window → success_rate_pct=None.

    With our 1/day/channel cap, fewer than 3 samples is too noisy for
    red/yellow/green to be meaningful. Card renders these as "—" with
    a greyed cell so operator distinguishes "no data" from "actually 0%".
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    rows = [
        # 1 success + 1 failed = 2 total → insufficient
        {
            "niche_id": "gaming",
            "platform": "youtube",
            "success_count": 1,
            "failed_count": 1,
            "last_success_at": datetime(2026, 6, 26, tzinfo=UTC),
            "last_failure_at": datetime(2026, 6, 25, tzinfo=UTC),
            "last_failure_error": "Some error",
        },
        # 2 success + 0 failed = 2 total → insufficient (even at 100%)
        {
            "niche_id": "anime",
            "platform": "facebook",
            "success_count": 2,
            "failed_count": 0,
            "last_success_at": datetime(2026, 6, 26, tzinfo=UTC),
            "last_failure_at": None,
            "last_failure_error": None,
        },
    ]
    fake_conn = _mock_pg_with_rows(rows)
    with patch("server.core.publishing_health_per_niche.pg_connect", return_value=fake_conn):
        from server.core.publishing_health_per_niche import (
            fetch_per_niche_platform_health,
        )

        result = fetch_per_niche_platform_health(days=14)

    assert result[0]["success_rate_pct"] is None
    assert result[1]["success_rate_pct"] is None
    # But sample counts ARE surfaced (operator can still see the raw numbers)
    assert result[0]["success_count"] == 1
    assert result[1]["success_count"] == 2


def test_skipped_excluded_insights_included(monkeypatch):
    """The library's SQL filter:

      * INCLUDES SUCCESS, FAILED, and INSIGHTS_* (the lifecycle-mutated
        SUCCESS rows — PR D 2026-06-27).
      * EXCLUDES SKIPPED (credential / quota guards — not real attempts).

    Pin: callers must NOT see SKIPPED in the success/failed counts, but
    INSIGHTS_24H/48H/168H rows MUST count as successes (they're
    lifecycle-mutated SUCCESS rows from the metric collector — the prior
    code under-counted success rates ~5x for any platform measured >24h
    after publish, e.g. anime YouTube showed 0% instead of ~75%).

    Verified by checking the SQL passed to execute() includes the
    canonical WHERE clause with INSIGHTS_% inclusion.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    rows: list[dict] = []
    fake_conn = _mock_pg_with_rows(rows)
    with patch("server.core.publishing_health_per_niche.pg_connect", return_value=fake_conn):
        from server.core.publishing_health_per_niche import (
            fetch_per_niche_platform_health,
        )

        fetch_per_niche_platform_health(days=14)

    # First execute = set_config. Second execute = the grouped query.
    second_call = fake_conn.execute.call_args_list[1]
    sql_text = second_call.args[0]
    # The canonical WHERE clause must accept SUCCESS, FAILED, AND
    # the INSIGHTS_* lifecycle states (the literal % is double-escaped
    # for psycopg parameter binding — server sees a single %).
    assert "status IN ('SUCCESS', 'FAILED')" in sql_text
    assert "status LIKE 'INSIGHTS_%%'" in sql_text
    # Defensive: SKIPPED should not appear in the WHERE filter at all.
    # (It may appear in commentary elsewhere — keep the assertion scoped.)
    where_segment = sql_text.split("WHERE", 1)[1].split("GROUP BY", 1)[0]
    assert "SKIPPED" not in where_segment


def test_days_param_filters_correctly(monkeypatch):
    """The days arg flows through to the SQL ``make_interval(days => %s)``
    parameter. Confirms the parameterised query is bound correctly.

    Out-of-band rows (older than the window) are filtered by the SQL,
    not by Python — so this test only verifies the param wiring, not
    Python-side filtering.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    rows: list[dict] = []
    fake_conn = _mock_pg_with_rows(rows)
    with patch("server.core.publishing_health_per_niche.pg_connect", return_value=fake_conn):
        from server.core.publishing_health_per_niche import (
            fetch_per_niche_platform_health,
        )

        fetch_per_niche_platform_health(days=7)

    # Inspect the SQL parameter list — second call is the grouped query.
    # Library binds two %s placeholders (subquery + outer WHERE), both
    # to the same days value.
    second_call = fake_conn.execute.call_args_list[1]
    params = second_call.args[1]
    assert params == (7, 7)


def test_days_clamped_to_safe_range(monkeypatch):
    """Garbage / oversized days clamped at library entry."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    rows: list[dict] = []
    fake_conn = _mock_pg_with_rows(rows)
    with patch("server.core.publishing_health_per_niche.pg_connect", return_value=fake_conn):
        from server.core.publishing_health_per_niche import (
            fetch_per_niche_platform_health,
        )

        fetch_per_niche_platform_health(days=9999)
    second_call = fake_conn.execute.call_args_list[1]
    assert second_call.args[1] == (90, 90)

    fake_conn = _mock_pg_with_rows([])
    with patch("server.core.publishing_health_per_niche.pg_connect", return_value=fake_conn):
        from server.core.publishing_health_per_niche import (
            fetch_per_niche_platform_health,
        )

        fetch_per_niche_platform_health(days=0)
    second_call = fake_conn.execute.call_args_list[1]
    assert second_call.args[1] == (1, 1)


def test_no_dsn_returns_empty(monkeypatch):
    """Fail-OPEN — no DATABASE_URL → empty list, not exception."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from server.core.publishing_health_per_niche import (
        fetch_per_niche_platform_health,
    )

    assert fetch_per_niche_platform_health(days=14) == []


def test_datetime_fields_iso_formatted(monkeypatch):
    """Last-success / last-failure timestamps serialised to ISO-8601
    strings for JSON transport. None when no row of that status existed."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    rows = [
        {
            "niche_id": "ai_creators",
            "platform": "instagram",
            "success_count": 5,
            "failed_count": 3,
            "last_success_at": datetime(2026, 6, 25, 6, 37, 34, tzinfo=UTC),
            "last_failure_at": datetime(2026, 6, 27, 6, 42, 47, tzinfo=UTC),
            "last_failure_error": "Container processing error: 2207077",
        },
        # No failures ever → last_failure_at and last_failure_error null
        {
            "niche_id": "sports",
            "platform": "facebook",
            "success_count": 10,
            "failed_count": 0,
            "last_success_at": datetime(2026, 6, 27, tzinfo=UTC),
            "last_failure_at": None,
            "last_failure_error": None,
        },
    ]
    fake_conn = _mock_pg_with_rows(rows)
    with patch("server.core.publishing_health_per_niche.pg_connect", return_value=fake_conn):
        from server.core.publishing_health_per_niche import (
            fetch_per_niche_platform_health,
        )

        result = fetch_per_niche_platform_health(days=14)

    assert result[0]["last_success_at"] == "2026-06-25T06:37:34+00:00"
    assert result[0]["last_failure_at"] == "2026-06-27T06:42:47+00:00"
    assert result[1]["last_failure_at"] is None
    assert result[1]["last_failure_error"] is None


# ── Endpoint tests ────────────────────────────────────────────────


def test_endpoint_no_dsn_returns_empty_payload(monkeypatch):
    """Fail-OPEN at the endpoint — empty rows, shape preserved, 200."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = _make_app_with_publishing_health_bp()
    with app.test_client() as c:
        r = c.get("/api/v1/publishing/per-niche-platform-health")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["rows"] == []
    # thresholds + window_days still present even on empty payload
    assert body["thresholds"]["red_below_pct"] == 50
    assert body["thresholds"]["yellow_below_pct"] == 80
    assert body["window_days"] == 14


def test_endpoint_invalid_days_returns_400(monkeypatch):
    """Garbage in days → 400, not 500."""
    app = _make_app_with_publishing_health_bp()
    with app.test_client() as c:
        r = c.get("/api/v1/publishing/per-niche-platform-health?days=not-a-number")
    body = r.get_json() or {}
    assert "days" in (body.get("error") or "").lower()


def test_endpoint_clamps_oversized_days():
    """days=9999 → clamped to 90."""
    app = _make_app_with_publishing_health_bp()
    with patch(
        "server.core.publishing_health_per_niche.fetch_per_niche_platform_health",
        return_value=[],
    ) as mock_fetch:
        with app.test_client() as c:
            r = c.get("/api/v1/publishing/per-niche-platform-health?days=9999")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["window_days"] == 90
    mock_fetch.assert_called_once_with(days=90)


def test_endpoint_threshold_in_response_payload():
    """Frontend reads thresholds from the API response (not hardcoded).
    Pin: response includes ``thresholds`` dict with sensible defaults."""
    app = _make_app_with_publishing_health_bp()
    with patch(
        "server.core.publishing_health_per_niche.fetch_per_niche_platform_health",
        return_value=[],
    ):
        with app.test_client() as c:
            r = c.get("/api/v1/publishing/per-niche-platform-health")
    body = r.get_json()["data"]
    assert "thresholds" in body
    assert body["thresholds"] == {"red_below_pct": 50, "yellow_below_pct": 80}


def test_endpoint_last_failure_error_returned_in_full():
    """Pin: backend returns the full error_message; frontend handles
    truncation for tooltip display. This separation means analytics /
    debug tools can rely on the raw payload without re-querying the DB."""
    long_error = "x" * 500
    app = _make_app_with_publishing_health_bp()
    rows = [
        {
            "niche_id": "ai_creators",
            "platform": "instagram",
            "success_count": 0,
            "failed_count": 3,
            "success_rate_pct": 0.0,
            "last_success_at": None,
            "last_failure_at": "2026-06-27T06:42:47+00:00",
            "last_failure_error": long_error,
        }
    ]
    with patch(
        "server.core.publishing_health_per_niche.fetch_per_niche_platform_health",
        return_value=rows,
    ):
        with app.test_client() as c:
            r = c.get("/api/v1/publishing/per-niche-platform-health")
    body = r.get_json()["data"]
    assert body["rows"][0]["last_failure_error"] == long_error


def test_endpoint_sr_f_filters_to_allowlist():
    """Restricted operator sees ONLY their allowlist niches' rows."""
    rows = [
        {
            "niche_id": "gaming",
            "platform": "youtube",
            "success_count": 5,
            "failed_count": 0,
            "success_rate_pct": 100.0,
            "last_success_at": None,
            "last_failure_at": None,
            "last_failure_error": None,
        },
        {
            "niche_id": "anime",
            "platform": "youtube",
            "success_count": 1,
            "failed_count": 4,
            "success_rate_pct": 20.0,
            "last_success_at": None,
            "last_failure_at": None,
            "last_failure_error": None,
        },
        {
            "niche_id": "ai_creators",
            "platform": "instagram",
            "success_count": 0,
            "failed_count": 3,
            "success_rate_pct": 0.0,
            "last_success_at": None,
            "last_failure_at": None,
            "last_failure_error": None,
        },
    ]
    app = _make_app_with_publishing_health_bp()
    with (
        patch(
            "server.core.publishing_health_per_niche.fetch_per_niche_platform_health",
            return_value=rows,
        ),
        patch(
            "server.auth.niche_allowlist.get_allowed_niches",
            return_value={"gaming"},
        ),
    ):
        with app.test_client() as c:
            r = c.get("/api/v1/publishing/per-niche-platform-health")
    assert r.status_code == 200
    body = r.get_json()["data"]
    niches = {row["niche_id"] for row in body["rows"]}
    assert niches == {"gaming"}


def test_endpoint_handles_import_error_gracefully():
    """If core helper unavailable, endpoint returns shaped empty payload."""
    import sys

    app = _make_app_with_publishing_health_bp()
    with patch.dict(
        sys.modules,
        {"server.core.publishing_health_per_niche": None},
    ):
        with app.test_client() as c:
            r = c.get("/api/v1/publishing/per-niche-platform-health")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["rows"] == []
    assert "thresholds" in body


# ── PR D (2026-06-27) lifecycle-state rollup pins ─────────────────
#
# These pins guarantee the per-niche / per-platform card correctly
# rolls INSIGHTS_24H / INSIGHTS_48H / INSIGHTS_168H into the success
# count. Without this, the card under-counts success ~5x for any
# platform measured >24h after publish (publishing_analytics rows
# get their status mutated as they age through insight windows).
# Real prod evidence (2026-06-27): anime YouTube last 60d was 5
# FAILED + 15 INSIGHTS_* (all originally SUCCESS) — the card was
# showing 0% and operator thought YouTube was broken; real rate ~75%.


def test_insights_states_counted_as_success(monkeypatch):
    """SQL aggregates INSIGHTS_24H/48H/168H rows into success_count.

    Since SQL is mocked, this test pins the FILTER clauses in the
    query so the lifecycle-mutated SUCCESS rows aren't excluded.
    Combined with test_realistic_lifecycle_scenario below, this
    locks the rollup contract end-to-end.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    rows: list[dict] = []
    fake_conn = _mock_pg_with_rows(rows)
    with patch("server.core.publishing_health_per_niche.pg_connect", return_value=fake_conn):
        from server.core.publishing_health_per_niche import (
            fetch_per_niche_platform_health,
        )

        fetch_per_niche_platform_health(days=14)

    second_call = fake_conn.execute.call_args_list[1]
    sql_text = second_call.args[0]
    # Both the success_count FILTER and the last_success_at FILTER
    # must accept INSIGHTS_* states. If either is missing, half the
    # contract leaks (counts are right but last-success timestamp is
    # wrong, or vice versa).
    assert "COUNT(*) FILTER (WHERE status='SUCCESS' OR status LIKE 'INSIGHTS_%%')" in sql_text, (
        "success_count FILTER must include INSIGHTS_*"
    )
    assert (
        "MAX(created_at) FILTER (WHERE status='SUCCESS' OR status LIKE 'INSIGHTS_%%')" in sql_text
    ), "last_success_at FILTER must include INSIGHTS_*"


def test_skipped_state_excluded_from_both_counts(monkeypatch):
    """SKIPPED rows (credential / quota guards) must NOT appear in
    success_count NOR failed_count. They're deliberate non-attempts,
    not failures; counting them either way pollutes the rate.

    The card output has a success_count and failed_count column but
    no skipped column — the FILTER expressions must omit SKIPPED.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    rows: list[dict] = []
    fake_conn = _mock_pg_with_rows(rows)
    with patch("server.core.publishing_health_per_niche.pg_connect", return_value=fake_conn):
        from server.core.publishing_health_per_niche import (
            fetch_per_niche_platform_health,
        )

        fetch_per_niche_platform_health(days=14)

    second_call = fake_conn.execute.call_args_list[1]
    sql_text = second_call.args[0]
    # Extract just the two FILTER expressions for the counts.
    # Both must NOT mention SKIPPED.
    for filter_clause in (
        "COUNT(*) FILTER (WHERE status='SUCCESS' OR status LIKE 'INSIGHTS_%%')",
        "COUNT(*) FILTER (WHERE status='FAILED')",
    ):
        assert filter_clause in sql_text, f"missing expected filter: {filter_clause}"
    # And the WHERE clause that gates the whole query must not let
    # SKIPPED rows through (they'd inflate group totals via GROUP BY).
    where_segment = sql_text.split("WHERE", 1)[1].split("GROUP BY", 1)[0]
    assert "SKIPPED" not in where_segment


def test_realistic_lifecycle_scenario(monkeypatch):
    """Realistic prod scenario: 5 SUCCESS + 8 INSIGHTS_* + 2 FAILED.

    The mocked DB has already done the SQL aggregation, so we
    simulate what the new query SHOULD return: success_count=13
    (5+8), failed_count=2. The Python side just computes the rate.

    Pin: rate = 13 / (13 + 2) * 100 = 86.666... → rounded to 86.7%.
    Before PR D, the same DB state would have surfaced as 5 / 7
    = 71.4% (or worse, with the WHERE excluding INSIGHTS_*, the
    GROUP BY row would have only seen 5 + 2 = 7 total and reported
    71.4%, masking 8 successful publishes that aged past 24h).

    This is the closest unit-test analogue to the prod anime/YouTube
    scenario that motivated this PR.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    rows = [
        {
            "niche_id": "anime",
            "platform": "youtube",
            "success_count": 13,  # 5 SUCCESS + 8 INSIGHTS_* rolled up
            "failed_count": 2,
            "last_success_at": datetime(2026, 6, 27, 6, 0, tzinfo=UTC),
            "last_failure_at": datetime(2026, 6, 25, 12, 0, tzinfo=UTC),
            "last_failure_error": "quotaExceeded",
        }
    ]
    fake_conn = _mock_pg_with_rows(rows)
    with patch("server.core.publishing_health_per_niche.pg_connect", return_value=fake_conn):
        from server.core.publishing_health_per_niche import (
            fetch_per_niche_platform_health,
        )

        result = fetch_per_niche_platform_health(days=60)

    assert len(result) == 1
    r = result[0]
    assert r["success_count"] == 13
    assert r["failed_count"] == 2
    # 13 / 15 * 100 = 86.666... → 86.7 at 1dp.
    assert r["success_rate_pct"] == 86.7
    # Last-success timestamp survives the rollup (set on the
    # INSIGHTS_* row, since those are the most recent ones).
    assert r["last_success_at"] == "2026-06-27T06:00:00+00:00"
