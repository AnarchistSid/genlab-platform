"""Pure-function helpers that read/derive per-platform publish state.

These all operate on the ``platform_publish_status`` JSON column that
SharePoint Blueprints carry — a small dict mapping platform name to
either a bare status string (legacy) or a status dict
(``{"status": ..., "error_class": ..., "attempts": ...}``, current).

Lives in its own module so the multi-platform publisher orchestrator
(:mod:`genlab_core.publishing.publish_all_platforms`) stays focused on
publishing flow. Extracted from there in the refactor-#9 decomposition
(PR 1/N). Re-exported from the orchestrator for backwards-compatible
imports.
"""

from __future__ import annotations

import json
from typing import Any

# Maps legacy/alternate platform names to canonical registry IDs. Used at
# the seam between SharePoint blueprint state (which carries human-typed
# values like "twitter") and the platform-client registry (which uses
# "x_twitter").
PLATFORM_ID_MAP: dict[str, str] = {
    "twitter": "x_twitter",
    "x": "x_twitter",
    "ig": "instagram",
    "yt": "youtube",
    "fb": "facebook",
    "tt": "tiktok",
}

# Error classes the retry loop refuses to retry — these mean human attention
# is needed (a token rotation, a content fix, a deletion etc).
TERMINAL_ERROR_CLASSES: frozenset[str] = frozenset({"CREDENTIAL", "CONTENT", "PERMANENT"})

# After this many attempts even retryable errors are considered terminal,
# to keep the retry loop from chewing on a permanently-broken platform.
TERMINAL_ATTEMPT_THRESHOLD: int = 3


def to_registry_id(platform: str) -> str:
    """Map a legacy/alternate platform name to its canonical registry ID."""
    return PLATFORM_ID_MAP.get(platform, platform)


def already_published_platforms(fields: dict[str, Any]) -> set[str]:
    """Platforms already PUBLISHED for this blueprint per its persisted state.

    R-29/R-83: a partial publish (or crash mid-publish) leaves per-platform
    PUBLISHED markers in ``platform_publish_status``. Re-publishing the
    blueprint (after a crash-recovery reset, or a retry-only run) MUST skip
    these so a succeeded platform is never double-posted. Handles both the
    bare-string (``"PUBLISHED"``) and dict (``{"status": "PUBLISHED"}``)
    value shapes.
    """
    raw = fields.get("platform_publish_status", "{}")
    try:
        pps = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (json.JSONDecodeError, TypeError):
        return set()
    if not isinstance(pps, dict):
        return set()
    return {
        p
        for p, v in pps.items()
        if v == "PUBLISHED" or (isinstance(v, dict) and v.get("status") == "PUBLISHED")
    }


def terminal_failed_platforms(
    platform_status: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Return platforms whose failure won't be auto-resolved by the retry loop.

    A failure is terminal when ``error_class`` is non-retryable
    (CREDENTIAL/CONTENT/PERMANENT) or attempts have hit
    :data:`TERMINAL_ATTEMPT_THRESHOLD`. Used to surface partial-publish
    events to the dashboard so silent platform failures aren't lost behind
    the blueprint's overall PUBLISHED status.
    """
    out: dict[str, dict[str, Any]] = {}
    for plat, val in platform_status.items():
        if not isinstance(val, dict) or val.get("status") != "FAILED":
            continue
        error_class = val.get("error_class", "TRANSIENT")
        attempts = int(val.get("attempts", 0) or 0)
        if error_class in TERMINAL_ERROR_CLASSES or attempts >= TERMINAL_ATTEMPT_THRESHOLD:
            out[plat] = val
    return out


def transient_failed_platforms(
    platform_status: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Return platforms that failed with a retryable error this run.

    Audit R-36 — the dashboard previously only saw TERMINAL failures via
    :func:`terminal_failed_platforms`. A run where 1/5 platforms succeeded
    and 4 hit TRANSIENT errors (rate-limit, timeout, 5xx) showed up as
    ``publish_success`` because no terminal failure existed. This helper
    surfaces the "queued for retry" tier so the dashboard can render an
    honest partial state — the retry pass will pick these up, but the
    operator needs to know they're not OK *yet*.
    """
    out: dict[str, dict[str, Any]] = {}
    for plat, val in platform_status.items():
        if not isinstance(val, dict) or val.get("status") != "FAILED":
            continue
        error_class = val.get("error_class", "TRANSIENT")
        attempts = int(val.get("attempts", 0) or 0)
        # Anything failed but NOT terminal is, by definition, transient.
        if error_class not in TERMINAL_ERROR_CLASSES and attempts < TERMINAL_ATTEMPT_THRESHOLD:
            out[plat] = val
    return out


def published_platforms(
    platform_status: dict[str, Any],
) -> list[str]:
    """Return the platforms whose status this run is ``PUBLISHED``.

    Matches the shape :data:`success_platforms` is built from in the
    orchestrator; centralised so the partial-state check can use it
    too without duplicating the bare-string / dict shape handling.
    """
    out = []
    for plat, val in platform_status.items():
        if val == "PUBLISHED":
            out.append(plat)
        elif isinstance(val, dict) and val.get("status") == "PUBLISHED":
            out.append(plat)
    return out
