"""Shared affiliate-catalog loader (canonical implementation).

Why this module exists
----------------------
Before 2026-06-17, two divergent ``_load_catalog`` implementations
existed:

  * ``genlab_core/monetization/affiliate_matcher.py:_load_catalog``
    correctly called ``os.path.expandvars()`` so URLs with
    placeholders like ``${AMAZON_US_AFFILIATE_TAG}`` resolved to
    real affiliate tags.
  * ``dashboard/server/api/links.py:_load_catalog`` did NOT call
    expandvars — URLs flowed to Amazon with the literal
    ``${AMAZON_US_AFFILIATE_TAG}`` string. Amazon silently rejected
    the unknown tag and 135 historical clicks earned zero
    commission attribution (fixed in PR #272).

The two implementations diverged because the dashboard's loader was
added later without referencing the canonical one. This module is
the single source of truth — both callers now import from here.

What this module does NOT do
----------------------------
- **No caching**: each caller maintains its own cache (the dashboard
  has a 5-minute in-memory TTL; affiliate_matcher loads per-stage).
  Keeping cache concerns out of the loader keeps it pure and easy
  to test.
- **No file-path policy**: callers pass the path. Path defaults live
  with the caller (different processes, different roots).

Usage
-----
    from pathlib import Path
    from genlab_core.monetization.catalog_loader import load_catalog

    catalog = load_catalog(Path("/opt/genlab/genlab-core/config/affiliate_catalog.yaml"))
    # catalog["niches"]["gaming"]["products"][0]["networks"]["amazon_us"]["url"]
    # → 'https://amazon.com/dp/X?tag=aspirehub06-20'  (placeholder expanded)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


def expand_env_vars(catalog: dict[str, Any]) -> dict[str, Any]:
    """Expand ``${ENV_VAR}`` placeholders in all affiliate URLs.

    Walks ``catalog["niches"][*]["products"][*]["networks"][*]["url"]``
    and runs ``os.path.expandvars()`` on any URL containing ``${``.

    Mutates *catalog* in place AND returns it for chaining.

    Behaviour on missing env vars: ``os.path.expandvars()`` leaves
    the literal ``${VAR}`` substring intact when the variable isn't
    set. This is intentional graceful degradation — a missing env
    var produces a broken-but-introspectable URL rather than an
    exception or empty string. Operators can spot ``${...}`` in
    ``affiliate_clicks.affiliate_url`` and trace back to a missing
    env config.

    Edge cases handled:
      - ``catalog["niches"]`` missing or None: returns *catalog* unchanged
      - A product with ``networks=None`` or no networks: skipped
      - A network with no ``url`` key: skipped
      - URL without ``${``: passes through unchanged (perf micro-opt)
    """
    for niche_data in (catalog.get("niches") or {}).values():
        for product in niche_data.get("products") or []:
            for net_info in (product.get("networks") or {}).values():
                url = net_info.get("url", "")
                if url and "${" in url:
                    net_info["url"] = os.path.expandvars(url)
    return catalog


def load_catalog(catalog_path: Path) -> dict[str, Any]:
    """Load the affiliate catalog YAML from disk + expand env-var URLs.

    The single canonical loader. Replaces two divergent
    implementations: ``affiliate_matcher._load_catalog`` and
    ``dashboard/server/api/links._load_catalog``. Both now delegate
    here so a future bug-fix only needs to happen in one place.

    Args:
        catalog_path: Path to ``affiliate_catalog.yaml``. Required;
            callers must supply since path defaults are per-process.

    Returns:
        Catalog dict with all ``${ENV_VAR}`` placeholders in
        affiliate URLs expanded via ``os.path.expandvars()``.

    Raises:
        FileNotFoundError / yaml.YAMLError: propagated to caller.
        Each caller decides how to handle missing/malformed catalogs
        (the dashboard logs WARN + returns stale cache; affiliate_
        matcher logs WARN + returns empty dict + skips the run).
        Keeping error policy at the caller boundary lets each
        consumer pick its own resilience strategy.
    """
    with open(catalog_path, encoding="utf-8") as fh:
        catalog = yaml.safe_load(fh) or {}
    return expand_env_vars(catalog)
