"""``check_publish_silence`` catches the zero-publish outage class.

Post-Wave-4 audit (2026-06-17) found that ``check_publish_failures``
only triggers on ≥5 FAILED in 24h, leaving full pipeline outages that
produce no attempts at all unmonitored. Prod confirmed: 2026-06-14/15/16
had 1+0+1 publishes and zero alerts fired.

This test pins the two-tier alert (warning at 24h, critical at 48h)
and the SUCCESS-allowlist that mirrors ``publishing/daily_cap``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.monitoring.health_monitor import check_publish_silence


def _conn_returning(in_24h: int, in_48h: int) -> MagicMock:
    """Build a fake psycopg connection whose cursor returns the given counts."""
    cur = MagicMock()
    cur.fetchone.return_value = (in_24h, in_48h)
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn


def _patch_connect(conn):
    return patch(
        "psycopg.connect",
        return_value=conn,
    )


class TestPublishSilence:
    def test_publishes_in_last_24h_no_alert(self):
        """Healthy niche — at least one publish in last 24h. No alert."""
        with _patch_connect(_conn_returning(in_24h=3, in_48h=5)):
            alerts = check_publish_silence("gaming")
        assert alerts == []

    def test_zero_in_24h_but_present_in_48h_warning(self):
        """24h gap → warning. Could be a slow operator queue or a
        publisher that just self-healed; surface but don't escalate."""
        with _patch_connect(_conn_returning(in_24h=0, in_48h=2)):
            alerts = check_publish_silence("gaming")
        assert len(alerts) == 1
        assert alerts[0].severity == "warning"
        assert alerts[0].check == "publish_silence"
        assert alerts[0].niche_id == "gaming"
        assert "24 hours" in alerts[0].message

    def test_zero_in_48h_critical(self):
        """48h gap → critical. By then launchd has fired the publisher
        7-10 times and produced nothing. Unambiguous breakage."""
        with _patch_connect(_conn_returning(in_24h=0, in_48h=0)):
            alerts = check_publish_silence("anime")
        assert len(alerts) == 1
        assert alerts[0].severity == "critical"
        assert alerts[0].check == "publish_silence"
        assert alerts[0].niche_id == "anime"
        assert "48 hours" in alerts[0].message
        assert "silently dead" in alerts[0].message

    def test_critical_not_duplicated_as_warning(self):
        """48h critical supersedes the 24h warning — only ONE alert."""
        with _patch_connect(_conn_returning(in_24h=0, in_48h=0)):
            alerts = check_publish_silence("movies")
        assert len(alerts) == 1, "critical should not double-fire as warning too"

    def test_details_carry_observed_counts(self):
        """Operator needs the raw numbers to decide whether to dig in.
        Both windows surface in details so the dashboard alert card
        renders them."""
        with _patch_connect(_conn_returning(in_24h=0, in_48h=1)):
            alerts = check_publish_silence("sports")
        assert alerts[0].details == {"publishes_24h": 0, "publishes_48h": 1}

    def test_db_failure_returns_empty_does_not_raise(self):
        """Monitoring checks fail-open — a transient DB hiccup must
        never crash the health_monitor sweep. Returns [], logs at
        DEBUG (same pattern as ``check_publish_failures``)."""
        import psycopg

        with patch(
            "psycopg.connect",
            side_effect=psycopg.OperationalError("conn refused"),
        ):
            alerts = check_publish_silence("gaming")
        assert alerts == []

    def test_sql_filters_by_niche_id(self):
        """The query must be scoped to ``niche_id`` — otherwise a
        publish in niche A would silence the publish_silence alert
        for niche B."""
        cur = MagicMock()
        cur.fetchone.return_value = (1, 1)
        captured_params: list = []

        def execute(sql, params=None):
            captured_params.append((sql, params))

        cur.execute = execute
        conn = MagicMock()
        conn.cursor.return_value = cur

        with _patch_connect(conn):
            check_publish_silence("anime")

        # Find the SELECT call
        select_calls = [(s, p) for s, p in captured_params if "SELECT" in s]
        assert select_calls, "must run a SELECT"
        sql, params = select_calls[0]
        assert "niche_id = %s" in sql
        assert params == ("anime",)

    def test_sql_allowlists_post_publish_states(self):
        """status flips to INSIGHTS_6H/24H/48H/168H after publish (R-09 /
        PR #220). The allowlist must include them — otherwise a publish
        from 8h ago would look like silence to this check."""
        cur = MagicMock()
        cur.fetchone.return_value = (0, 0)
        captured = []

        def execute(sql, params=None):
            captured.append(sql)

        cur.execute = execute
        conn = MagicMock()
        conn.cursor.return_value = cur

        with _patch_connect(conn):
            check_publish_silence("gaming")

        select_sql = next(s for s in captured if "SELECT" in s)
        for expected in ("'SUCCESS'", "'INSIGHTS_6H'", "'INSIGHTS_24H'", "'INSIGHTS_48H'"):
            assert expected in select_sql, (
                f"status allowlist must include {expected} — "
                "post-publish metric collector flips statuses ~5h later"
            )
