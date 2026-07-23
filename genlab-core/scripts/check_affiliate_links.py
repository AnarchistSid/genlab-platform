#!/usr/bin/env python3
"""GenLab Affiliate Link Health Checker.

Loads the affiliate_catalog.yaml, extracts real URLs (skipping example.com
placeholders), HEAD-requests each with a 10s timeout, and reports healthy vs
broken links.

Exit codes:
  0 — script ran + broken rate below 10% (normal state; a few dead URLs
      is expected as merchants remove products or change slugs)
  1 — broken rate >= 10% OR ALL links broken (network outage / catalog
      corruption / merchant-side mass removal — genuine incident)

2026-07-21: threshold-based exit code (rule #26 class-of-bug fix).
Prior behaviour returned exit 1 on ANY broken link, which caused
`service_down` systemd alarms every hour despite 78/80 links being
healthy. Operator saw noise-CRITICAL alerts that obscured real
incidents. Broken links are still fully reported via stdout for
operator triage; the exit code now only fires when a genuine outage
is likely (>= 10% broken).

Usage:
  python genlab-core/scripts/check_affiliate_links.py
  python genlab-core/scripts/check_affiliate_links.py --verbose
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CATALOG_PATH = Path(__file__).parent.parent / "config" / "affiliate_catalog.yaml"

HEALTHY_STATUSES = {200, 206, 301, 302, 303, 307, 308}
REQUEST_TIMEOUT = 10  # seconds
RATE_LIMIT_SLEEP = 0.5  # seconds between requests
USER_AGENT = "GenLab-LinkChecker/1.0"

# 2026-07-21: some endpoints are HEAD/GET-hostile — they return 4xx/5xx to
# automated probes but work fine in real browsers. Marking them broken
# creates noise in the daily report. The URL substrings below indicate
# a request pattern that we KNOW is misleading:
#
# - `amazon.com/s?k=` — Amazon search endpoints throttle automated GET/HEAD
#   requests with 503 even though the same URL loads in a browser
# - `amazon.in/s?k=` — same, on the India TLD
# - `claude.ai/` — the bare product page returns 403 to unauthenticated
#   HEAD/GET but the URL is real + resolves for real users
#
# Any URL matching these substrings is treated as "not check-able via
# HEAD" (skipped in the broken-rate calculation) rather than counted
# as broken. Add new patterns here only after manually confirming the
# URL is fine when opened in a browser.
HEAD_HOSTILE_URL_PATTERNS = (
    "amazon.com/s?k=",
    "amazon.in/s?k=",
    "claude.ai/",
)


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def load_catalog() -> dict:
    """Load and return the affiliate catalog YAML as a dict.

    2026-07-17: delegate to `catalog_loader.load_catalog` so
    ``${AMAZON_US_AFFILIATE_TAG}`` placeholders in URLs get expanded
    via ``os.path.expandvars``. Prior state read raw YAML, so the
    HEAD-request health check saw literal ``?tag=${AMAZON_US_...}``
    URLs — Amazon 404'd every one, producing 4/80 "broken links"
    false alarms that had nothing to do with the runtime publish
    path (which correctly uses catalog_loader). See
    session-2026-07-17 audit round 2 agent 3.
    """
    from genlab_core.monetization.catalog_loader import (
        load_catalog as _canonical_load_catalog,
    )

    return _canonical_load_catalog(CATALOG_PATH)


def parse_catalog_urls(catalog: dict) -> list[dict]:
    """Extract all real affiliate URLs from the catalog.

    Skips any URL that contains 'example.com'.

    Returns a list of dicts, each with keys:
        product  — product name
        network  — affiliate network key (amazon, amazon_us, cuelinks, earnkaro, …)
        niche    — niche key (gaming, sports, movies, anime, ai_creators, …)
        url      — the affiliate URL
    """
    results: list[dict] = []
    niches: dict = catalog.get("niches", {})

    for niche, niche_data in niches.items():
        products = niche_data.get("products", [])
        for product in products:
            product_name: str = product.get("name", "unknown")
            networks: dict = product.get("networks", {})
            for network, network_data in networks.items():
                url: str | None = network_data.get("url")
                if not url:
                    continue
                if "example.com" in url:
                    continue
                results.append(
                    {
                        "product": product_name,
                        "network": network,
                        "niche": niche,
                        "url": url,
                    }
                )

    return results


def check_url(url: str) -> dict:
    """HEAD-request a URL and return a result dict.

    Returns:
        {
            "url": str,
            "status": int | None,
            "healthy": bool,
            "error": str | None,   # only present when there is an error
        }
    """
    # Use GET instead of HEAD — Amazon and Cuelinks reject HEAD with 405.
    # We read only the first few bytes and close the connection immediately.
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Range", "bytes=0-0")  # minimize data transfer

    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            status: int = resp.status
            healthy = status in HEALTHY_STATUSES
            return {"url": url, "status": status, "healthy": healthy}
    except urllib.error.HTTPError as exc:
        status = exc.code
        healthy = status in HEALTHY_STATUSES
        result: dict = {"url": url, "status": status, "healthy": healthy}
        if not healthy:
            result["error"] = str(exc)
        return result
    except urllib.error.URLError as exc:
        reason = str(exc.reason)
        if "timed out" in reason.lower() or "timeout" in reason.lower():
            error_msg = "timeout"
        else:
            error_msg = reason
        return {"url": url, "status": None, "healthy": False, "error": error_msg}
    except TimeoutError:
        return {"url": url, "status": None, "healthy": False, "error": "timeout"}
    except Exception as exc:  # noqa: BLE001
        return {"url": url, "status": None, "healthy": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def auto_disable_broken_products(catalog: dict, broken: list[dict]) -> int:
    """Set ``enabled: false`` on products with ALL networks broken.

    Only disables a product if every real (non-example.com) network URL
    for that product is broken. Modifies the catalog dict in-place.

    Returns the number of products disabled.
    """
    # Build lookup: (niche, product_name) → set of broken network keys
    broken_lookup: dict[tuple[str, str], set[str]] = {}
    for item in broken:
        key = (item["niche"], item["product"])
        broken_lookup.setdefault(key, set()).add(item["network"])

    disabled = 0
    niches = catalog.get("niches", {})
    for niche, niche_data in niches.items():
        for product in niche_data.get("products", []):
            name = product.get("name", "unknown")
            key = (niche, name)
            if key not in broken_lookup:
                continue

            # Count total real networks vs broken networks
            networks = product.get("networks", {})
            real_networks = {
                n
                for n, d in networks.items()
                if d.get("url") and "example.com" not in d.get("url", "")
            }
            broken_networks = broken_lookup[key]

            # Only disable if ALL real networks are broken
            if real_networks and broken_networks >= real_networks:
                product["enabled"] = False
                disabled += 1
                print(f"  AUTO-DISABLED: {niche}/{name} (all {len(real_networks)} networks broken)")

    return disabled


def save_catalog(catalog: dict, path: Path | None = None) -> None:
    """Write the catalog back to YAML, preserving structure."""
    target = path or CATALOG_PATH
    with open(target, "w", encoding="utf-8") as fh:
        yaml.dump(catalog, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)
    print(f"  Catalog saved to {target}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check affiliate link health from affiliate_catalog.yaml"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed status for every URL checked",
    )
    parser.add_argument(
        "--auto-disable",
        action="store_true",
        help="Auto-disable products where ALL network URLs are broken",
    )
    args = parser.parse_args()
    verbose: bool = args.verbose

    # Load catalog and parse URLs
    catalog = load_catalog()
    entries = parse_catalog_urls(catalog)

    if not entries:
        print("No real affiliate URLs found in catalog.")
        sys.exit(0)

    total = len(entries)
    print(f"Checking {total} affiliate URLs ...\n")

    healthy_count = 0
    skipped_head_hostile = 0
    broken: list[dict] = []

    for i, entry in enumerate(entries, start=1):
        # 2026-07-21: skip URLs known to be HEAD/GET-hostile — Amazon
        # search endpoints + auth-required product pages that return
        # 4xx/5xx to automated probes but work fine in browsers. Count
        # them as skipped so they don't inflate the broken count.
        if any(pat in entry["url"] for pat in HEAD_HOSTILE_URL_PATTERNS):
            skipped_head_hostile += 1
            if verbose:
                print(
                    f"  [{i:>2}/{total}] SKIP   HEAD-hostile "
                    f"{entry['niche']}/{entry['network']}  {entry['product']}"
                )
            continue

        result = check_url(entry["url"])

        if result["healthy"]:
            healthy_count += 1
            status_label = f"HTTP {result['status']}"
            if verbose:
                print(
                    f"  [{i:>2}/{total}] OK     {status_label:<10} "
                    f"{entry['niche']}/{entry['network']}  {entry['product']}"
                )
        else:
            broken.append({**entry, **result})
            status_label = (
                f"HTTP {result['status']}" if result["status"] else result.get("error", "unknown")
            )
            print(
                f"  [{i:>2}/{total}] BROKEN {status_label:<10} "
                f"{entry['niche']}/{entry['network']}  {entry['product']}"
            )
            if verbose and result.get("error"):
                print(f"            error: {result['error']}")

        # Rate limit between requests (skip sleep after last request)
        if i < total:
            time.sleep(RATE_LIMIT_SLEEP)

    # Summary
    checked = total - skipped_head_hostile
    print("\n--- Summary ---")
    print(f"  Total in catalog : {total}")
    print(f"  Skipped (HEAD-hostile): {skipped_head_hostile}")
    print(f"  Checked          : {checked}")
    print(f"  Healthy          : {healthy_count}")
    print(f"  Broken           : {len(broken)}")

    if broken:
        print("\nBroken links:")
        for item in broken:
            status_label = str(item["status"]) if item["status"] else item.get("error", "unknown")
            print(f"  [{status_label}] {item['niche']}/{item['network']} — {item['product']}")
            print(f"        {item['url']}")

        # Auto-disable products with all networks broken
        if args.auto_disable:
            print("\n--- Auto-disable ---")
            disabled = auto_disable_broken_products(catalog, broken)
            if disabled > 0:
                save_catalog(catalog)
                print(f"  {disabled} product(s) auto-disabled and catalog saved.")
            else:
                print("  No products needed full disabling (some networks still healthy).")

        # 2026-07-21: threshold-based exit (rule #26). Broken links are
        # already reported via stdout above; exit code only signals a
        # genuine incident (mass outage / merchant removal spree) so
        # systemd doesn't service_down-CRITICAL every hour on the
        # 2-3 known-dead URLs. Uses `checked` (excludes HEAD-hostile
        # skips) so the denominator matches what was actually probed.
        broken_rate = len(broken) / max(checked, 1)
        BROKEN_RATE_THRESHOLD = 0.10
        if broken_rate >= BROKEN_RATE_THRESHOLD:
            print(
                f"\n⚠️  Broken rate {broken_rate:.1%} >= "
                f"{BROKEN_RATE_THRESHOLD:.0%} threshold — exiting 1 "
                f"(likely network outage or mass merchant removal)"
            )
            sys.exit(1)
        else:
            print(
                f"\n{len(broken)}/{total} broken ({broken_rate:.1%}) — "
                f"below {BROKEN_RATE_THRESHOLD:.0%} incident threshold; "
                f"exiting 0. Broken URLs listed above for operator triage."
            )
            sys.exit(0)
    else:
        print("\nAll affiliate links are healthy.")
        sys.exit(0)


def _main_with_durable_error() -> int:
    """Wrapper around main() that preserves any unhandled exception
    traceback to /opt/genlab/.runtime/check_affiliate_links_last_error.txt
    BEFORE exiting. Same pattern as publisher, nightly-scheduler,
    shared_ingestion. See
    genlab_core.observability.durable_error.
    """
    try:
        main()
        return 0
    except SystemExit as e:
        # main() uses sys.exit() liberally — preserve its exit code
        # without treating it as an unhandled exception.
        return int(e.code) if isinstance(e.code, int) else 0
    except Exception as exc:  # noqa: BLE001
        from genlab_core.observability.durable_error import write_durable_error

        write_durable_error("check_affiliate_links", exc)
        return 3


if __name__ == "__main__":
    sys.exit(_main_with_durable_error())
