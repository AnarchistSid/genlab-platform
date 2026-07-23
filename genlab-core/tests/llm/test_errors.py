"""Tests for genlab_core.llm.errors.classify_llm_error.

Motivating: this classifier is the shared attribution helper for LLM
SDK errors, symmetric with format_meta_error for Meta APIs. See
[[class-of-bug-signal-loss-through-merged-failure-paths]] — 3 days
of Threads engagement failures all wrote "Reply generation failed"
because the caller had no way to attribute the underlying LLM error
to a specific category.
"""

from __future__ import annotations

import pytest
from genlab_core.llm.errors import (
    LLM_ERROR_AUTH,
    LLM_ERROR_CIRCUIT_OPEN,
    LLM_ERROR_CONNECTION,
    LLM_ERROR_CONTENT_FILTER,
    LLM_ERROR_CREDIT_EXHAUSTED,
    LLM_ERROR_INVALID_REQUEST,
    LLM_ERROR_OVERLOADED,
    LLM_ERROR_RATE_LIMIT,
    LLM_ERROR_TIMEOUT,
    LLM_ERROR_UNKNOWN,
    classify_llm_error,
)


class TestMessageMarkersEscalate:
    """Message markers must win over class-name mapping — a
    BadRequestError with 'credit balance too low' is credit exhaustion,
    not a generic invalid request."""

    def test_anthropic_credit_exhaustion(self):
        exc = RuntimeError(
            "Error code: 400 - {'type': 'error', 'error': "
            "{'type': 'invalid_request_error', 'message': "
            "'Your credit balance is too low to access the Anthropic API'}}"
        )
        assert classify_llm_error(exc) == LLM_ERROR_CREDIT_EXHAUSTED

    def test_openai_insufficient_quota(self):
        exc = RuntimeError(
            "Error code: 429 - {'error': {'code': 'insufficient_quota', "
            "'message': 'You exceeded your current quota'}}"
        )
        assert classify_llm_error(exc) == LLM_ERROR_CREDIT_EXHAUSTED

    def test_content_policy_violation(self):
        exc = RuntimeError("content_policy_violation: prompt was rejected")
        assert classify_llm_error(exc) == LLM_ERROR_CONTENT_FILTER

    def test_overloaded(self):
        exc = RuntimeError("overloaded_error: server is temporarily overloaded")
        assert classify_llm_error(exc) == LLM_ERROR_OVERLOADED

    def test_rate_limit_by_message(self):
        exc = RuntimeError("rate_limit_error: too many requests")
        assert classify_llm_error(exc) == LLM_ERROR_RATE_LIMIT


class TestClassNameFallback:
    """When no message marker matches, fall back to class-name mapping."""

    def test_rate_limit_error_class(self):
        # Simulate anthropic.RateLimitError shape without importing.
        class RateLimitError(Exception):
            pass

        assert classify_llm_error(RateLimitError("something")) == LLM_ERROR_RATE_LIMIT

    def test_authentication_error_class(self):
        class AuthenticationError(Exception):
            pass

        assert classify_llm_error(AuthenticationError("bad key")) == LLM_ERROR_AUTH

    def test_api_connection_error_class(self):
        class APIConnectionError(Exception):
            pass

        assert (
            classify_llm_error(APIConnectionError("DNS failure"))
            == LLM_ERROR_CONNECTION
        )

    def test_api_timeout_error_class(self):
        class APITimeoutError(Exception):
            pass

        assert (
            classify_llm_error(APITimeoutError("timed out"))
            == LLM_ERROR_TIMEOUT
        )

    def test_bad_request_error_class(self):
        class BadRequestError(Exception):
            pass

        assert (
            classify_llm_error(BadRequestError("malformed body"))
            == LLM_ERROR_INVALID_REQUEST
        )

    def test_our_circuit_open_error(self):
        class CircuitOpenError(Exception):
            pass

        assert classify_llm_error(CircuitOpenError()) == LLM_ERROR_CIRCUIT_OPEN


class TestFallbackToUnknown:
    """Unrecognised exceptions map to 'unknown' — never raises."""

    def test_generic_runtime_error(self):
        assert classify_llm_error(RuntimeError("who knows")) == LLM_ERROR_UNKNOWN

    def test_value_error(self):
        assert classify_llm_error(ValueError("bad type")) == LLM_ERROR_UNKNOWN

    def test_none_exception_returns_unknown(self):
        # Defensive: passing None must not crash.
        assert classify_llm_error(None) == LLM_ERROR_UNKNOWN  # type: ignore[arg-type]


class TestMessageMarkerCaseInsensitive:
    """Message markers must match regardless of case."""

    def test_uppercase_credit_message(self):
        exc = RuntimeError("CREDIT BALANCE IS TOO LOW")
        assert classify_llm_error(exc) == LLM_ERROR_CREDIT_EXHAUSTED

    def test_mixed_case_rate_limit(self):
        exc = RuntimeError("Rate Limit Exceeded — try again in 60s")
        assert classify_llm_error(exc) == LLM_ERROR_RATE_LIMIT


def test_categories_are_stable_strings():
    """Downstream consumers filter journal + DB by these strings —
    they must not change across refactors without updating consumers."""
    assert LLM_ERROR_CREDIT_EXHAUSTED == "credit_exhausted"
    assert LLM_ERROR_RATE_LIMIT == "rate_limit"
    assert LLM_ERROR_AUTH == "auth"
    assert LLM_ERROR_INVALID_REQUEST == "invalid_request"
    assert LLM_ERROR_TIMEOUT == "timeout"
    assert LLM_ERROR_CONNECTION == "connection"
    assert LLM_ERROR_OVERLOADED == "overloaded"
    assert LLM_ERROR_CONTENT_FILTER == "content_filter"
    assert LLM_ERROR_CIRCUIT_OPEN == "circuit_open"
    assert LLM_ERROR_UNKNOWN == "unknown"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
