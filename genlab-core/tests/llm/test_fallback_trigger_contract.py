"""FIX-LLMFB §1: what must and must not trigger a fallback off Anthropic.

Measured 2026-09-12: the previous `should_fallback` got two of these six wrong.
A typed `billing_error` with a terse message returned False, because detection
was purely substring-based on "credit balance is too low" — so the shape the SDK
actually models did not match and no fallback fired. And a 429 returned True,
moving traffic off the primary for transient throttling.

Both matter. The OpenAI fallback has never fired in production (no OpenAI model
in `pipeline_run_costs.by_model` since 2026-06-14), and during the 09-06→09-09
outage `by_model` carried only `{"tts": ...}` — no Claude, no OpenAI.
"""

from __future__ import annotations

import httpx
import pytest
from anthropic import _exceptions as ex
from genlab_core.llm.fallback import should_fallback, should_fallback_on_auth


def _err(cls, status: int, err_type: str, message: str):
    body = {"type": "error", "error": {"type": err_type, "message": message}}
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(status, request=req, json=body)
    return cls(message=message, response=resp, body=body)


# (id, exception factory, expected should_fallback)
CASES = [
    # EXHAUSTION — must fail over.
    (
        "billing_error_terse",
        lambda: _err(ex.BadRequestError, 400, "billing_error", "Billing error"),
        True,
    ),
    (
        "billing_error_classic",
        lambda: _err(
            ex.BadRequestError,
            400,
            "invalid_request_error",
            "Your credit balance is too low to access the Anthropic API.",
        ),
        True,
    ),
    (
        "insufficient_credits",
        lambda: _err(ex.BadRequestError, 400, "invalid_request_error", "insufficient credits"),
        True,
    ),
    # TRANSIENT — must NOT fail over; retry on the primary.
    (
        "rate_limit_429",
        lambda: _err(ex.RateLimitError, 429, "rate_limit_error", "rate limited"),
        False,
    ),
    (
        "overloaded_529",
        lambda: _err(ex.OverloadedError, 529, "overloaded_error", "Overloaded"),
        False,
    ),
    (
        "internal_500",
        lambda: _err(ex.InternalServerError, 500, "api_error", "Internal server error"),
        False,
    ),
    (
        "service_unavailable_503",
        lambda: _err(ex.ServiceUnavailableError, 503, "api_error", "unavailable"),
        False,
    ),
    # OPERATOR PROBLEM — not should_fallback's business.
    (
        "auth_401",
        lambda: _err(ex.AuthenticationError, 401, "authentication_error", "invalid x-api-key"),
        False,
    ),
]


@pytest.mark.parametrize("case_id,make,expected", CASES, ids=[c[0] for c in CASES])
def test_fallback_trigger_contract(case_id, make, expected):
    assert should_fallback(make()) is expected


def test_auth_failover_is_a_separate_opt_in(monkeypatch):
    """A revoked key is functionally exhaustion, but the caller decides —
    a FIRST auth failure can be a control-plane blip, and failing over on it
    would hide a key rotation."""
    auth = _err(ex.AuthenticationError, 401, "authentication_error", "invalid x-api-key")
    monkeypatch.delenv("GENLAB_LLM_FALLBACK_ON_AUTH", raising=False)
    assert should_fallback_on_auth(auth) is True  # default on
    assert should_fallback(auth) is False  # but not via the main path
    monkeypatch.setenv("GENLAB_LLM_FALLBACK_ON_AUTH", "0")
    assert should_fallback_on_auth(auth) is False  # toggle honoured


def test_non_api_exceptions_never_trigger():
    """A network error would hit the same DNS from the secondary."""
    assert should_fallback(ConnectionError("dns failure")) is False
    assert should_fallback(ValueError("unrelated")) is False
