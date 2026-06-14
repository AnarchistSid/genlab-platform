"""2026-06-14 Threads LLUT auto-refresh pins.

The engagement-loop audit found all 5 niche Threads tokens silently
expired 2026-05-21 (60-day LLUTs never refreshed). This module pins
the new ``refresh_threads_token`` helper contract.

Tests are pure-unit: mock requests.get to assert the URL/params/
error-handling shape WITHOUT hitting the Threads API. Integration
coverage (real API call) is intentionally out of scope — that requires
real credentials and would be flaky in CI.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _mock_response(*, ok: bool, status: int = 200, reason: str = "OK", body: dict | None = None):
    """Build a requests.Response mock with the given shape."""
    resp = MagicMock()
    resp.ok = ok
    resp.status_code = status
    resp.reason = reason
    resp.json.return_value = body or {}
    return resp


# ────────────────────────────────────────────────────────────────────────────
# Happy path — successful refresh
# ────────────────────────────────────────────────────────────────────────────


def test_refresh_returns_new_token_and_expiry_on_success():
    """The Threads API returns {access_token, token_type, expires_in}.
    The helper must extract both and surface them on the result."""
    from genlab_core.engagement.token_health import refresh_threads_token

    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response(
            ok=True,
            body={
                "access_token": "NEW_TOKEN_VALUE",
                "token_type": "bearer",
                "expires_in": 5184000,  # 60 days
            },
        )
        result = refresh_threads_token("OLD_TOKEN")

    assert result.ok is True
    assert result.access_token == "NEW_TOKEN_VALUE"
    assert result.expires_in == 5184000
    assert result.error == ""
    # Verify the URL + params shape the Threads API expects.
    call = mock_get.call_args
    assert "graph.threads.net" in call.args[0]
    assert "refresh_access_token" in call.args[0]
    assert call.kwargs["params"]["grant_type"] == "th_refresh_token"
    assert call.kwargs["params"]["access_token"] == "OLD_TOKEN"


# ────────────────────────────────────────────────────────────────────────────
# Failure modes
# ────────────────────────────────────────────────────────────────────────────


def test_refresh_handles_http_error_without_logging_token():
    """A 400/401/etc must surface as ok=False with a status-only error
    message. The response body is NOT included in the error because it
    may echo the token value."""
    from genlab_core.engagement.token_health import refresh_threads_token

    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response(ok=False, status=400, reason="Bad Request")
        result = refresh_threads_token("EXPIRED_TOKEN")

    assert result.ok is False
    assert "400" in result.error
    assert "Bad Request" in result.error
    assert "EXPIRED_TOKEN" not in result.error, (
        "regression: error message must NOT echo the token value"
    )
    assert result.access_token == ""


def test_refresh_handles_malformed_json_response():
    """Threads API quirks have been seen (HTML error pages, missing
    fields). Helper must surface ``ok=False`` with a structured error."""
    from genlab_core.engagement.token_health import refresh_threads_token

    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response(ok=True, body={"unexpected": "shape"})
        result = refresh_threads_token("OLD_TOKEN")

    assert result.ok is False
    assert "malformed" in result.error.lower()
    assert "unexpected" in result.error  # the response keys are in the diagnostic


def test_refresh_handles_network_exception():
    """requests.Timeout / ConnectionError / etc. must NOT raise — the
    caller (the daily systemd timer) treats any exception as a refresh
    failure and emits an alert."""
    from genlab_core.engagement.token_health import refresh_threads_token

    with patch("requests.get", side_effect=TimeoutError("read timeout after 10s")):
        result = refresh_threads_token("OLD_TOKEN")

    assert result.ok is False
    assert "TimeoutError" in result.error


def test_refresh_rejects_empty_token_before_calling_api():
    """An empty current_token must fail fast (no HTTP call). Otherwise
    we'd pass an empty access_token to graph.threads.net and get a
    confusing 400 instead of a clear local error."""
    from genlab_core.engagement.token_health import refresh_threads_token

    with patch("requests.get") as mock_get:
        result = refresh_threads_token("")

    assert result.ok is False
    assert "empty" in result.error.lower()
    mock_get.assert_not_called()


def test_refresh_handles_zero_expires_in():
    """Defensive: if the API responds with expires_in=0, treat as
    malformed. A 0-second token is useless and likely indicates a
    parsing or scope problem."""
    from genlab_core.engagement.token_health import refresh_threads_token

    with patch("requests.get") as mock_get:
        mock_get.return_value = _mock_response(
            ok=True,
            body={"access_token": "TOK", "expires_in": 0, "token_type": "bearer"},
        )
        result = refresh_threads_token("OLD_TOKEN")

    assert result.ok is False
    assert "malformed" in result.error.lower()


# ────────────────────────────────────────────────────────────────────────────
# Repr — no token leakage in __repr__
# ────────────────────────────────────────────────────────────────────────────


def test_repr_does_not_leak_access_token():
    """The ThreadsRefreshResult repr is used in logs and error messages.
    It must NOT contain the access_token value even on success."""
    from genlab_core.engagement.token_health import ThreadsRefreshResult

    r = ThreadsRefreshResult(ok=True, access_token="SECRET_TOKEN_VALUE", expires_in=5184000)
    text = repr(r)
    assert "SECRET_TOKEN_VALUE" not in text, (
        f"regression: __repr__ leaked the token value. Got: {text}"
    )
    # The expires_in IS safe to surface (just a duration).
    assert "5184000" in text
