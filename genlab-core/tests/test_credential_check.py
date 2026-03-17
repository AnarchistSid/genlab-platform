"""Tests for genlab_core.tools.credential_check.

Validates that the credential checker correctly identifies missing
and present env vars, with proper exit codes.
"""
from __future__ import annotations

import pytest
from genlab_core.tools.credential_check import (
    OPTIONAL_PLATFORMS,
    REQUIRED_PLATFORMS,
    check_credentials,
    main,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# All required env vars across all platforms
ALL_REQUIRED_VARS = {
    var for vars_list in REQUIRED_PLATFORMS.values() for var in vars_list
}

ALL_OPTIONAL_VARS = {
    var for vars_list in OPTIONAL_PLATFORMS.values() for var in vars_list
}


def _set_all_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set all required env vars to dummy values."""
    for var in ALL_REQUIRED_VARS:
        monkeypatch.setenv(var, "test_value")


def _set_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set all env vars (required + optional) to dummy values."""
    for var in ALL_REQUIRED_VARS | ALL_OPTIONAL_VARS:
        monkeypatch.setenv(var, "test_value")


def _clear_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove all platform env vars."""
    for var in ALL_REQUIRED_VARS | ALL_OPTIONAL_VARS:
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# 1. All required vars present -> exit code 0
# ---------------------------------------------------------------------------


class TestAllRequiredPresent:
    def test_exit_code_zero_when_all_required_set(self, monkeypatch):
        _set_all(monkeypatch)
        assert main() == 0

    def test_no_missing_required(self, monkeypatch):
        _set_all(monkeypatch)
        missing_req, _missing_opt = check_credentials()
        assert missing_req == {}


# ---------------------------------------------------------------------------
# 2. Missing a required var -> exit code 1
# ---------------------------------------------------------------------------


class TestMissingRequired:
    def test_exit_code_one_when_youtube_missing(self, monkeypatch):
        _set_all(monkeypatch)
        monkeypatch.delenv("YOUTUBE_CLIENT_ID")
        assert main() == 1

    def test_missing_youtube_reported(self, monkeypatch):
        _set_all(monkeypatch)
        monkeypatch.delenv("YOUTUBE_CLIENT_ID")
        missing_req, _ = check_credentials()
        assert "YouTube" in missing_req
        assert "YOUTUBE_CLIENT_ID" in missing_req["YouTube"]


# ---------------------------------------------------------------------------
# 3. Missing optional var -> still exit code 0, but reported
# ---------------------------------------------------------------------------


class TestMissingOptional:
    def test_exit_code_zero_with_optional_missing(self, monkeypatch):
        _set_all_required(monkeypatch)
        # optional vars not set
        monkeypatch.delenv("TIKTOK_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("THREADS_ACCESS_TOKEN", raising=False)
        assert main() == 0

    def test_optional_missing_reported(self, monkeypatch):
        _set_all_required(monkeypatch)
        monkeypatch.delenv("TIKTOK_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("THREADS_ACCESS_TOKEN", raising=False)
        _, missing_opt = check_credentials()
        assert "TikTok" in missing_opt
        assert "Threads" in missing_opt


# ---------------------------------------------------------------------------
# 4. All vars missing -> exit code 1, all required platforms reported
# ---------------------------------------------------------------------------


class TestAllMissing:
    def test_exit_code_one_when_all_missing(self, monkeypatch):
        _clear_all(monkeypatch)
        assert main() == 1

    def test_all_required_platforms_reported(self, monkeypatch):
        _clear_all(monkeypatch)
        missing_req, _ = check_credentials()
        for platform in REQUIRED_PLATFORMS:
            assert platform in missing_req


# ---------------------------------------------------------------------------
# 5. Whitespace-only var treated as missing
# ---------------------------------------------------------------------------


class TestWhitespaceOnly:
    def test_whitespace_treated_as_missing(self, monkeypatch):
        _set_all(monkeypatch)
        monkeypatch.setenv("META_ACCESS_TOKEN", "   ")
        missing_req, _ = check_credentials()
        assert "Instagram/Meta" in missing_req


# ---------------------------------------------------------------------------
# 6. Empty string var treated as missing
# ---------------------------------------------------------------------------


class TestEmptyString:
    def test_empty_string_treated_as_missing(self, monkeypatch):
        _set_all(monkeypatch)
        monkeypatch.setenv("X_BEARER_TOKEN", "")
        _, missing_opt = check_credentials()
        assert "X/Twitter" in missing_opt  # X/Twitter moved to OPTIONAL (Sprint 31)
