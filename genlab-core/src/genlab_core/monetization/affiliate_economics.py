"""Pydantic loader for affiliate_economics.yaml.

Single source of truth for the per-niche average order value, per-network
commission rate, and click-to-conversion assumption. Used by both:

* :mod:`dashboard.server.api.revenue` — real-time estimate computed
  inline from click counts (display only).
* :mod:`genlab_core.monetization.proxy_revenue_aggregator` — daily
  batch that writes proxy revenue rows into ``affiliate_revenue`` so
  the dashboard's "actual" column lights up before real revenue
  ingestion lands.

Keeping both consumers on the same loader means the dashboard's
real-time estimate and the persisted proxy stay numerically
identical — drift between them would otherwise confuse anyone
comparing the two columns.

Audit ref: R-32.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class AffiliateEconomics(BaseModel):
    """The full set of economic assumptions, validated."""

    avg_order_inr: dict[str, float] = Field(default_factory=dict)
    default_avg_order_inr: float = 11640.0
    commission_pct: dict[str, float] = Field(default_factory=dict)
    default_commission_pct: float = 0.05
    conversion_rate: float = 0.02
    default_currency: str = "INR"

    def avg_order_for(self, niche_id: str) -> float:
        """Per-niche average order value, falling back to the default."""
        return self.avg_order_inr.get(niche_id, self.default_avg_order_inr)

    def commission_for(self, network: str) -> float:
        """Per-network commission percentage, falling back to the default."""
        return self.commission_pct.get(network, self.default_commission_pct)

    def estimate_revenue(self, *, niche_id: str, network: str, clicks: int) -> float:
        """The single canonical formula.

        ``clicks × conversion_rate × avg_order × commission``

        Returns INR. Both call sites use this so dashboard display and
        persisted proxy rows match to the rupee.
        """
        return (
            clicks
            * self.conversion_rate
            * self.avg_order_for(niche_id)
            * self.commission_for(network)
        )


def _config_path() -> Path:
    """Locate ``affiliate_economics.yaml`` next to the other genlab-core configs."""
    return Path(__file__).resolve().parents[3] / "config" / "affiliate_economics.yaml"


@lru_cache(maxsize=1)
def get_affiliate_economics() -> AffiliateEconomics:
    """Load + cache the YAML. Use this everywhere.

    Returns a default-populated model when the YAML is missing — this
    keeps tests + fresh installs running on the same hard-coded defaults
    that previously lived in ``dashboard/server/api/revenue.py``.
    """
    p = _config_path()
    if not p.exists():
        logger.warning("[affiliate_economics] config missing at %s — using defaults", p)
        return AffiliateEconomics()
    try:
        raw = yaml.safe_load(p.read_text()) or {}
        return AffiliateEconomics(**raw)
    except Exception as exc:
        # Catch YAML parse errors AND Pydantic validation errors —
        # a broken config must never break the dashboard / aggregator.
        logger.exception("[affiliate_economics] failed to load %s: %s", p, exc)
        return AffiliateEconomics()


def reset_cache() -> None:
    """Drop the lru_cache. Used in tests that need fresh reads.

    No-op when ``get_affiliate_economics`` has been monkeypatched out
    (the bound name may no longer carry ``.cache_clear``).
    """
    fn = get_affiliate_economics
    if hasattr(fn, "cache_clear"):
        fn.cache_clear()
