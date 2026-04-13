"""Affiliate product matching pipeline stage.

Scans story content for keyword matches against the affiliate catalog and
selects the best network (highest commission) for each match.

Two matching strategies:
1. **Keyword matching** (fast, free) — regex word-boundary matching against product keywords
2. **LLM matching** (Claude Haiku fallback) — contextual understanding when keywords fail

The LLM matcher is only invoked when keyword matching returns zero hits, keeping
costs minimal (~$0.00005 per LLM call at ~200 input tokens).

Usage:
    stage = AffiliateMatch()
    context = stage.execute(context)
"""

from __future__ import annotations

import json as _json
import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml

from genlab_core.monetization.seasonal import load_seasonal_config, get_seasonal_products

logger = logging.getLogger(__name__)

# Absolute path to the shared affiliate catalog.
# Path: affiliate_matcher.py → monetization/ → genlab_core/ → src/ → genlab-core/
_CATALOG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "affiliate_catalog.yaml"


# ── LLM-powered contextual matching ──────────────────────────────────────────

def _build_llm_product_list(products: list[dict[str, Any]]) -> str:
    """Build a compact product list string for the LLM prompt."""
    lines = []
    for i, p in enumerate(products):
        name = p.get("name", "")
        category = p.get("category", "")
        keywords = ", ".join(str(k) for k in (p.get("keywords") or [])[:5])
        lines.append(f"{i}: {name} [{category}] (keywords: {keywords})")
    return "\n".join(lines)


