"""Pre-publish credential resolution + proactive token refresh.

Two narrowly-scoped functions used by the publisher orchestrator
(:mod:`genlab_core.publishing.publish_all_platforms`) immediately
before each platform-client call:

* :func:`resolve_client_kwargs` — per-platform constructor kwargs from
  the per-niche credential store. Returns ``None`` when credentials
  are missing, which the caller turns into a per-platform SKIP rather
  than a crash.
* :func:`preflight_token_refresh` — single proactive Threads-token
  refresh per run (R-35 mitigation; the only client with an idempotent
  ``refresh_token_if_needed()`` hook today). Strictly non-fatal — any
  failure logs a WARNING and publishing proceeds.

Lives in its own module so the multi-platform publisher orchestrator
stays focused on the orchestration flow. Extracted in the refactor-#9
decomposition (PR 4/N). Re-exported from the orchestrator with
``_``-prefixed aliases so existing tests
(``test_publish_all_platforms.TestPreflightTokenRefresh``,
``_CRED_PATCH = ...``) keep working byte-stable.
"""

from __future__ import annotations

import logging
import os

from genlab_core.platforms.registry import get_client
from genlab_core.publishing.niche_credentials import (
    resolve_fb_credentials,
    resolve_meta_credentials,
    resolve_niche_env,
    resolve_threads_credentials,
    resolve_twitter_credentials,
    resolve_youtube_credentials,
)
from genlab_core.publishing.platform_status import to_registry_id

logger = logging.getLogger(__name__)


def resolve_client_kwargs(registry_id: str, niche_id: str) -> dict | None:
    """Resolve per-niche constructor kwargs for a platform client.

    Returns ``None`` when credentials for ``registry_id`` are missing
    or incomplete — the caller treats ``None`` as "skip this platform
    for this run" and writes a SKIPPED record (never silent-fail).

    Unknown ``registry_id`` returns an empty ``{}`` (the caller can
    construct the client with no kwargs); this is intentional to keep
    a new platform pluggable before it has any niche-prefixed env vars
    declared.
    """
    # P2 phase 1 activation (2026-06-19): all 5 platform clients accept
    # ``niche_id`` as a kw-only arg (PRs #377/#378/#379). Pass it through
    # so the client knows its operating niche — stored on ``self.niche_id``,
    # available for per-niche logging / metrics / future per-niche rate
    # limiting. Explicit token kwargs still take priority in each client's
    # internal resolver, so per-niche re-resolution doesn't double up with
    # the explicit values this function already provides.
    if registry_id == "instagram":
        creds = resolve_meta_credentials(niche_id)
        token = creds.get("ig_access_token", "")
        user_id = creds.get("ig_user_id", "")
        if token and user_id:
            return {"access_token": token, "ig_user_id": user_id, "niche_id": niche_id}
        return None

    if registry_id == "facebook":
        token, page_id = resolve_fb_credentials(niche_id)
        if token and page_id:
            return {"access_token": token, "page_id": page_id, "niche_id": niche_id}
        return None

    if registry_id == "youtube":
        creds = resolve_youtube_credentials(niche_id)
        refresh_token = creds.get("refresh_token", "")
        client_id = creds.get("client_id", "") or os.environ.get("YOUTUBE_CLIENT_ID", "")
        client_secret = creds.get("client_secret", "") or os.environ.get(
            "YOUTUBE_CLIENT_SECRET", ""
        )
        # Expected channel ID for cross-channel verification.
        expected_channel = resolve_niche_env(niche_id, "", "YT_CHANNEL_ID")
        if refresh_token and client_id and client_secret:
            kwargs = {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "niche_id": niche_id,
            }
            if expected_channel:
                kwargs["expected_channel_id"] = expected_channel
            return kwargs
        return None

    if registry_id == "x_twitter":
        creds = resolve_twitter_credentials(niche_id)
        if all(creds.values()):
            # Defensive copy — don't mutate the resolver's returned dict
            return {**creds, "niche_id": niche_id}
        return None

    if registry_id == "threads":
        token, user_id = resolve_threads_credentials(niche_id)
        if token and user_id:
            return {"access_token": token, "user_id": user_id, "niche_id": niche_id}
        return None

    return {}


def preflight_token_refresh(niche_id: str, enabled_platforms: list[str]) -> None:
    """R-35: proactively refresh near-expiry tokens before publishing.

    Threads tokens are 60-day and today refresh only *lazily*, inside the
    Threads client's ``publish()``. A channel dark for >10 days never reaches
    that code, so its token can silently lapse and surface only as a per-
    platform SKIP (compounding R-01). ``refresh_token_if_needed()`` is
    idempotent — it acts only when the token is ≥50 days old — so calling
    it once per run is safe and shrinks the dark-channel window.

    Strictly non-fatal: any failure logs a WARNING and publishing proceeds
    (the per-platform publish will surface a real auth failure as a SKIP
    regardless).

    NOTE: this does not eliminate R-35 — a channel that never runs the
    publisher still won't refresh. Closing that fully needs a host-level
    token-health timer, which is an ops concern tracked separately.
    """
    for platform in enabled_platforms:
        registry_id = to_registry_id(platform)
        # Only Threads has a proactive, idempotent refresh hook today. The
        # EAA Meta page token is permanent (never refreshed — security rule),
        # and YouTube/X refresh on use; revisit if other clients gain the hook.
        if registry_id != "threads":
            continue
        try:
            kwargs = resolve_client_kwargs(registry_id, niche_id)
            if not kwargs:
                logger.warning(
                    "[publish] token preflight: no %s credentials for niche '%s'",
                    registry_id,
                    niche_id,
                )
                continue
            client = get_client(registry_id, **kwargs)
            refresh = getattr(client, "refresh_token_if_needed", None)
            if callable(refresh):
                refresh()
        except Exception as exc:
            logger.warning("[publish] token preflight refresh failed for %s: %s", registry_id, exc)
