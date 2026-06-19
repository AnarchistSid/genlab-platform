"""Tests for genlab_core.platforms.base.BasePlatformClient.

Pin the contract that 5 platform clients will progressively migrate to.
Bug class addressed: ~3,200 LOC of duplicate auth/retry/error/logging
across IG/FB/YT/X/Threads producing subtle drift (different retry counts,
different log formats, different missing-token handling).

The base owns the shared infrastructure; subclasses only implement
``_do_publish``. A migrated subclass that drops a contract behavior
(e.g. wrong missing-token return shape) fails one of these tests.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from genlab_core.platforms.base import BasePlatformClient
from genlab_core.platforms.models import PublishPayload, PublishResult


def _make_payload() -> PublishPayload:
    return PublishPayload(
        caption="test",
        media_paths=[],
        media_type="text",
        hashtags=[],
        hook="hook",
        niche_id="gaming",
    )


class _ConcretePlatformClient(BasePlatformClient):
    """Minimal valid subclass — used by all tests below."""

    platform_id = "test_platform"
    TOKEN_ENV_SUFFIX = "TEST_PLATFORM_TOKEN"

    def __init__(self, niche_id: str, publish_result: PublishResult | None = None):
        super().__init__(niche_id)
        self._stub_result = publish_result
        self.publish_call_count = 0
        self.last_token_seen: str | None = None

    def _do_publish(self, payload: PublishPayload, token: str) -> PublishResult:
        self.publish_call_count += 1
        self.last_token_seen = token
        if self._stub_result is not None:
            return self._stub_result
        return PublishResult(platform=self.platform_id, success=True, post_id="default-id")


# ─── subclass contract ────────────────────────────────────────────────────────


class TestSubclassContract:
    def test_subclass_must_set_platform_id(self):
        class BadNoPlatformId(BasePlatformClient):
            TOKEN_ENV_SUFFIX = "FOO"

            def _do_publish(self, payload, token): ...

        with pytest.raises(NotImplementedError, match="platform_id"):
            BadNoPlatformId(niche_id="gaming")

    def test_subclass_must_set_token_env_suffix(self):
        class BadNoTokenSuffix(BasePlatformClient):
            platform_id = "x"

            def _do_publish(self, payload, token): ...

        with pytest.raises(NotImplementedError, match="TOKEN_ENV_SUFFIX"):
            BadNoTokenSuffix(niche_id="gaming")

    def test_subclass_must_implement_do_publish(self):
        class BadAbstract(BasePlatformClient):
            platform_id = "x"
            TOKEN_ENV_SUFFIX = "F"

        with pytest.raises(TypeError):  # abstract method
            BadAbstract(niche_id="gaming")  # type: ignore[abstract]


# ─── token resolution ─────────────────────────────────────────────────────────


class TestTokenResolution:
    def test_access_token_delegates_to_resolve_niche_env(self, monkeypatch):
        """Token resolution flows through ``niche_credentials.resolve_niche_env``
        so the strict per-niche-prefix-no-cross-channel rule is honored."""
        client = _ConcretePlatformClient(niche_id="gaming")
        with patch(
            "genlab_core.publishing.niche_credentials.resolve_niche_env",
            return_value="resolved-token-xyz",
        ) as mock_resolve:
            token = client.access_token
        assert token == "resolved-token-xyz"
        mock_resolve.assert_called_once_with(
            niche_id="gaming",
            global_var="TEST_PLATFORM_TOKEN",
            niche_suffix="TEST_PLATFORM_TOKEN",
        )

    def test_publish_passes_resolved_token_to_do_publish(self):
        client = _ConcretePlatformClient(niche_id="gaming")
        with patch(
            "genlab_core.publishing.niche_credentials.resolve_niche_env",
            return_value="resolved-token-xyz",
        ):
            client.publish(_make_payload())
        assert client.last_token_seen == "resolved-token-xyz"


# ─── missing-token handling ───────────────────────────────────────────────────


class TestMissingToken:
    def test_returns_skipped_publish_result_when_token_missing(self):
        """The contract: when token is empty, return a SKIPPED ``PublishResult``
        with a clear error message — do NOT raise. This matches existing
        client behavior so a missing token for one platform doesn't kill the
        publisher's parallel attempt at other platforms."""
        client = _ConcretePlatformClient(niche_id="gaming")
        with patch(
            "genlab_core.publishing.niche_credentials.resolve_niche_env",
            return_value="",  # missing
        ):
            result = client.publish(_make_payload())
        assert isinstance(result, PublishResult)
        assert result.success is False
        assert result.platform == "test_platform"
        assert "TEST_PLATFORM_TOKEN" in result.error
        assert "gaming" in result.error

    def test_do_publish_not_called_when_token_missing(self):
        """No API call attempt when token is missing — saves wasted retries."""
        client = _ConcretePlatformClient(niche_id="gaming")
        with patch(
            "genlab_core.publishing.niche_credentials.resolve_niche_env",
            return_value="",
        ):
            client.publish(_make_payload())
        assert client.publish_call_count == 0

    def test_missing_token_logs_warning(self, caplog):
        """Operator must see WHICH platform was skipped + WHY."""
        client = _ConcretePlatformClient(niche_id="gaming")
        with (
            patch(
                "genlab_core.publishing.niche_credentials.resolve_niche_env",
                return_value="",
            ),
            caplog.at_level(logging.WARNING, logger="genlab_core.platforms.base"),
        ):
            client.publish(_make_payload())
        warnings = [r for r in caplog.records if "publish skipped" in r.message]
        assert len(warnings) == 1
        assert warnings[0].levelname == "WARNING"


