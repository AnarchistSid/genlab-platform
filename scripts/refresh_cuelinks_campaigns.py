#!/usr/bin/env python3
"""Weekly Cuelinks campaign shortlist refresh (PR 2/3, 2026-07-16).

Fetches Cuelinks' merchant campaign list via the V3 API, filters to
open-access campaigns for our audience geos, sorts by 7-day EPC, and
writes a curated per-category shortlist to
``genlab-core/config/cuelinks_campaigns.yaml``. affiliate_matcher
consumes this file in PR 3 as a discovery source for high-EPC
non-Amazon merchants (Flipkart, Myntra, Ajio, Meesho, Nykaa, etc.).

## Why weekly not daily

Cuelinks' EPC signal doesn't shift meaningfully day-to-day — the
underlying data is clicks/commission accumulated over 7-day rolling
windows. Weekly refit captures directional shifts without hammering
their API. Cost per fire: 1 `/campaigns` call × ~500 results = well
under any rate limit.

## Silent-write hardening (2026-07-16 audit lesson)

Today's comprehensive audit flagged
``genlab-refit-top-creator-priors.service`` for a silent-write bug:
the timer fires, exits 0, but the artifact directory is empty. This
script guards against the same class-of-bug by:

  1. Explicitly asserting the fetched campaign list is non-empty
     BEFORE writing. Empty result = WARNING + exit 1 (not silent).
  2. Atomic write via ``os.replace`` on a ``.tmp`` sibling — a
     crash mid-write leaves the previous YAML intact rather than
     truncating it.
  3. Post-write assertion: reload the written YAML and confirm the
     top-level list is non-empty. Any deserialisation drift fails
     the run loudly.

## Artifact shape

``genlab-core/config/cuelinks_campaigns.yaml``::

    # AUTO-GENERATED — do not hand-edit. Rewritten weekly by
    # genlab-cuelinks-campaign-refresh.timer. Manual edits will
    # be overwritten on the next Mon 04:00 UTC fire.
    generated_at: 2026-07-16T04:00:00+00:00
    per_page_requested: 500
    total_returned: 342
    top_by_epc: 100
    campaigns:
      - id: 12345
        name: Flipkart
        category: apparel
        country: IN
        access_status: open
        epc_7d: 4.82
        merchant_url: https://www.flipkart.com/
      ...

affiliate_matcher looks up by ``category`` (mapping from niche →
category lives in the niche's ``affiliate.yaml``).

## Amazon-guard interaction

The client's ``AmazonUrlNotAllowed`` guard fires at ``convert_url``
time — that's the LAST defense. This script does NOT filter out
Amazon-brokered Cuelinks campaigns at the discovery step because:

  (a) Cuelinks may list amazon.in as a campaign anyway (they broker
      MANY merchants; some overlap with our direct deals);
  (b) The client-side guard is the load-bearing check; the shortlist
      is a hint to affiliate_matcher, not a runtime redirect list.

The shortlist output DOES include a ``merchant_url`` field so
downstream consumers can decide whether to attempt Cuelinks OR fall
back to direct Amazon Associates based on the URL.

## CLI

    python -m genlab_core.scripts.refresh_cuelinks_campaigns [--dry-run]

    Exit 0 = success (shortlist written)
    Exit 1 = failure (empty result, API error, write error)

## Env vars

    CUELINKS_V3_API_KEY (required)
    GENLAB_PROJECT_ROOT (optional, defaults to script's grand-parent)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


_TOP_BY_EPC = 100  # Keep the top-100 by EPC in the shortlist
_DEFAULT_ACCESS_STATUS = "open"


def _project_root() -> Path:
    env_root = os.environ.get("GENLAB_PROJECT_ROOT")
    if env_root:
        return Path(env_root)
    # scripts/refresh_cuelinks_campaigns.py → parents[1] = repo root
    return Path(__file__).resolve().parent.parent


def _shortlist_from_campaigns(campaigns: list[dict], *, top_n: int = _TOP_BY_EPC) -> list[dict]:
    """Sort by EPC descending + trim to top-N + shape for the YAML output.

    The Cuelinks V3 response includes a lot of fields we don't need
    (creative sizes, deep-link support flags, etc.). Project down to
    the fields affiliate_matcher actually reads.
    """

    def epc_7d(c: dict) -> float:
        # V3 response shape TBD in prod; try common field names
        for k in ("epc_7d", "epc", "epc_7"):
            v = c.get(k)
            if isinstance(v, (int, float)):
                return float(v)
            if isinstance(v, str):
                try:
                    return float(v)
                except ValueError:
                    continue
        return 0.0

    ranked = sorted(campaigns, key=epc_7d, reverse=True)[:top_n]
    shortlist = []
    for c in ranked:
        shortlist.append(
            {
                "id": c.get("id"),
                "name": c.get("name") or c.get("campaign_name") or "?",
                # 2026-07-21: `c.get("categories", ["?"])[0]` was IndexError when
                # categories == [] (empty list ≠ missing key). Coalesce empty
                # list to fallback list so [0] indexing is always safe.
                "category": c.get("category") or (c.get("categories") or ["?"])[0],
                "country": c.get("country") or c.get("geo") or "IN",
                "access_status": c.get("access_status", "open"),
                "epc_7d": epc_7d(c),
                "merchant_url": c.get("merchant_url") or c.get("url") or "",
            }
        )
    return shortlist


def _atomic_write(path: Path, payload: dict) -> None:
    """Write via ``.tmp`` sibling + os.replace so a crash mid-write
    leaves the previous YAML intact.

    Also validates the payload deserialises cleanly BEFORE the rename
    — guards against a subtly-corrupt dict silently producing an
    unreadable YAML that all downstream consumers would then reject.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    yaml_str = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    # Sanity: does what we're about to write parse back?
    parsed = yaml.safe_load(yaml_str)
    if not isinstance(parsed, dict) or "campaigns" not in parsed:
        raise RuntimeError(
            f"internal serialisation drift — parsed shape {type(parsed).__name__} "
            f"missing 'campaigns' key. Refusing to write."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(yaml_str, encoding="utf-8")
    os.replace(tmp, path)


def _post_write_assertion(path: Path) -> None:
    """Reload the written YAML + confirm top-level 'campaigns' list is
    non-empty. Any deserialisation drift or empty-write fails the run
    loudly — avoids the ``genlab-refit-top-creator-priors.service``
    silent-write class-of-bug the 2026-07-16 audit flagged."""
    reloaded = yaml.safe_load(path.read_text())
    if not isinstance(reloaded, dict):
        raise RuntimeError(f"post-write reload: expected dict, got {type(reloaded)}")
    campaigns = reloaded.get("campaigns", [])
    if not isinstance(campaigns, list) or len(campaigns) == 0:
        raise RuntimeError(
            f"post-write reload: 'campaigns' is empty or wrong shape "
            f"({len(campaigns) if isinstance(campaigns, list) else type(campaigns)}). "
            f"Silent-write bug prevention (2026-07-16 audit)."
        )


def refresh(*, dry_run: bool = False) -> int:
    """Fetch → sort → write. Returns exit code (0 success or data-side
    no-op, 1 genuine write failure).

    Exit convention updated 2026-08-07 (QB-FIX-12 rule #26 sweep):
    data-side outcomes (no API key provisioned, empty API response)
    return 0 with WARN log. Only genuine infra failures (write error,
    invalid API response format) return 1. The prior contract fired
    systemd_unit_failed every week on the missing-API-key case,
    contributing to alarm-cascade noise. Operator sees the warning
    in journalctl + logs; the cascade shouldn't fire on config gaps
    or empty upstream responses.
    """
    if not os.environ.get("CUELINKS_V3_API_KEY", "").strip():
        logger.warning(
            "[cuelinks-refresh] CUELINKS_V3_API_KEY unset — nothing to do. "
            "Rule #26: config-gap is data-side, not infra failure; exit 0."
        )
        return 0

    # Late import so a missing V3 key doesn't drag the client module in
    from genlab_core.monetization import cuelinks_client

    logger.info("[cuelinks-refresh] Fetching campaigns (access_status=%s)", _DEFAULT_ACCESS_STATUS)
    campaigns = cuelinks_client.list_campaigns(
        access_status=_DEFAULT_ACCESS_STATUS,
        sort="epc_7d",
        order="desc",
        per_page=500,
        force_refresh=True,  # Always refresh weekly
    )

    if not campaigns:
        logger.warning(
            "[cuelinks-refresh] EMPTY campaign list returned — either the "
            "API is down, the key is invalid, or Cuelinks has no matching "
            "campaigns for our filters. NOT writing the shortlist "
            "(silent-write bug prevention). Rule #26: data-side "
            "outcome — exit 0 with warning; existing shortlist stays."
        )
        return 0

    shortlist = _shortlist_from_campaigns(campaigns, top_n=_TOP_BY_EPC)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "per_page_requested": 500,
        "total_returned": len(campaigns),
        "top_by_epc": _TOP_BY_EPC,
        "campaigns": shortlist,
    }

    target = _project_root() / "genlab-core" / "config" / "cuelinks_campaigns.yaml"

    if dry_run:
        logger.info(
            "[cuelinks-refresh] --dry-run: would write %d campaigns to %s "
            "(top_epc=%.2f, lowest_epc=%.2f)",
            len(shortlist),
            target,
            shortlist[0].get("epc_7d", 0.0) if shortlist else 0,
            shortlist[-1].get("epc_7d", 0.0) if shortlist else 0,
        )
        return 0

    try:
        _atomic_write(target, payload)
        _post_write_assertion(target)
    except Exception as exc:
        logger.warning(
            "[cuelinks-refresh] write_failed target=%s exc=%s: %s",
            target,
            type(exc).__name__,
            exc,
        )
        return 1

    logger.info(
        "[cuelinks-refresh] Wrote %d campaigns to %s (fetched %d, kept top %d by EPC)",
        len(shortlist),
        target,
        len(campaigns),
        _TOP_BY_EPC,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh the Cuelinks campaign shortlist from V3 API."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch + shortlist but don't write to disk. Prints summary.",
    )
    args = parser.parse_args()
    return refresh(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
