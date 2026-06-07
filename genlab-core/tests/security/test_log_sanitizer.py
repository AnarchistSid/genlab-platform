"""Unit tests for the canonical token scrubber.

Lives next to its module so the security primitive has its own focused
suite — separate from the engagement-poller tests that exercise it
indirectly. Any future HTTP caller that imports ``scrub_token`` can rely
on the contract here.
"""

from __future__ import annotations

import pytest
from genlab_core.security import scrub_token


class TestRedactsTokenInQueryString:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (
                "GET https://api.example.com/foo?access_token=ABCDEF&x=safe",
                "GET https://api.example.com/foo?access_token=<REDACTED>&x=safe",
            ),
            (
                "url?api_key=DEADBEEF",
                "url?api_key=<REDACTED>",
            ),
            (
                "https://x?key=abc123",
                "https://x?key=<REDACTED>",
            ),
            (
                "Authorization: bearer=THAANDXYZ",
                "Authorization: bearer=<REDACTED>",
            ),
        ],
    )
    def test_credential_query_param_is_redacted(self, raw: str, expected: str) -> None:
        assert scrub_token(raw) == expected

    def test_multiple_credentials_in_one_message(self) -> None:
        msg = "url=https://x?api_key=K1&bearer=B1&access_token=A1&unrelated=safe"
        out = scrub_token(msg)
        assert "K1" not in out
        assert "B1" not in out
        assert "A1" not in out
        # Non-credential params are preserved.
        assert "unrelated=safe" in out

    def test_param_name_is_preserved(self) -> None:
        # The redaction leaves the parameter NAME so log lines stay legible
        # (we can still see *which* credential failed, just not its value).
        out = scrub_token("?access_token=SECRET")
        assert "access_token=" in out
        assert "<REDACTED>" in out
        assert "SECRET" not in out


class TestPreservesNonCredentialContent:
    def test_safe_string_unchanged(self) -> None:
        msg = "Connection refused"
        assert scrub_token(msg) == "Connection refused"

    def test_empty_string(self) -> None:
        assert scrub_token("") == ""

    def test_url_without_token_unchanged(self) -> None:
        msg = "GET https://api.example.com/foo?limit=10&fields=id"
        assert scrub_token(msg) == msg


class TestAcceptsAnyObject:
    """``scrub_token`` is typed as ``object`` deliberately — callers pass
    exceptions, URL strings, formatted log lines, etc. The contract is
    ``str(obj)`` is run before scrubbing."""

    def test_exception_object(self) -> None:
        exc = RuntimeError("GET https://x?access_token=SECRET failed")
        out = scrub_token(exc)
        assert "SECRET" not in out
        assert "<REDACTED>" in out
        assert "failed" in out

    def test_integer(self) -> None:
        # No credential to redact, str() coercion still works.
        assert scrub_token(42) == "42"

    def test_object_with_custom_str(self) -> None:
        class Custom:
            def __str__(self) -> str:
                return "request to ?access_token=LEAK"

        out = scrub_token(Custom())
        assert "LEAK" not in out
        assert "<REDACTED>" in out


class TestCaseInsensitiveMatch:
    @pytest.mark.parametrize(
        "param",
        ["ACCESS_TOKEN", "Access_Token", "BEARER", "Api_Key", "KEY"],
    )
    def test_matches_uppercase_variants(self, param: str) -> None:
        out = scrub_token(f"?{param}=SECRET")
        assert "SECRET" not in out
        # The original casing of the parameter name is preserved.
        assert param in out