def _llm_match_product(
    text: str,
    niche_id: str,
    products: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Use Claude Haiku to contextually match content to a product.

    Only called when keyword matching fails. Returns the best product or None.

    Cost: ~200 input tokens + ~20 output tokens = ~$0.00005 per call
    at Claude Haiku pricing ($0.25/M input, $1.25/M output).
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.debug("[AffiliateMatch] No ANTHROPIC_API_KEY — skipping LLM match")
        return None

    # Filter to enabled products only
    enabled = [p for p in products if p.get("enabled", True)]
    if not enabled:
        return None

    product_list = _build_llm_product_list(enabled)

    # Truncate content to keep token count low (~150 tokens max)
    content_truncated = text[:500]

    prompt = (
        f"You are a product matcher for a {niche_id} content channel.\n\n"
        f"Content:\n\"{content_truncated}\"\n\n"
        f"Products:\n{product_list}\n\n"
        "Which product (if any) is most relevant to this content? "
        "Reply with ONLY the product index number (e.g. '3') or 'none' if no product fits. "
        "A product fits if the content topic naturally relates to it — "
        "the viewer of this content would plausibly be interested in the product."
    )

    try:
        from genlab_core.writing.llm_client import AnthropicLLMClient

        client = AnthropicLLMClient(api_key=api_key, model="claude-haiku-4-5-20251001")
        answer = client.complete(
            system="You are a product matcher. Reply with ONLY a product index number or 'none'.",
            user=prompt,
            max_tokens=20,
            temperature=0.0,
        ).strip().lower()

        if answer == "none" or not answer:
            logger.debug("[AffiliateMatch] LLM returned 'none' — no contextual match")
            return None

        # Parse the index
        # Handle responses like "3" or "3 - PS5 Console" or "Product 3"
        idx_match = re.search(r"\d+", answer)
        if not idx_match:
            logger.debug("[AffiliateMatch] LLM response not parseable: %s", answer)
            return None

        idx = int(idx_match.group())
        if 0 <= idx < len(enabled):
            product = enabled[idx]
            logger.info(
                "[AffiliateMatch] LLM contextual match: '%s' (index=%d)",
                product.get("name", ""),
                idx,
            )
            return product

        logger.debug("[AffiliateMatch] LLM returned out-of-range index: %d", idx)
        return None

    except Exception as e:
        logger.warning("[AffiliateMatch] LLM match failed: %s", e)
        return None


def _load_catalog(catalog_path: Path | None = None) -> dict[str, Any]:
    """Load the affiliate catalog YAML from disk.

    Expands ``${ENV_VAR}`` placeholders in all affiliate URLs so that
    tags like ``${AMAZON_US_AFFILIATE_TAG}`` resolve to their actual
    values from the environment.
    """
    path = catalog_path or _CATALOG_PATH
    with open(path, "r", encoding="utf-8") as fh:
        catalog = yaml.safe_load(fh)

    # Expand ${...} env var placeholders in all network URLs
    for niche_data in (catalog.get("niches") or {}).values():
        for product in niche_data.get("products") or []:
            for net_info in (product.get("networks") or {}).values():
                url = net_info.get("url", "")
                if "${" in url:
                    net_info["url"] = os.path.expandvars(url)

    return catalog


def _keyword_hits(keywords: list, text_lower: str, *, return_matched: bool = False) -> int | tuple[int, list[str]]:
    """Count keyword matches using word-boundary regex.

    Uses ``\\b`` word boundaries instead of substring containment to prevent
    false positives like ``"mat"`` matching ``"post-match"`` or ``"led"``
    matching ``"called"``.  Casts each keyword to ``str`` to handle YAML
    auto-parsed integers (e.g. ``4090``).  Skips empty/None keywords.

    If *return_matched* is True, returns ``(hits, matched_keywords)`` instead
    of just *hits*.
    """
    hits = 0
    matched: list[str] = []
    for kw in keywords:
        kw_str = str(kw).lower().strip() if kw else ""
        if not kw_str:
            continue
        if re.search(r"\b" + re.escape(kw_str) + r"\b", text_lower):
            hits += 1
            if return_matched:
                matched.append(kw_str)
    if return_matched:
        return hits, matched
    return hits


_DEFAULT_MAX_PRICE_INR = 2500  # Impulse-buy threshold for short-form video viewers


def _price_filter(products: list, max_price_inr: int = _DEFAULT_MAX_PRICE_INR) -> list:
    """Filter products to those at or below the max price.

    High-ticket items (>₹2500) have near-zero conversion rate from
    short-form video traffic. Restrict to impulse-buy zone by default.
    Falls through to all products if no cheap matches exist.
    """
    cheap = [p for p in products if 0 < p.get("price_inr", 0) <= max_price_inr]
    return cheap if cheap else products


def match_product(
    text: str,
    niche_id: str,
    catalog: dict[str, Any],
    seasonal_config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Scan text for keyword matches against niche products.

    Checks seasonal products first (if any active events), then falls back
    to static catalog. Returns the product with the most keyword hits, or None.
    Uses word-boundary matching to prevent substring false positives.
    Filters to impulse-buy price range (≤₹2500) by default.
    """
    # Strip hashtags and existing affiliate CTAs from search text to prevent
    # self-referencing circular matches and hashtag keyword pollution.
    clean_text = re.sub(r"#\w+", "", text)  # Remove hashtags
    clean_text = re.sub(r"🔗.*?link in bio", "", clean_text, flags=re.IGNORECASE)
    text_lower = clean_text.lower()

    logger.debug("[AffiliateMatch] Search text (%d chars): %s...", len(text_lower), text_lower[:100])

    best_product: dict[str, Any] | None = None
    best_hits = 0
    best_matched_keywords: list[str] = []

    # 1. Check seasonal products first (highest priority during events)
    if seasonal_config is None:
        try:
            seasonal_config = load_seasonal_config()
        except Exception:
            seasonal_config = {}

    seasonal_products = get_seasonal_products(seasonal_config)
    for product in seasonal_products:
        # Only match seasonal products for their target niche (or all if unspecified)
        product_niche = product.get("niche_id", "")
        if product_niche and product_niche != niche_id:
            continue
        keywords = product.get("keywords", [])
        hits, matched_kws = _keyword_hits(keywords, text_lower, return_matched=True)
        if hits > best_hits:
            best_hits = hits
            best_product = product
            best_matched_keywords = matched_kws

    # If seasonal match found with 2+ keyword hits, return it
    if best_product is not None and best_hits >= 2:
        logger.info(
            "[AffiliateMatch] Seasonal match: '%s' (%d hits, keywords=%s, event: %s)",
            best_product.get("name", ""),
            best_hits,
            best_matched_keywords,
            best_product.get("_seasonal_event", ""),
        )
        return best_product

    # Reset if seasonal had only 1 weak hit — let static catalog try
    if best_hits < 2:
        best_product = None
        best_hits = 0
        best_matched_keywords = []

    # 2. Fall back to static catalog (skip disabled products).
    # Apply price filter: only consider impulse-buy items unless niche overrides.
    raw_niche_products = (catalog.get("niches") or {}).get(niche_id, {}).get("products", [])
    if not raw_niche_products:
        return None
    niche_max_price = (
        (catalog.get("niches") or {}).get(niche_id, {}).get("max_price_inr")
        or _DEFAULT_MAX_PRICE_INR
    )
    enabled = [p for p in raw_niche_products if p.get("enabled", True)]
    niche_products = _price_filter(enabled, niche_max_price)
    if len(niche_products) < len(enabled):
        logger.debug(
            "[AffiliateMatch] Price filter for %s: %d/%d products under ₹%d",
            niche_id, len(niche_products), len(enabled), niche_max_price,
        )

    for product in niche_products:
        keywords = product.get("keywords") or []
        hits, matched_kws = _keyword_hits(keywords, text_lower, return_matched=True)
        if hits > best_hits:
            best_hits = hits
            best_product = product
            best_matched_keywords = matched_kws

    if best_product is not None and best_hits > 0:
        logger.debug(
            "[AffiliateMatch] Static match: '%s' (%d hits, keywords=%s)",
            best_product.get("name", ""),
            best_hits,
            best_matched_keywords,
        )
        return best_product

    # 3. LLM contextual fallback — only when keyword matching failed entirely.
    # Use the same price-filtered list (impulse-buy zone only).
    enabled_products = niche_products
    if not enabled_products:
        return None

    # Only invoke LLM if the cleaned text has enough substance (>20 chars of content)
    if len(text_lower.strip()) > 20:
        llm_result = _llm_match_product(text_lower, niche_id, enabled_products)
        if llm_result is not None:
            return llm_result

    return None


def select_best_network(product: dict[str, Any]) -> tuple[str, str, float]:
    """Return (network_name, url, commission_pct) for the network with the highest commission.

    Skips placeholder URLs (example.com) and validates link health via the
    geo_link_resolver health cache. Falls back to lower-commission networks
    if the highest-commission link is broken.
    """
    networks: dict[str, dict[str, Any]] = product.get("networks") or {}
    if not networks:
        return ("", "", 0.0)

    from genlab_core.monetization.geo_link_resolver import _is_placeholder, _is_url_healthy

    # Sort by commission descending so we try the best link first
    ranked = sorted(
        networks.items(),
        key=lambda kv: float(kv[1].get("commission_pct", 0.0)),
        reverse=True,
    )

    for name, info in ranked:
        url = info.get("url", "")
        if _is_placeholder(url):
            continue
        if not _is_url_healthy(url):
            logger.debug(
                "[AffiliateMatch] Skipping broken %s link for %s",
                name, product.get("name", "?"),
            )
            continue
        commission = float(info.get("commission_pct", 0.0))
        return (name, url, commission)

    return ("", "", 0.0)


class AffiliateMatch:
    """Pipeline stage: match affiliate products to stories.

    Loads the affiliate catalog, scans each story's text for keyword matches,
    selects the best affiliate network, and enriches the story dict with
    affiliate fields. Non-fatal: stories without a match are left unchanged.
    """

    def __init__(
        self,
        catalog_path: Path | None = None,
        seasonal_config_path: Path | None = None,
    ) -> None:
        self._catalog_path = catalog_path
        self._seasonal_config_path = seasonal_config_path

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        stories: list[dict[str, Any]] = context.get("stories", [])
        if not stories:
            logger.info("[AffiliateMatch] No stories to process")
            context.setdefault("run_stats", {})["affiliate"] = {
                "matched": 0, "skipped": 0, "cap_enforced": 0,
            }
            return context

        niche_id: str = context.get("niche_id", "")

        # Load catalog
        try:
            catalog = _load_catalog(self._catalog_path)
        except Exception as exc:
            logger.warning("[AffiliateMatch] Could not load catalog: %s — skipping", exc)
            context.setdefault("run_stats", {})["affiliate"] = {
                "matched": 0, "skipped": len(stories), "cap_enforced": 0,
                "error": str(exc),
            }
            return context

        # Load seasonal config once for the whole run
        try:
            seasonal_config = load_seasonal_config(self._seasonal_config_path)
        except Exception as exc:
            logger.warning("[AffiliateMatch] Could not load seasonal config: %s — using empty", exc)
            seasonal_config = {}

        catalog_settings: dict[str, Any] = catalog.get("settings") or {}
        max_per_day: int = int(catalog_settings.get("max_affiliate_posts_per_day", 3))
        disclosure_map: dict[str, str] = catalog_settings.get("disclosure_text") or {}

        matched = 0
        skipped = 0
        cap_enforced = 0

        for story in stories:
            # Respect daily cap
            if matched >= max_per_day:
                cap_enforced += 1
                logger.debug(
                    "[AffiliateMatch] Cap of %d reached — skipping story '%s'",
                    max_per_day,
                    story.get("title", "")[:60],
                )
                continue

            # Build search text from hook + caption + title
            content = story.get("content") or {}
            if isinstance(content, str):
                try:
                    import json
                    content = json.loads(content)
                except (json.JSONDecodeError, TypeError):
                    content = {}

            hook = content.get("hook", "")
            ig_caption = (content.get("instagram") or {}).get("caption", "")
            title = story.get("title", "")
            search_text = f"{hook} {ig_caption} {title}"

            # 1. Try dynamic LLM-based subject extraction first.
            # This produces context-relevant Amazon search URLs that
            # outperform generic catalog matches by ~10x conversion.
            from genlab_core.monetization.dynamic_matcher import dynamic_match
            from genlab_core.monetization.geo_link_resolver import NICHE_PRIMARY_GEO
            geo = NICHE_PRIMARY_GEO.get(niche_id, "IN")
            product = dynamic_match(search_text, niche_id, geo=geo)

            # 2. Fall back to static catalog if dynamic match fails
            if product is None:
                product = match_product(search_text, niche_id, catalog, seasonal_config)
            if product is None:
                skipped += 1
                logger.debug(
                    "[AffiliateMatch] No product match for story '%s'",
                    title[:60],
                )
                continue

            network_name, url, commission_pct = select_best_network(product)
            if not url:
                skipped += 1
                logger.debug(
                    "[AffiliateMatch] Product '%s' has no network URL — skipping",
                    product.get("name", ""),
                )
                continue

            product_name: str = product.get("name", "")

            # Geo-targeted link resolution with UTM tracking.
            # Returns BOTH url and the actual network used (geo-priority may
            # differ from commission-priority used by select_best_network).
            from genlab_core.monetization.geo_link_resolver import (
                resolve_affiliate_link_with_network,
            )
            blueprint_id = story.get("_candidate_id", story.get("story_id", ""))
            tracked_url, resolved_network = resolve_affiliate_link_with_network(
                product=product,
                niche_id=niche_id,
                platform="instagram",  # default; cta_engine overrides per-platform
                blueprint_id=blueprint_id,
            )
            if tracked_url:
                url = tracked_url
                # Sync network_name to whatever resolve_affiliate_link picked,
                # so the DB label matches the actual URL provider.
                if resolved_network:
                    network_name = resolved_network
                    # Update commission_pct to match the resolved network
                    resolved_info = (product.get("networks") or {}).get(resolved_network, {})
                    commission_pct = float(resolved_info.get("commission_pct", commission_pct))

            # Build CTA — actionable, price-aware, urgency-driven.
            # "link in bio" has near-zero CTR; show price + emoji + verb.
            price = product.get("price_inr", 0)
            if price and price < 1000:
                cta = f"🛒 Get {product_name} for ₹{price} — link in 1st comment 👇"
            elif price and price < 5000:
                cta = f"🔥 {product_name} — only ₹{price}. Link in 1st comment 👇"
            elif price:
                cta = f"⭐ {product_name} (₹{price}) — link below 👇"
            else:
                cta = f"🔗 Get {product_name} — link in 1st comment 👇"

            story["affiliate_product"] = product_name
            story["affiliate_url"] = url
            story["affiliate_network"] = network_name
            story["affiliate_commission_pct"] = commission_pct
            story["affiliate_cta"] = cta
            story["affiliate_price_inr"] = int(product.get("price_inr", 0) or 0)
            story["_affiliate_disclosure_map"] = disclosure_map

            matched += 1
            logger.info(
                "[AffiliateMatch] Matched '%s' → %s via %s (%.1f%%)",
                title[:60],
                product_name,
                network_name,
                commission_pct,
            )

        context.setdefault("run_stats", {})["affiliate"] = {
            "matched": matched,
            "skipped": skipped,
            "cap_enforced": cap_enforced,
        }
        logger.info(
            "[AffiliateMatch] %d matched, %d skipped, %d cap-enforced",
            matched, skipped, cap_enforced,
        )
        return context
