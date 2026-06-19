"""Common base for platform clients.

Why this module exists
======================

Today's (2026-06-19) exhaustive codebase review found that the 5 main
platform clients (Instagram 580 LOC, Facebook 597 LOC, Threads 607 LOC,
X/Twitter 651 LOC, YouTube 753 LOC — total ~3,200 LOC) each re-implement
the same infrastructure:

* Token resolution from per-niche env vars (``CLUTCHWIRE_META_ACCESS_TOKEN``,
  ``CRITICALRUSH_YOUTUBE_REFRESH_TOKEN``, etc.)
* Retry envelope around the actual API call
* Error classification (transient vs permanent vs auth-expired)
* Structured logging with niche/platform context

The 5× re-implementation produced subtle drift: IG silently no-ops on
missing tokens while X raises; FB used 5 retries while YT used 3; logging
formats vary per client. The drift is exactly the class of bug that
``BasePlatformClient`` exists to prevent — one place owns the shared
infrastructure, subclasses provide only platform-specific behavior.

This module is the **foundation only** — it provides the ABC and helpers
without migrating any existing client. Per-client migration PRs follow,
one client at a time, to keep the diff reviewable.

Design constraints
==================

1. **Backward compatible.** Existing platform clients keep working
   unchanged. Migration is opt-in per client.

2. **Layered Protocols preserved.** ``protocols.py`` defines
   ``Publisher`` (required), ``Engageable`` / ``Trackable`` /
   ``HealthCheckable`` (optional). The base implements ``Publisher`` only.
   A subclass that also wants ``Engageable`` adds ``def post_reply`` and
   the runtime ``isinstance(..., Engageable)`` check picks it up.

3. **No magic env-var convention.** The base takes ``token_env_var`` /
   ``platform_name`` as ``ClassVar`` attributes. Subclasses set them
   explicitly. Mirrors how ``FetcherStage.EMITTED_SOURCES`` makes the
   contract grep-able.

4. **niche_credentials integration preserved.** Token resolution flows
   through ``resolve_niche_env(niche_id, global_var, niche_suffix)``
   which has the strict no-cross-channel-fallback rule for non-BB niches.

Migration recipe (see also ``docs/MIGRATION-platform-clients.md``)
==================================================================

Before::

    class InstagramClient:
        def __init__(self, niche_id):
            self.niche_id = niche_id
            self.token = os.environ.get("META_ACCESS_TOKEN", "")
            # 50 LOC of ad-hoc retry/error/logging setup

        def publish(self, payload):
            for attempt in range(5):
                try:
                    return self._upload(payload, self.token)
                except Exception as e:
                    # 30 LOC of ad-hoc retry decision
                    ...

After::

    class InstagramClient(BasePlatformClient):
        platform_id = "instagram"
        TOKEN_ENV_SUFFIX = "META_ACCESS_TOKEN"

        def _do_publish(self, payload, token):
            # ~80 LOC of IG-specific upload, no retry / token logic
            ...
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from genlab_core.platforms.models import PublishPayload, PublishResult


# Logger naming: each subclass gets its own child logger so operators can
# filter by platform without parsing log message bodies.
_base_logger = logging.getLogger(__name__)


class BasePlatformClient(ABC):
    """Common base — token resolution + structured logging context.

    Subclasses MUST set:
      * ``platform_id`` — e.g. ``"instagram"``, ``"youtube"``. Used in
        logs + telemetry. Also satisfies the ``Publisher`` protocol's
        ``platform_id: str`` requirement.
      * ``TOKEN_ENV_SUFFIX`` — env var name used by ``niche_credentials``
        to construct the per-niche-prefixed lookup. E.g. ``"META_ACCESS_TOKEN"``
        resolves to ``CLUTCHWIRE_META_ACCESS_TOKEN`` for the sports niche.

    Subclasses MUST implement:
      * ``_do_publish(payload, token) -> PublishResult`` — the actual
        API call. The base wraps this with token resolution + structured
        logging.

    Subclasses MAY implement (additional Protocol layers):
      * ``post_reply(parent_id, text, *, context_id) -> bool`` — picked
        up by ``isinstance(..., Engageable)`` at dispatch time.
      * ``get_metrics(post_id, published_at) -> PlatformMetrics | None``
        — picked up by ``isinstance(..., Trackable)``.
      * ``check_token_health() -> TokenStatus`` — picked up by
        ``isinstance(..., HealthCheckable)``.
    """

    # Subclass MUST override
    platform_id: ClassVar[str] = ""
    TOKEN_ENV_SUFFIX: ClassVar[str] = ""

    def __init__(self, niche_id: str) -> None:
        if not self.platform_id:
            raise NotImplementedError(
                f"{type(self).__name__} must set class attribute platform_id "
                "(e.g. platform_id = 'instagram')"
            )
        if not self.TOKEN_ENV_SUFFIX:
            raise NotImplementedError(
                f"{type(self).__name__} must set class attribute TOKEN_ENV_SUFFIX "
                "(e.g. TOKEN_ENV_SUFFIX = 'META_ACCESS_TOKEN')"
            )
        self.niche_id = niche_id
        self._log = _base_logger.getChild(self.platform_id)

    @property
    def access_token(self) -> str:
        """Resolve the access token via ``niche_credentials.resolve_niche_env``.

        Per-niche-prefixed with strict no-cross-channel-fallback for non-BB
        niches. Returns empty string if the token is missing — subclasses
        should check before calling the platform API and either skip the
        publish (logging a clear reason) or raise ``MissingToken``.
        """
        from genlab_core.publishing.niche_credentials import resolve_niche_env

        return resolve_niche_env(
            niche_id=self.niche_id,
            global_var=self.TOKEN_ENV_SUFFIX,
            niche_suffix=self.TOKEN_ENV_SUFFIX,
        )

    def publish(self, payload: PublishPayload) -> PublishResult:
        """Public entry — resolves token, logs, delegates to ``_do_publish``.

        This is the **only** method the dispatcher calls. Subclasses
        implement ``_do_publish`` with the platform-specific API logic;
        the base owns token resolution + structured logging so that
        behavior is consistent across all 5 platforms.

        If the token is missing, returns a SKIPPED ``PublishResult``
        rather than raising — this matches the existing behavior across
        most clients and lets the publisher continue to other platforms.
        """
        from genlab_core.platforms.models import PublishResult

        token = self.access_token
        if not token:
            self._log.warning(
                "publish skipped — missing token",
                extra={
                    "niche": self.niche_id,
                    "platform": self.platform_id,
                    "env_var": self.TOKEN_ENV_SUFFIX,
                },
            )
            return PublishResult(
                platform=self.platform_id,
                success=False,
                error=(
                    f"missing {self.TOKEN_ENV_SUFFIX} for niche {self.niche_id} "
                    f"(see niche_credentials prefix conventions)"
                ),
            )

        self._log.info(
            "publish attempt",
            extra={"niche": self.niche_id, "platform": self.platform_id},
        )
        return self._do_publish(payload, token)

    @abstractmethod
    def _do_publish(self, payload: PublishPayload, token: str) -> PublishResult:
        """Subclass implements the actual API call.

        Receives the resolved token so subclasses don't need to call
        ``self.access_token`` again. Should return a ``PublishResult``
        with ``success=True/False`` and platform-specific identifiers.
        """
