"""Tests for genlab_core.engagement.token_health.

Pins fix #3 of the autonomy roadmap: token-expiry visibility for the
engagement pollers. Pre-fix, expired tokens silently logged WARN and
the engagement engine went dark for 22 days. Post-fix, the same event
writes a CRITICAL pipeline_alert with the literal env var to refresh.

Env var handling uses patch.dict (not monkeypatch.setenv) so the
DATABASE_URL set inside the same context manager as the psycopg.connect
patch — pre-fix CI was flaky because conftest.py pops DATABASE_URL at
module import and the monkeypatch ordering was different on CI than
locally.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from genlab_core.engagement.token_health import (
    emit_token_expiry_alert,
    is_oauth_expiry,
)


def _mock_conn(
    *, existing_row: bool = False, fetchone_side_effect: list | None = None
) -> MagicMock:
    """Build a context-manager-aware mock psycopg connection.

    ``existing_row=True`` makes fetchone return a truthy tuple so the
    function takes the "alert already exists" branch.
    ``fetchone_side_effect`` overrides for tests that need different
    fetchone behaviour on successive calls.
    """
    mock_cur = MagicMock()
    if fetchone_side_effect is not None:
        mock_cur.fetchone.side_effect = fetchone_side_effect
    elif existing_row:
        mock_cur.fetchone.return_value = (42,)
    else:
        mock_cur.fetchone.return_value = None

    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.__exit__.return_value = None
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_conn.cursor.return_value.__exit__.return_value = None
    return mock_conn, mock_cur


class TestIsOAuthExpiry:
    def test_meta_oauth_exception_code_190_detected(self):
        body = (
            '{"error":{"message":"Error validating access token: Session has '
            "expired on Tuesday, 26-May-26 08:49:34 PDT. The current time is "
            'Friday, 12-Jun-26 20:25:54 PDT.","type":"OAuthException","code":190,'
            '"error_subcode":0,"fbtrace_id":"AH4ZiBKxvCYZA_PutmrRREd"}}'
        )
        assert is_oauth_expiry(body) is True

    def test_oauth_exception_type_without_code_detected(self):
        body = '{"error":{"message":"bad token","type":"OAuthException"}}'
        assert is_oauth_expiry(body) is True

    def test_session_expired_in_message_detected(self):
        body = "Session has expired on yesterday"
        assert is_oauth_expiry(body) is True

    def test_plain_500_not_detected(self):
        body = '{"error":{"message":"Internal server error","type":"GraphMethodException"}}'
        assert is_oauth_expiry(body) is False

    def test_empty_body_safe(self):
        assert is_oauth_expiry("") is False
        assert is_oauth_expiry(None) is False  # type: ignore[arg-type]

    def test_malformed_json_safe(self):
        assert is_oauth_expiry("{not json") is False


class TestEmitTokenExpiryAlert:
    def test_writes_critical_row_to_pipeline_alerts(self):
        mock_conn, mock_cur = _mock_conn()
        with (
            patch.dict("os.environ", {"DATABASE_URL": "postgresql://test"}, clear=False),
            patch("psycopg.connect", return_value=mock_conn),
        ):
            ok = emit_token_expiry_alert("threads", "gaming", '{"error":{"code":190}}')

        assert ok is True
        insert_calls = [c for c in mock_cur.execute.call_args_list if "INSERT" in c.args[0]]
        assert len(insert_calls) == 1
        sql, params = insert_calls[0].args
        assert "INSERT INTO pipeline_alerts" in sql
        assert params[0] == "gaming"
        assert params[1] == "token_expired"
        assert params[2] == "critical"
        assert "CRITICALRUSH_THREADS_ACCESS_TOKEN" in params[3]

    def test_second_call_skipped_via_db_dedup(self):
        """The DB-side SELECT-against-unresolved check is the only throttle.
        First call inserts (fetchone=None); second call hits the existing-row
        check (fetchone returns a row) and skips."""
        mock_conn, mock_cur = _mock_conn(fetchone_side_effect=[None, (123,)])

        with (
            patch.dict("os.environ", {"DATABASE_URL": "postgresql://test"}, clear=False),
            patch("psycopg.connect", return_value=mock_conn),
        ):
            first = emit_token_expiry_alert("threads", "anime", "{}")
            second = emit_token_expiry_alert("threads", "anime", "{}")

        assert first is True
        assert second is False

    def test_skips_when_unresolved_alert_exists(self):
        """If pipeline_alerts already has an unresolved row, don't duplicate."""
        mock_conn, mock_cur = _mock_conn(existing_row=True)

        with (
            patch.dict("os.environ", {"DATABASE_URL": "postgresql://test"}, clear=False),
            patch("psycopg.connect", return_value=mock_conn),
        ):
            ok = emit_token_expiry_alert("threads", "sports", "{}")

        assert ok is False
        insert_calls = [c for c in mock_cur.execute.call_args_list if "INSERT" in c.args[0]]
        assert insert_calls == []

    def test_safe_when_no_database_url(self):
        # patch.dict with clear=False but no DATABASE_URL key, AND
        # explicitly delete DATABASE_URL after the dict patch applies.
        # Use a context-managed env mutation that's CI-deterministic.
        with patch.dict("os.environ", {}, clear=False):
            # Ensure DATABASE_URL not present in this context.
            import os as _os

            _os.environ.pop("DATABASE_URL", None)
            ok = emit_token_expiry_alert("threads", "movies", "{}")
        assert ok is False

    def test_safe_when_psycopg_raises(self):
        """Alert emission must never crash the poll loop."""
        with (
            patch.dict("os.environ", {"DATABASE_URL": "postgresql://test"}, clear=False),
            patch("psycopg.connect", side_effect=RuntimeError("db gone")),
        ):
            ok = emit_token_expiry_alert("threads", "ai_creators", "{}")
        assert ok is False


class TestEnvVarHintsPerPlatform:
    """The alert message must carry the literal env var the operator needs
    to update. Pin the prefix map so a niche rename doesn't silently lose
    the hint."""

    @pytest.mark.parametrize(
        "niche_id,platform,expected_substring",
        [
            ("gaming", "threads", "CRITICALRUSH_THREADS_ACCESS_TOKEN"),
            ("sports", "threads", "CLUTCHWIRE_THREADS_ACCESS_TOKEN"),
            ("movies", "threads", "SPLICEREEL_THREADS_ACCESS_TOKEN"),
            ("anime", "threads", "FRAMEDRIFT_THREADS_ACCESS_TOKEN"),
            ("ai_creators", "threads", "BLACKBOXBRIEF_THREADS_ACCESS_TOKEN"),
            ("ai_tech", "threads", "BLACKBOXBRIEF_THREADS_ACCESS_TOKEN"),
            ("gaming", "facebook", "CRITICALRUSH_FB_PAGE_ACCESS_TOKEN"),
            ("gaming", "x_twitter", "CRITICALRUSH_X_ACCESS_TOKEN"),
            ("unknown_niche", "threads", "UNKNOWN_NICHE_THREADS_ACCESS_TOKEN"),
        ],
    )
    def test_env_var_in_message(self, niche_id, platform, expected_substring):
        mock_conn, mock_cur = _mock_conn()
        with (
            patch.dict("os.environ", {"DATABASE_URL": "postgresql://test"}, clear=False),
            patch("psycopg.connect", return_value=mock_conn),
        ):
            emit_token_expiry_alert(platform, niche_id, "{}")

        insert_calls = [c for c in mock_cur.execute.call_args_list if "INSERT" in c.args[0]]
        assert len(insert_calls) == 1
        params = insert_calls[0].args[1]
        assert expected_substring in params[3]
