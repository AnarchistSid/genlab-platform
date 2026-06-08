"""Tests for genlab_core.tools.credential_check.

Audit M-5 expanded the checker from 5 platforms to ~15 required +
optional env vars + per-niche prefixes + WARP proxy probe. These
tests pin the new contract.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from genlab_core.tools.credential_check import (
    NICHE_PREFIXES,
    NICHE_REQUIRED_SUFFIXES,
    OPTIONAL_INFRA,
    OPTIONAL_PLATFORMS,
    REQUIRED_INFRA,
    REQUIRED_PLATFORMS,
    check_credentials,
    check_niche_credentials,
    check_warp_proxy,
    main,
)


def _set_group(monkeypatch: pytest.MonkeyPatch, group: dict[str, list[str]]) -> None:
    for env_vars in group.values():
        for v in env_vars:
            monkeypatch.setenv(v, "x")


def _all_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set every var that any REQUIRED_* group asks for."""
    _set_group(monkeypatch, REQUIRED_PLATFORMS)
    _set_group(monkeypatch, REQUIRED_INFRA)


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wipe every var the checker looks at — both required + optional."""
    for group in (REQUIRED_PLATFORMS, REQUIRED_INFRA, OPTIONAL_PLATFORMS, OPTIONAL_INFRA):
        for env_vars in group.values():
            for v in env_vars:
                monkeypatch.delenv(v, raising=False)


# ---------------------------------------------------------------------------
# check_credentials (4-bucket dict shape)
# ---------------------------------------------------------------------------


class TestCheckCredentials:
    def test_returns_four_buckets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_env(monkeypatch)
        result = check_credentials()
        assert set(result.keys()) == {
            "required_platforms",
            "required_infra",
            "optional_platforms",
            "optional_infra",
        }

    def test_all_required_missing_when_env_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_env(monkeypatch)
        result = check_credentials()
        assert len(result["required_platforms"]) == len(REQUIRED_PLATFORMS)
        assert len(result["required_infra"]) == len(REQUIRED_INFRA)

    def test_all_required_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_env(monkeypatch)
        _all_required(monkeypatch)
        result = check_credentials()
        assert result["required_platforms"] == {}
        assert result["required_infra"] == {}

    def test_whitespace_treated_as_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_env(monkeypatch)
        for env_vars in REQUIRED_PLATFORMS.values():
            for v in env_vars:
                monkeypatch.setenv(v, "   ")  # whitespace-only
        result = check_credentials()
        assert len(result["required_platforms"]) == len(REQUIRED_PLATFORMS)

    def test_optional_missing_does_not_affect_required(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An incomplete optional group must not flip a required group's
        ready status."""
        _clear_env(monkeypatch)
        _all_required(monkeypatch)
        result = check_credentials()
        assert result["required_platforms"] == {}
        assert result["required_infra"] == {}
        # Optional groups have entries because nothing is set there.
        assert result["optional_platforms"] or result["optional_infra"]


# ---------------------------------------------------------------------------
# check_niche_credentials (per-niche prefix validation)
# ---------------------------------------------------------------------------


class TestCheckNicheCredentials:
    def test_unknown_niche_returns_empty(self) -> None:
        """An unknown niche_id is silently empty — the orchestrator
        validates the niche_id elsewhere; this function only reports."""
        assert check_niche_credentials("zoom_dance") == {}

    @pytest.mark.parametrize("niche", sorted(NICHE_PREFIXES.keys()))
    def test_empty_env_means_all_platforms_missing(
        self, niche: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Wipe all prefixed vars for this niche.
        prefix = NICHE_PREFIXES[niche]
        for suffixes in NICHE_REQUIRED_SUFFIXES.values():
            for s in suffixes:
                monkeypatch.delenv(f"{prefix}_{s}", raising=False)
        result = check_niche_credentials(niche)
        assert set(result.keys()) == set(NICHE_REQUIRED_SUFFIXES.keys())

    def test_full_env_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        prefix = NICHE_PREFIXES["gaming"]
        for suffixes in NICHE_REQUIRED_SUFFIXES.values():
            for s in suffixes:
                monkeypatch.setenv(f"{prefix}_{s}", "tok")
        assert check_niche_credentials("gaming") == {}


# ---------------------------------------------------------------------------
# check_warp_proxy
# ---------------------------------------------------------------------------


class TestCheckWarpProxy:
    def test_unset_env_returns_not_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Both GENLAB_HTTP_SOCKS_PROXY and YT_DLP_PROXY unset → fail."""
        monkeypatch.delenv("GENLAB_HTTP_SOCKS_PROXY", raising=False)
        monkeypatch.delenv("YT_DLP_PROXY", raising=False)
        ok, msg = check_warp_proxy()
        assert ok is False
        assert "unset" in msg

    def test_unreachable_host_returns_not_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A configured-but-unreachable proxy fails (the Hetzner-WARP-died
        regression class — yt-dlp depends on this proxy)."""
        # Port 1 is reserved (RFC) and never listens.
        monkeypatch.setenv("GENLAB_HTTP_SOCKS_PROXY", "socks5://127.0.0.1:1")
        ok, msg = check_warp_proxy()
        assert ok is False
        assert "1" in msg  # port in the error message

    def test_yt_dlp_proxy_fallback_honoured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Older launchd plists set YT_DLP_PROXY; the checker must still
        honour it so existing deployments don't fail-loud during the
        env-var rename window."""
        monkeypatch.delenv("GENLAB_HTTP_SOCKS_PROXY", raising=False)
        monkeypatch.setenv("YT_DLP_PROXY", "socks5://127.0.0.1:1")
        ok, msg = check_warp_proxy()
        # Probe still fails (port 1 unreachable) but the env var was found.
        assert "1" in msg


# ---------------------------------------------------------------------------
# main() exit codes — the contract operators rely on
# ---------------------------------------------------------------------------


class TestMainExitCode:
    def test_exit_one_when_required_platform_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_env(monkeypatch)
        # Set only infra, leave platforms unset.
        _set_group(monkeypatch, REQUIRED_INFRA)
        with patch("sys.argv", ["credential_check"]):
            assert main() == 1

    def test_exit_one_when_required_infra_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_env(monkeypatch)
        _set_group(monkeypatch, REQUIRED_PLATFORMS)
        # REQUIRED_INFRA left unset → exit 1.
        with patch("sys.argv", ["credential_check"]):
            assert main() == 1

    def test_exit_zero_when_all_required_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_env(monkeypatch)
        _all_required(monkeypatch)
        with patch("sys.argv", ["credential_check"]):
            assert main() == 0

    def test_exit_zero_when_only_optionals_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing optional vars (TikTok, ElevenLabs, etc) must NOT fail
        the startup gate — they're truly optional."""
        _clear_env(monkeypatch)
        _all_required(monkeypatch)
        with patch("sys.argv", ["credential_check"]):
            assert main() == 0

    def test_exit_one_when_niche_credentials_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``--niche gaming`` with no CRITICALRUSH_* vars set must fail."""
        _clear_env(monkeypatch)
        _all_required(monkeypatch)
        for s in (
            "YOUTUBE_REFRESH_TOKEN",
            "YT_CHANNEL_ID",
            "META_ACCESS_TOKEN",
            "IG_USER_ID",
        ):
            monkeypatch.delenv(f"CRITICALRUSH_{s}", raising=False)
        with patch("sys.argv", ["credential_check", "--niche", "gaming"]):
            assert main() == 1