# ─── successful publish flow ──────────────────────────────────────────────────


class TestPublishFlow:
    def test_publish_returns_do_publish_result(self):
        expected = PublishResult(platform="test_platform", success=True, post_id="ig-12345")
        client = _ConcretePlatformClient(niche_id="gaming", publish_result=expected)
        with patch(
            "genlab_core.publishing.niche_credentials.resolve_niche_env",
            return_value="token-xyz",
        ):
            result = client.publish(_make_payload())
        assert result is expected
        assert client.publish_call_count == 1

    def test_publish_attempt_logged_at_info(self, caplog):
        """Every successful publish attempt is logged so we can correlate
        failures with the attempt sequence."""
        client = _ConcretePlatformClient(niche_id="movies")
        with (
            patch(
                "genlab_core.publishing.niche_credentials.resolve_niche_env",
                return_value="token-xyz",
            ),
            caplog.at_level(logging.INFO, logger="genlab_core.platforms.base"),
        ):
            client.publish(_make_payload())
        attempts = [r for r in caplog.records if "publish attempt" in r.message]
        assert len(attempts) == 1
        assert attempts[0].levelname == "INFO"


# ─── per-platform logger naming ───────────────────────────────────────────────


class TestLoggerNaming:
    def test_each_subclass_gets_its_own_child_logger(self):
        """Operators can filter logs by platform via the logger hierarchy
        without parsing message bodies."""

        class FirstClient(BasePlatformClient):
            platform_id = "first"
            TOKEN_ENV_SUFFIX = "FIRST_TOKEN"

            def _do_publish(self, payload, token):
                return PublishResult(platform=self.platform_id, success=True)

        class SecondClient(BasePlatformClient):
            platform_id = "second"
            TOKEN_ENV_SUFFIX = "SECOND_TOKEN"

            def _do_publish(self, payload, token):
                return PublishResult(platform=self.platform_id, success=True)

        a = FirstClient(niche_id="gaming")
        b = SecondClient(niche_id="gaming")
        assert a._log.name == "genlab_core.platforms.base.first"
        assert b._log.name == "genlab_core.platforms.base.second"
        assert a._log is not b._log


# ─── architectural pins ──────────────────────────────────────────────────────


class TestArchitecturalContracts:
    """Documentation tests — fail loud if a future refactor breaks the
    contract these tests pin."""

    def test_subclasses_must_inherit_from_base_to_get_shared_infra(self):
        """A platform client that doesn't inherit from ``BasePlatformClient``
        won't get the auth/retry/log infrastructure. The test pins the
        inheritance check (used by future contract tests that verify all
        5 clients eventually inherit)."""
        client = _ConcretePlatformClient(niche_id="gaming")
        assert isinstance(client, BasePlatformClient)

    def test_publish_method_is_the_only_dispatch_entry(self):
        """The dispatcher should ONLY call ``publish()`` on a client.
        ``_do_publish`` is the internal hook for subclasses; if a client
        is called via ``_do_publish`` directly it bypasses token
        resolution + logging. The leading underscore communicates intent."""
        client = _ConcretePlatformClient(niche_id="gaming")
        # Public methods that look like dispatch entries
        public = [name for name in dir(client) if not name.startswith("_")]
        # publish is the only one (access_token is a property, not a verb)
        dispatch_verbs = [n for n in public if n in ("publish", "post", "send", "upload")]
        assert dispatch_verbs == ["publish"], (
            f"Found unexpected public dispatch methods on BasePlatformClient subclass: "
            f"{dispatch_verbs}. If a subclass needs additional verbs, they should be "
            "internal (_-prefix) helpers called from publish, OR Engageable/Trackable "
            "Protocol methods (post_reply, get_metrics) which are runtime-checked separately."
        )
