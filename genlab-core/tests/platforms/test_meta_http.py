"""Pin tests for genlab_core.platforms.meta_http.

Guards the two invariants that make items A + B of the 2026-07-22
Meta anti-fingerprint pack real:

  B: every session created by `get_shared_session()` MUST have a
     non-empty, identifying User-Agent header. This is what stops
     Meta's WAF from fingerprinting us as anonymous bot traffic.

  A: every Meta response with X-App-Usage / X-Business-Use-Case-Usage
     headers MUST be reachable via `extract_usage(response)` for
     upstream logging. Without this, we lose the rate-limit
     telemetry that turns Meta enforcement observable.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from genlab_core.platforms.meta_http import (
    _capture_usage_headers,
    _get_user_agent,
    extract_usage,
    get_shared_session,
)


class TestUserAgent:
    def test_default_ua_is_identifying(self) -> None:
        """UA must be non-empty, identifiable, and link to the project.
        Bare `requests` sends no UA — that's the fingerprint we're
        eliminating."""
        ua = _get_user_agent()
        assert ua
        assert "GenLab" in ua
        assert "github.com" in ua or "aspirehub" in ua  # some contact/link

    def test_env_override(self, monkeypatch) -> None:
        """Operator can swap UA via env without a process restart on
        next session creation."""
        monkeypatch.setenv("GENLAB_META_USER_AGENT", "CustomAgent/2.0")
        assert _get_user_agent() == "CustomAgent/2.0"

    def test_session_carries_user_agent(self) -> None:
        """Every session MUST have the UA header pre-set.
        This is the contract that facebook.py / instagram.py /
        threads.py depend on."""
        session = get_shared_session()
        assert session.headers.get("User-Agent"), (
            "get_shared_session() returned a session with no User-Agent — "
            "adopters will identify as anonymous Python-urllib to Meta"
        )
        assert "GenLab" in session.headers["User-Agent"]

    def test_session_ua_survives_request_kwargs(self) -> None:
        """When a caller passes their own headers={...}, the UA from
        the session default must still be present (requests library
        merges session defaults with per-request headers)."""
        session = get_shared_session()
        # requests.Session.get(headers={...}) merges with session headers
        prepared = session.prepare_request(
            __import__("requests").Request(
                "GET",
                "https://example.com",
                headers={"X-Custom": "test"},
            )
        )
        assert "User-Agent" in prepared.headers
        assert "GenLab" in prepared.headers["User-Agent"]
        assert prepared.headers.get("X-Custom") == "test"


class TestUsageCapture:
    def _make_response_with_headers(self, **hdrs) -> object:
        """Real object (not MagicMock) so `getattr` returns None when
        the attribute genuinely wasn't set — MagicMock auto-creates
        attributes on access which defeats the "not attached" contract."""
        class _FakeResp:
            def __init__(self, h):
                self.headers = h
        return _FakeResp(hdrs)

    def test_capture_hook_attaches_usage_dict(self) -> None:
        """Response hook attaches parsed usage headers as
        `_genlab_meta_usage` attribute for upstream extraction."""
        resp = self._make_response_with_headers(
            **{
                "X-App-Usage": '{"call_count":42,"total_cputime":13}',
                "X-Business-Use-Case-Usage": '{"page-id":{"call_count":5}}',
            }
        )
        _capture_usage_headers(resp)
        usage = extract_usage(resp)
        assert usage is not None
        assert usage["x_app_usage"] == '{"call_count":42,"total_cputime":13}'
        assert '"page-id"' in usage["x_business_use_case_usage"]

    def test_capture_returns_response_unchanged(self) -> None:
        """Hook must return the response object — requests library
        passes it through the hook chain."""
        resp = self._make_response_with_headers(**{"X-App-Usage": "{}"})
        returned = _capture_usage_headers(resp)
        assert returned is resp

    def test_capture_no_usage_headers_returns_none(self) -> None:
        """Non-Meta responses (or Meta responses without usage headers)
        must not attach the attribute — extract_usage returns None
        cleanly."""
        resp = self._make_response_with_headers()  # no usage headers
        _capture_usage_headers(resp)
        assert extract_usage(resp) is None

    def test_capture_fails_open_on_broken_headers(self) -> None:
        """If .headers raises for some reason (weird response mock,
        library changes), hook must NOT propagate the exception.
        Real response must still be returned."""
        resp = MagicMock()
        # Make .headers access raise
        type(resp).headers = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
        # Must not raise
        returned = _capture_usage_headers(resp)
        assert returned is resp

    def test_session_hook_is_wired(self) -> None:
        """The session returned by get_shared_session must have the
        capture hook installed on the 'response' event — without this
        the entire A pipeline is silently dead."""
        session = get_shared_session()
        hooks = session.hooks.get("response") or []
        assert _capture_usage_headers in hooks, (
            "Response hook not installed — X-App-Usage headers won't "
            "be captured; the A observability path is dead"
        )


class TestExtractUsage:
    def test_no_attribute_returns_none(self) -> None:
        """Bare response (no hook fired) must not raise."""
        resp = MagicMock(spec=[])  # spec=[] means no attributes
        assert extract_usage(resp) is None


class TestHookLogsUsage:
    """Pin the 2026-07-22 wire-fix — hook must LOG usage at INFO
    (not just attach to response). Callers no longer need explicit
    _log_usage_if_present() invocations."""

    def _make_response(self, **hdrs) -> object:
        class _FakeResp:
            def __init__(self, h):
                self.headers = h
                self.url = "https://graph.facebook.com/v22.0/12345/videos"
                self.status_code = 200
        return _FakeResp(hdrs)

    def test_hook_emits_info_log_when_usage_present(self, caplog) -> None:
        import logging as _logging
        resp = self._make_response(
            **{"X-App-Usage": '{"call_count":42,"total_cputime":13}'}
        )
        with caplog.at_level(_logging.INFO, logger="genlab_core.platforms.meta_http"):
            _capture_usage_headers(resp)
        info = [r for r in caplog.records if r.levelno == _logging.INFO]
        assert any("meta_usage" in r.message for r in info), (
            f"Expected [meta_usage] INFO log, got: {[r.message for r in caplog.records]}"
        )
        # URL and status should be in the log
        msg = next(r.message for r in info if "meta_usage" in r.message)
        assert "graph.facebook.com" in msg
        assert "status=200" in msg
        assert "call_count" in msg  # X-App-Usage payload leaked in

    def test_hook_does_not_log_when_no_usage_headers(self, caplog) -> None:
        """Non-Meta responses (no usage headers) must not emit log
        lines — otherwise we'd flood on every non-Meta HTTP call."""
        import logging as _logging
        resp = self._make_response()  # no usage headers
        with caplog.at_level(_logging.INFO, logger="genlab_core.platforms.meta_http"):
            _capture_usage_headers(resp)
        info = [r for r in caplog.records if r.levelno == _logging.INFO
                and "meta_usage" in r.message]
        assert not info, (
            f"Should not log when no usage headers present, got: {[r.message for r in info]}"
        )
