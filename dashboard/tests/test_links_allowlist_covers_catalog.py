"""2026-08-12 (F-QB-0702): pin that the /links/go redirect allowlist
covers every domain present in the affiliate catalog.

Motivation: QB-2026-08 Phase 7 flagged 3 clicks / $0 revenue in 30 days
across all niches. Diagnostic showed 4 legitimate AI-tool affiliate
domains (claude.ai, midjourney.com, openai.com, runwayml.com) were
silently blocked by `_ALLOWED_DOMAINS` in `dashboard/server/api/links.py`:

    [Links] Blocked redirect to non-allowlisted domain: claude.ai

The block sends the browser back to the landing page (`_fallback_url`)
without ever calling `log_click`, so:
* User's click never reaches Amazon / Anthropic / Runway
* `affiliate_clicks` gets no row
* Reward loop's `_update_cta_bandit_from_clicks` sees no signal

Silent-fail class: allowlist drift when catalog adds new networks.
This pin fires at CI if the catalog and allowlist ever diverge again.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse


def _extract_domains_from_catalog() -> set[str]:
    """Walk `affiliate_catalog.yaml` and return every URL hostname
    that would end up as an affiliate redirect target.

    Loading YAML instead of grepping so the parser correctly handles
    env-var placeholders + multiline values."""
    import yaml

    catalog_path = (
        Path(__file__).resolve().parents[2]
        / "genlab-core"
        / "config"
        / "affiliate_catalog.yaml"
    )
    if not catalog_path.exists():
        # In some checkouts the yaml may not exist; be defensive.
        # Don't fail this pin on catalog absence — the point is to
        # catch DRIFT, not existence.
        return set()

    catalog = yaml.safe_load(catalog_path.read_text())
    if not isinstance(catalog, dict):
        return set()

    domains: set[str] = set()
    for niche_data in (catalog.get("niches") or {}).values():
        for product in niche_data.get("products", []) or []:
            for network in (product.get("networks") or {}).values():
                url = network.get("url") if isinstance(network, dict) else None
                if not url or not isinstance(url, str):
                    continue
                # Skip env-var placeholders (they can't be validated).
                if url.startswith("${") or "${" in url and url.endswith("}"):
                    continue
                host = urlparse(url).hostname or ""
                if host:
                    # Strip leading "www." — the allowlist matches via
                    # str.endswith so "amazon.in" matches "www.amazon.in".
                    if host.startswith("www."):
                        host = host[4:]
                    if host.startswith("m."):
                        host = host[2:]
                    domains.add(host)
    return domains


def _get_allowlist() -> tuple[str, ...]:
    """Extract `_ALLOWED_DOMAINS` from the link_go function by reading
    the module source. The tuple is a local inside `link_go` so we
    can't just import it — parse it from the source instead."""
    import re

    src_path = (
        Path(__file__).resolve().parents[1]
        / "server"
        / "api"
        / "links.py"
    )
    src = src_path.read_text()
    # Match the _ALLOWED_DOMAINS = ( ... ) block, tolerant of comments
    m = re.search(
        r"_ALLOWED_DOMAINS\s*=\s*\(([^)]+)\)",
        src,
        re.DOTALL,
    )
    if not m:
        return tuple()
    body = m.group(1)
    # Extract quoted strings; ignore any comment content.
    return tuple(re.findall(r'"([^"]+)"', body))


class TestAllowlistCoversCatalog:
    def test_all_catalog_domains_in_allowlist(self):
        """Every hostname in affiliate_catalog.yaml MUST be covered by
        `_ALLOWED_DOMAINS` (via str.endswith). Otherwise `/links/go/<slug>`
        silently redirects back to the landing page and log_click is
        never called — exactly the F-QB-0702 failure mode."""
        catalog_domains = _extract_domains_from_catalog()
        allowlist = _get_allowlist()

        if not catalog_domains:
            # Nothing to check (test-env catalog missing) — treat as pass
            # rather than false-fail on CI worker without prod config.
            return

        assert allowlist, (
            "could not parse _ALLOWED_DOMAINS from links.py — "
            "did the variable get renamed or restructured?"
        )

        uncovered = [
            d for d in sorted(catalog_domains)
            if not any(d.endswith(allowed) for allowed in allowlist)
        ]
        assert not uncovered, (
            f"affiliate_catalog has {len(uncovered)} domain(s) not in "
            f"_ALLOWED_DOMAINS. Every /links/go click for these will "
            f"silently redirect to the landing page and NEVER log a "
            f"click. Add them to _ALLOWED_DOMAINS in dashboard/server/"
            f"api/links.py: {uncovered}"
        )

    def test_allowlist_includes_the_four_fixed_domains(self):
        """Regression pin — the specific 4 AI-tool domains fixed in
        commit b46ef2de must stay in the allowlist."""
        allowlist = _get_allowlist()
        for required in ("claude.ai", "midjourney.com", "openai.com", "runwayml.com"):
            assert required in allowlist, (
                f"regression: {required!r} was removed from allowlist. "
                f"F-QB-0702 confirmed clicks stopped when this was missing."
            )
