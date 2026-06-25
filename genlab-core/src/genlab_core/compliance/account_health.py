"""Per-platform account-health signals (PR #566 stub, expanded in PR #570).

Detects the early-warning signals that precede shadowbans + strikes:
engagement-rate cliffs, sudden follower loss, strike count from
platform APIs, repeated policy violations.

## Why this exists

Platform bans rarely come without warning. The patterns that
precede them:

  * **Engagement-rate cliff** — your average reach drops 10x in
    24-48 hours with no content changes. Classic shadowban signal
    on Instagram + TikTok.
  * **Strike count** — YouTube's policy strikes API exposes the
    current count + expiration; 3 strikes = channel termination.
    The system should auto-pause publishing when count > 1.
  * **Reach divergence** — your reels reach 100 viewers, your
    competitors' reach 100K. Algorithmic suppression you can't see
    in the UI, but you can compute from baseline.
  * **Reply-throttling** — your auto-replies stop showing up in
    the comment thread. Meta does this when a pattern looks
    automated.

## Public surface (PR #566 stub)

  get_signal(platform: str, niche_id: str) -> HealthSignal | None
    Returns the current health signal for the niche+platform
    pair, or None when no signal is available (cold-start, no
    baseline yet, or platform-specific implementation doesn't
    exist).

  HealthSignal dataclass — (state, message, evidence).
    state ∈ {'healthy', 'warning', 'critical'}.

## PR #566 status: STUB

This module ships the dataclass + the helper signature. The
implementation is a STUB that returns None for every call. PR
#570 will plug in:

  * Instagram + Facebook: pull from Meta Insights + compare to
    14-day baseline; flag when 24-48h reach drops > 10x.
  * YouTube: pull from YouTube Analytics API + strikes endpoint;
    auto-pause when strike count > 1.
  * TikTok: their Insights API is limited; use posting-success-
    rate as a proxy (videos accepted vs rejected by their
    moderation queue).
  * X: rely on follower-engagement baseline (no shadowban API).
  * Threads: same as X.

The fb_survival_check (existing in `genlab_core.monitoring`) is
the closest precedent — it checks if a published FB post still
exists at 24h. PR #570 expands that pattern to per-platform
health monitoring.

## Why the stub ships in PR #566

Two reasons:

  1. The dashboard's Mission Control card (PR #578) can poll the
     helper TODAY and degrade gracefully on None ('no data yet').
     This lets the frontend ship before the per-platform
     implementations land.

  2. The chokepoint-shape commitment matters: future callers
     can be written assuming get_signal exists. When the per-
     platform implementations land, callers don't need to
     change — the same get_signal(platform, niche) starts
     returning richer data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HealthSignal:
    """Per-platform health snapshot.

    state — 'healthy' | 'warning' | 'critical'
      * healthy: baseline performance, no action needed
      * warning: divergence detected; operator should investigate
      * critical: shadowban-like pattern OR strike count >1;
                  publishing should pause for this niche+platform

    message — human-readable summary for the dashboard card
    evidence — structured detail dict (metric names + values)
               so an operator can drill in
    """

    state: Literal["healthy", "warning", "critical"]
    message: str
    evidence: dict = field(default_factory=dict)


def get_signal(platform: str, niche_id: str) -> HealthSignal | None:
    """Return current health signal for (platform, niche), or None.

    PR #566 stub — always returns None. PR #570 plugs in the per-
    platform implementations.

    Callers should treat None as 'no data; proceed as healthy' —
    same fail-OPEN pattern as the rest of the auth + compliance
    surface. Failing-CLOSED here would pause every publish when
    the monitoring isn't ready yet.
    """
    if not platform or not niche_id:
        return None
    logger.debug(
        "[account_health] stub returning None for platform=%r niche=%r "
        "(PR #570 will plug in real implementations)",
        platform,
        niche_id,
    )
    return None
