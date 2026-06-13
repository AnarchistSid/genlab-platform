"""Tests for genlab_core.engagement.token_health.

Pins fix #3 of the autonomy roadmap: token-expiry visibility for the
engagement pollers. Pre-fix, expired tokens silently logged WARN and
the engagement engine went dark for 22 days. Post-fix, the same event
writes a CRITICAL pipeline_alert with the literal env var to refresh.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from genlab_core.engagement.token_health import (
    emit_token_expiry_alert,
    is_oauth_expiry,
    reset_throttle,
)


@pytest.fixture(autouse=True)
def clear_throttle():
    """Each test starts with a clean throttle cache."""
    reset_throttle()
    yield
    reset_throttle()


class TestIsOAuthExpiry:
    def test_meta_oauth_exception_code_190_detected(self):
        """The literal shape from Meta when a Threads token expires."""
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
        """A free-form Exception.__str__ may not be valid JSON but should
        still trip when the canonical phrase is present."""
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
    def test_writes_critical_row_to_pipeline_alerts(self, monkeypatch):
        """Happy path: token expired → row inserted with severity=critical."""
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None  # no existing unresolved alert
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = None
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_conn.cursor.return_value.__exit__.return_value = None

        monkeypatch.setenv("DATABASE_URL", "postgresql://test")

        with patch("psycopg.connect", return_value=mock_conn):
            ok = emit_token_expiry_alert("threads", "gaming", '{"error":{"code":190}}')

        assert ok is True
        # The INSERT was made with the niche_id + check_name + severity=critical
        insert_calls = [c for c in mock_cur.execute.call_args_list if "INSERT" in c.args[0]]
        assert len(insert_calls) == 1
        sql, params = insert_calls[0].args
        assert "INSERT INTO pipeline_alerts" in sql
        assert params[0] == "gaming"
        assert params[1] == "token_expired"
        assert params[2] == "critical"
        # Message embeds the literal env var name for the operator.
        assert "CRITICALRUSH_THREADS_ACCESS_TOKEN" in params[3]

    def test_throttle_suppresses_second_call_same_key(self, monkeypatch):
        """Re-emit at most once per (platform, niche_id) per throttle window
        so a 10-min poller doesn't carpet-bomb pipeline_alerts."""
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = None
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_conn.cursor.return_value.__exit__.return_value = None

        monkeypatch.setenv("DATABASE_URL", "postgresql://test")

        with patch("psycopg.connect", return_value=mock_conn):
            first = emit_token_expiry_alert("threads", "anime", "{}")
            second = emit_token_expiry_alert("threads", "anime", "{}")

        assert first is True
        assert second is False

    def test_skips_when_unresolved_alert_exists(self, monkeypatch):
        """If pipeline_alerts already has an unresolved row for this
        (check_name, niche_id), don't duplicate it."""
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (123,)  # existing unresolved row
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = None
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_conn.cursor.return_value.__exit__.return_value = None

        monkeypatch.setenv("DATABASE_URL", "postgresql://test")

        with patch("psycopg.connect", return_value=mock_conn):
            ok = emit_token_expiry_alert("threads", "sports", "{}")

        assert ok is False
        # No INSERT performed.
        insert_calls = [c for c in mock_cur.execute.call_args_list if "INSERT" in c.args[0]]
        assert insert_calls == []

    def test_safe_when_no_database_url(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        ok = emit_token_expiry_alert("threads", "movies", "{}")
        assert ok is False  # but no exception

    def test_safe_when_psycopg_raises(self, monkeypatch):
        """Alert emission must never crash the poll loop."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://test")
        with patch("psycopg.connect", side_effect=RuntimeError("db gone")):
            ok = emit_token_expiry_alert("threads", "ai_creators", "{}")
        assert ok is False


class TestEnvVarHintsPerPlatform:
    """The alert message must carry the literal env var the operator needs
    to update — generic "refresh the token" messages are what slow operators
    down at 2am. Pin the prefix map so a niche rename doesn't silently lose
    the hint."""

    @pytest.mark.parametrize(
        "niche_id,platform,expected_substring",
        [
            ("gaming", "threads", "CRITICALRUSH_THREADS_ACCESS_TOKEN"),
            ("sports", "threads", "CLUTCHWIRE_THREADS_ACCESS_TOKEN"),
            ("movies", "threads", "SPLICEREEL_THREADS_ACCESS_TOKEN"),
            ("anime", "threads", "FRAMEDRIFT_THREADS_ACCESS_TOKEN"),
            ("ai_creators", "threads", "BLACKBOXBRIEF_THREADS_ACCESS_TOKEN"),
            ("ai_tech", "threads", "BLACKBOXBRIEF_THREADS_ACCESS_TOKEN"),  # alias
            ("gaming", "facebook", "CRITICALRUSH_FB_PAGE_ACCESS_TOKEN"),
            ("gaming", "x_twitter", "CRITICALRUSH_X_ACCESS_TOKEN"),
            ("unknown_niche", "threads", "UNKNOWN_NICHE_THREADS_ACCESS_TOKEN"),
        ],
    )
    def test_env_var_in_message(self, monkeypatch, niche_id, platform, expected_substring):
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = None
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_conn.cursor.return_value.__exit__.return_value = None

        monkeypatch.setenv("DATABASE_URL", "postgresql://test")

        with patch("psycopg.connect", return_value=mock_conn):
            emit_token_expiry_alert(platform, niche_id, "{}")

        insert_calls = [c for c in mock_cur.execute.call_args_list if "INSERT" in c.args[0]]
        assert len(insert_calls) == 1
        params = insert_calls[0].args[1]
        # Message is index 3 in the INSERT param tuple
        assert expected_substring in params[3]
