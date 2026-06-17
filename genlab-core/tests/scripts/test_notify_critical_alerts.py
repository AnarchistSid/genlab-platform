"""Tests for scripts/notify_critical_alerts.py — pure logic only.

The DB-touching paths (fetch_pending, mark_notified) are not covered
here; they get exercised end-to-end via the 5-min systemd timer
against the live DB. The covered surface is the webhook POST shape +
the idempotency contract + the CLI no-op path.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "notify_critical_alerts.py"
spec = importlib.util.spec_from_file_location("notify_critical_alerts", SCRIPT)
mod = importlib.util.module_from_spec(spec)
sys.modules["notify_critical_alerts"] = mod
spec.loader.exec_module(mod)


class TestPostToWebhook:
    def test_2xx_returns_true(self):
        class _Resp:
            status_code = 200
            text = "ok"

        with (
            patch.object(mod, "_connect"),
            patch.dict(sys.modules, {"requests": _FakeRequests(_Resp())}),
        ):
            result = mod.post_to_webhook("https://example.com", _sample_alert())
        assert result is True

    def test_5xx_returns_false_and_logs(self):
        class _Resp:
            status_code = 503
            text = "Service Unavailable"

        with (
            patch.object(mod, "_connect"),
            patch.dict(sys.modules, {"requests": _FakeRequests(_Resp())}),
        ):
            result = mod.post_to_webhook("https://example.com", _sample_alert())
        assert result is False

    def test_network_exception_returns_false(self):
        with patch.dict(sys.modules, {"requests": _FakeRequests(raise_exc=ConnectionError("net"))}):
            result = mod.post_to_webhook("https://example.com", _sample_alert())
        assert result is False

    def test_payload_includes_title_body_and_id(self):
        captured = {}

        class _Resp:
            status_code = 200
            text = ""

        with patch.dict(
            sys.modules,
            {"requests": _FakeRequests(_Resp(), captured_payload=captured)},
        ):
            mod.post_to_webhook("https://example.com", _sample_alert())

        # Slack-compatible "text" key
        assert "text" in captured
        text = captured["text"]
        assert "future_stale_visual_paths" in text
        assert "movies" in text
        assert "alert id" in text
        # ID should be in the body (truncated form is fine)
        assert "abc-123" in text

    def test_message_truncated_to_2000_chars(self):
        long_alert = _sample_alert(message="x" * 5000)
        captured = {}

        class _Resp:
            status_code = 200
            text = ""

        with patch.dict(
            sys.modules,
            {"requests": _FakeRequests(_Resp(), captured_payload=captured)},
        ):
            mod.post_to_webhook("https://example.com", long_alert)

        # Body is trimmed; total text under 2500 (title + body + footer)
        assert len(captured["text"]) < 2500
        # Should have at most 2000 x's
        assert captured["text"].count("x") <= 2000


class TestMainCLI:
    def test_no_webhook_url_is_noop(self, monkeypatch):
        """Most important contract: when GENLAB_ALERT_WEBHOOK_URL is
        unset the script MUST exit 0 and NOT touch the DB."""
        monkeypatch.delenv("GENLAB_ALERT_WEBHOOK_URL", raising=False)
        connect_called = {"n": 0}

        def boom_connect():
            connect_called["n"] += 1
            raise RuntimeError("should not be called when webhook unset")

        monkeypatch.setattr(mod, "_connect", boom_connect)
        rc = mod.main([])
        assert rc == 0
        assert connect_called["n"] == 0

    def test_db_error_returns_1(self, monkeypatch):
        monkeypatch.setenv("GENLAB_ALERT_WEBHOOK_URL", "https://example.com")

        def boom():
            raise RuntimeError("DATABASE_URL not set")

        monkeypatch.setattr(mod, "_connect", boom)
        rc = mod.main([])
        assert rc == 1


# ── Helpers ──────────────────────────────────────────────────────────


def _sample_alert(**overrides):
    base = {
        "id": "abc-123-def",
        "niche_id": "movies",
        "check_name": "future_stale_visual_paths",
        "message": "Future-scheduled blueprint abc-123 (movies) will fail at 2026-06-19...",
        "created_at": None,
    }
    base.update(overrides)
    return base


class _FakeRequests:
    """Minimal stand-in for the `requests` module."""

    def __init__(self, response=None, raise_exc=None, captured_payload=None):
        self._response = response
        self._raise = raise_exc
        self._captured = captured_payload

    def post(self, url, json=None, timeout=None):
        if self._raise:
            raise self._raise
        if self._captured is not None:
            self._captured.update(json or {})
        return self._response


# ── fetch_pending: severity case-insensitivity (post-Wave-4 audit bug #1) ──


class TestFetchPendingILike:
    """Verify the severity filter is case-insensitive.

    The audit found prod had 3 lowercase + 3 uppercase 'critical' rows,
    and the old ``severity = 'CRITICAL'`` predicate silently dropped half.
    The fix (``ILIKE 'critical'``) catches both casings — pin that here
    so a future "tidy-up" edit can't quietly regress.
    """

    def _fake_conn_capturing_sql(self, captured: list[str]):
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.fetchall.return_value = []  # no rows

        def execute(sql, params=None):
            captured.append(sql)

        cur.execute = execute
        # Cursor context manager
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)

        conn = MagicMock()
        conn.cursor.return_value = cur
        return conn

    def test_query_uses_ilike_severity(self):
        captured: list[str] = []
        conn = self._fake_conn_capturing_sql(captured)
        mod.fetch_pending(conn, lookback_hours=24)

        # The SELECT (second execute call after the SET) must use ILIKE
        select_sqls = [s for s in captured if "SELECT" in s]
        assert select_sqls, "fetch_pending must run a SELECT"
        select_sql = select_sqls[0]
        assert "ILIKE 'critical'" in select_sql, (
            "severity match must be case-insensitive (ILIKE 'critical') — "
            "the audit found 4 of 6 alert writers emit lowercase 'critical' "
            "and the previous '= CRITICAL' silently dropped them"
        )
        assert "= 'CRITICAL'" not in select_sql, (
            "regression guard: the old case-sensitive predicate must not "
            "reappear via a tidy-up edit"
        )
