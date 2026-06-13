"""Impact.com per-advertiser deep-link generation — task #34c (2026-06-13).

Impact.com is a network-of-networks: every brand the operator
partners with has its own subdomain (``acme.sjv.io``), advertiser ID,
and campaign ID. Unlike ShareASale or CJ (one publisher ID + per-product
ad_id is enough), Impact's tracking URLs require a per-advertiser
config map.

How #34c differs from a full API integration
============================================

Impact.com also exposes a REST API for programmatic deep-link
generation. That requires:
  * OAuth account credentials with periodic token rotation
  * Per-advertiser campaign-ID discovery (an extra GET per advertiser)
  * Rate-limit handling per their tier

This commit ships the **template-driven** path instead: the operator
maintains a YAML map of ``advertiser_id → {subdomain, campaign_id}``,
and ``build_deep_link`` substitutes the tracking template:

    https://{subdomain}/c/{account_sid}/{advertiser_id}/{campaign_id}?u={encoded_target_url}&subId1={sub_id}

Operator adds a row to the YAML the moment they partner with a new
brand — no code change. The OAuth API path is a future #34d when
volume justifies it (the template covers ~95% of Impact use cases).

Config file
===========

``genlab-core/config/impact_advertisers.yaml`` — defaults to an
empty advertisers map. The operator adds a row per partnered
advertiser:

    advertisers:
      acme_corp:
        subdomain: acme.sjv.io
        advertiser_id: "12345"
        campaign_id: "67890"
      brand_b:
        subdomain: brand-b.7eer.net
        advertiser_id: "98765"
        campaign_id: "54321"

When the affiliate matcher tries Impact for a product, it passes the
operator-chosen ``advertiser_id`` kwarg; the client looks up the
config row, substitutes, and returns the URL. Missing config row →
returns "" (graceful skip).
"""

from __future__ import annotations

import logging
import os
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_FILENAME = "impact_advertisers.yaml"
_REQUIRED_FIELDS = ("subdomain", "advertiser_id", "campaign_id")


@dataclass(frozen=True)
class ImpactAdvertiser:
    """One row from the Impact advertisers config file."""

    advertiser_key: str
    subdomain: str
    advertiser_id: str
    campaign_id: str


_advertisers_cache: dict[str, ImpactAdvertiser] | None = None
_advertisers_cache_path: Path | None = None


def _resolve_config_path() -> Path:
    """Find the impact_advertisers.yaml config.

    Order:
      1. IMPACT_ADVERTISERS_CONFIG env var (absolute path) — for tests
         and per-environment overrides
      2. genlab-core/config/impact_advertisers.yaml (workspace default)
    """
    env_override = os.environ.get("IMPACT_ADVERTISERS_CONFIG", "").strip()
    if env_override:
        return Path(env_override)

    # Walk up from this file: monetization/impact_client.py →
    # genlab_core/ → src/ → genlab-core/
    pkg_root = Path(__file__).resolve().parent.parent.parent.parent
    return pkg_root / "config" / _DEFAULT_CONFIG_FILENAME


def load_advertisers(*, force_reload: bool = False) -> dict[str, ImpactAdvertiser]:
    """Load the per-advertiser config from disk. Cached per-process.

    Returns an empty dict (not None) when the file is missing — the
    operator has simply not partnered with any Impact advertisers yet,
    which is the current default state.

    Returns
    -------
    dict[str, ImpactAdvertiser]
        Map of advertiser_key → ImpactAdvertiser. Invalid rows
        (missing required fields, wrong types) are skipped with a
        warning instead of raising — one bad row doesn't disable the
        whole network.
    """
    global _advertisers_cache, _advertisers_cache_path

    config_path = _resolve_config_path()

    # Cache invalidation: if the resolved path changed (e.g. tests
    # toggle IMPACT_ADVERTISERS_CONFIG between cases), discard the
    # cached map.
    if not force_reload and _advertisers_cache is not None:
        if _advertisers_cache_path == config_path:
            return _advertisers_cache

    if not config_path.exists():
        _advertisers_cache = {}
        _advertisers_cache_path = config_path
        return _advertisers_cache

    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning(
            "[impact] failed to read advertisers config %s: %s — treating as empty",
            config_path,
            exc,
        )
        _advertisers_cache = {}
        _advertisers_cache_path = config_path
        return _advertisers_cache

    advertisers_block = data.get("advertisers") if isinstance(data, dict) else None
    if not isinstance(advertisers_block, dict):
        _advertisers_cache = {}
        _advertisers_cache_path = config_path
        return _advertisers_cache

    parsed: dict[str, ImpactAdvertiser] = {}
    for key, row in advertisers_block.items():
        if not isinstance(row, dict):
            logger.warning(
                "[impact] advertisers.%s is not a mapping — skipping", key
            )
            continue
        if any(not row.get(f) for f in _REQUIRED_FIELDS):
            logger.warning(
                "[impact] advertisers.%s missing required fields (%s) — skipping",
                key,
                ", ".join(_REQUIRED_FIELDS),
            )
            continue
        parsed[str(key)] = ImpactAdvertiser(
            advertiser_key=str(key),
            subdomain=str(row["subdomain"]),
            advertiser_id=str(row["advertiser_id"]),
            campaign_id=str(row["campaign_id"]),
        )

    _advertisers_cache = parsed
    _advertisers_cache_path = config_path
    return parsed


def get_advertiser(advertiser_key: str) -> ImpactAdvertiser | None:
    """Return the advertiser config or None if not registered."""
    if not advertiser_key:
        return None
    return load_advertisers().get(advertiser_key)


def build_deep_link(
    *,
    advertiser_key: str,
    target_url: str,
    account_sid: str | None = None,
    sub_id: str = "",
) -> str:
    """Construct the Impact tracking URL from the per-advertiser template.

    Parameters
    ----------
    advertiser_key:
        Lookup key in impact_advertisers.yaml. Operator chooses these
        slugs when adding advertisers.
    target_url:
        The merchant landing page the click should redirect to.
    account_sid:
        Defaults to ``IMPACT_ACCOUNT_SID`` env var if not provided.
        Required (returns "" when missing).
    sub_id:
        Tracking sub-ID (passed as ``subId1``). Conventionally the
        ``niche_id_blueprintHash`` slug.

    Returns
    -------
    str
        Templated tracking URL, or "" if any required input is missing.
        Empty string lets the matcher fall back to the next network.
    """
    if not target_url:
        return ""
    sid = account_sid if account_sid is not None else os.environ.get("IMPACT_ACCOUNT_SID", "")
    sid = sid.strip()
    if not sid:
        return ""

    advertiser = get_advertiser(advertiser_key)
    if advertiser is None:
        # Not necessarily an error — the operator may not have onboarded
        # this advertiser yet. Logged at debug; matcher tries next network.
        logger.debug(
            "[impact] advertiser_key=%s not in config — graceful skip",
            advertiser_key,
        )
        return ""

    base = (
        f"https://{advertiser.subdomain}/c/{sid}/"
        f"{advertiser.advertiser_id}/{advertiser.campaign_id}"
    )
    params: dict[str, str] = {"u": target_url}
    if sub_id:
        params["subId1"] = sub_id
    return f"{base}?{urllib.parse.urlencode(params)}"


def _clear_cache_for_tests() -> None:
    """Reset the module-level cache. Tests use this between cases that
    swap the IMPACT_ADVERTISERS_CONFIG env var to ensure no cross-
    test pollution."""
    global _advertisers_cache, _advertisers_cache_path
    _advertisers_cache = None
    _advertisers_cache_path = None
