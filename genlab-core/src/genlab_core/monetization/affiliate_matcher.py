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

import logging
import os
import re
from pathlib import Path
from typing import Any

from genlab_core.monetization.seasonal import get_seasonal_products, load_seasonal_config

logger = logging.getLogger(__name__)


def slugify_product_name(name: str) -> str:
    """Return the canonical slug for a product name.

    Layer 3 PR 2 (2026-07-07) — the JOIN key that makes click →
    blueprint attribution possible. Blueprints historically stored the
    display name ("NVIDIA RTX 4090"); ``affiliate_clicks`` stores the
    slug ("nvidia-rtx-4090") because the bio-link hub at
    ``review.aspirehub.ai/links/go/<slug>`` uses slug URLs. Without a
    canonical shared key, correlation queries return 0 for every
    niche (audit found this on 2026-07-07 — 30 days of clicks with
    zero attributable blueprint IDs).

    Rule (matches what the bio-link hub emits):
      1. lowercase
      2. drop everything that isn't a-z, 0-9, whitespace, or hyphen
      3. collapse runs of whitespace/underscore to a single hyphen
      4. de-double consecutive hyphens
      5. strip leading/trailing hyphens

    >>> slugify_product_name("NVIDIA RTX 4090")
    'nvidia-rtx-4090'
    >>> slugify_product_name("Xbox Game Pass Ultimate")
    'xbox-game-pass-ultimate'
    >>> slugify_product_name("PS5 Console")
    'ps5-console'
    >>> slugify_product_name("Claude Pro Subscription")
    'claude-pro-subscription'
    >>> slugify_product_name("")
    ''
    """
    if not name:
        return ""
    lowered = name.lower()
    # Keep _ initially so it can act as a separator in step 3; strip
    # everything else outside [a-z0-9\s_-].
    kept = re.sub(r"[^a-z0-9\s_\-]", "", lowered)
    collapsed = re.sub(r"[\s_]+", "-", kept)
    collapsed = re.sub(r"-+", "-", collapsed)
    return collapsed.strip("-")


# Absolute path to the shared affiliate catalog.
# Path: affiliate_matcher.py → monetization/ → genlab_core/ → src/ → genlab-core/
_CATALOG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "affiliate_catalog.yaml"


def _count_today_affiliate_blueprints(niche_id: str) -> int:
    """Return the count of blueprints created today for ``niche_id`` that
    already carry an affiliate match.

    Used to enforce ``max_affiliate_posts_per_day`` as a true daily cap
    across pipeline runs.  Without this, two scheduled runs for the same
    niche (e.g. cron at 02:00 + manual triage at 14:00) each got their
    own ``max_per_day`` allowance, doubling the real daily total.

    Returns 0 on any DB failure — better to over-attribute affiliates
    than to block the pipeline because of a transient query error.
    """
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if not db_url:
        # 2026-07-14 (class-of-bug scan): WARNING was silent-0 return.
        # `return 0` means "0 posts today → cap DISABLED for this
        # niche" — if ops accidentally unsets DATABASE_URL in an env,
        # monetization loses its daily-cap guardrail with no signal.
        # WARNING makes the fault visible; the return-0 behavior is
        # preserved as fail-open per the "never block pipeline on
        # transient query error" docstring.
        logger.warning(
            "[affiliate_matcher] DATABASE_URL unset for niche=%s — "
            "daily cap check disabled (cap treated as 0 posts today, "
            "cap gate bypassed). Set DATABASE_URL to enforce.",
            niche_id,
        )
        return 0
    try:
        import psycopg  # noqa: F401 — kept for the except ImportError clause

        from genlab_core.storage.tenant_context import pg_connect  # SR-A/C/D Tier-3
    except ImportError:
        logger.warning(
            "[affiliate_matcher] psycopg import failed — daily cap check disabled for niche=%s",
            niche_id,
        )
        return 0
    try:
        with pg_connect(db_url, connect_timeout=5, niche_id=niche_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) FROM blueprints
                    WHERE niche_id = %s
                      AND affiliate_product IS NOT NULL
                      AND created_at::date = CURRENT_DATE
                    """,
                    (niche_id,),
                )
                row = cur.fetchone()
                return int(row[0]) if row else 0
    except Exception as exc:
        logger.warning(
            "[AffiliateMatch] Daily-cap query failed (%s) — treating as 0",
            exc,
        )
        return 0


def _load_catalog(catalog_path: Path | None = None) -> dict[str, Any]:
    """Load the affiliate catalog YAML from disk.

    Delegates to the canonical ``genlab_core.monetization.catalog_loader``
    so this implementation can never drift from the dashboard's loader
    (see ``catalog_loader.py`` docstring for the history that motivated
    the extraction). Local wrapper preserves the optional-``path``
    signature this module's callers expect.
    """
    from genlab_core.monetization.catalog_loader import load_catalog

    return load_catalog(catalog_path or _CATALOG_PATH)


def _keyword_hits(
    keywords: list, text_lower: str, *, return_matched: bool = False
) -> int | tuple[int, list[str]]:
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


def _log_selector_divergence(
    *,
    niche_id: str,
    matcher_pick: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> None:
    """Log the ProductSelector's top pick alongside the keyword matcher's.

    L3 PR 5 (2026-07-07): observation-only wire. When the selector is
    enabled AND registered arms exist, we score the same candidate set
    the keyword matcher chose from. If the bandit's top pick differs
    from the matcher's, that divergence is what a future PR will use
    to decide whether to flip enforcement per niche.

    Fail-open at every layer:
    * Selector disabled → immediate return (no cost to production path).
    * Selector raises → caught, logged at DEBUG, matcher continues.
    * Empty candidate list → immediate return (nothing to rank).

    The matcher's return value is NOT affected. This function is purely
    a side-channel observability wire.

    Observability contract:
    * INFO log ``[AffiliateMatch] selector agreed`` when matcher &
      selector converge — useful for computing agreement rate.
    * INFO log ``[AffiliateMatch] selector DIVERGES`` when they differ
      — includes both picks + their scores for offline analysis.
    """
    try:
        from genlab_core.monetization.product_selector import (
            is_enabled,
            select_products,
        )

        if not is_enabled():
            return
        if not candidates:
            return

        ranked = select_products(niche_id, candidates, top_k=1)
        if not ranked:
            # Selector returned empty — no registered arms or all
            # slugs empty. Not a divergence signal, just cold-start.
            return

        selector_pick, selector_score = ranked[0]
        matcher_name = matcher_pick.get("name", "")
        selector_name = selector_pick.get("name", "")

        if matcher_name == selector_name:
            logger.info(
                "[AffiliateMatch] selector agreed: %s (niche=%s, score=%.3f)",
                matcher_name,
                niche_id,
                selector_score,
            )
        else:
            logger.info(
                "[AffiliateMatch] selector DIVERGES: matcher=%s selector=%s "
                "(niche=%s, selector_score=%.3f, matcher pick will be used — "
                "observation-only mode)",
                matcher_name,
                selector_name,
                niche_id,
                selector_score,
            )

        # L3 PR 12a (2026-07-07): persist the divergence to
        # ``selector_divergences`` for the analyzer stats endpoint.
        # Best-effort — journalctl already recorded the event above,
        # so the DB write is a bonus for analyzable data. Any failure
        # here (missing table, DB unreachable) is caught + DEBUG-logged
        # by the outer try/except.
        _persist_divergence(
            niche_id=niche_id,
            matcher_name=matcher_name,
            selector_name=selector_name,
            selector_score=selector_score,
            agreed=(matcher_name == selector_name),
        )
    except Exception as exc:  # noqa: BLE001 — never let observability
        # break the production matcher path
        logger.debug(
            "[AffiliateMatch] selector divergence check failed: %s (matcher path unaffected)",
            exc,
        )


def _persist_divergence(
    *,
    niche_id: str,
    matcher_name: str,
    selector_name: str,
    selector_score: float,
    agreed: bool,
) -> None:
    """Best-effort append to ``selector_divergences`` (L3 PR 12a).

    The row is stored with the CANONICAL slug (via
    ``slugify_product_name``) rather than the display name so the
    analyzer can JOIN back to bandit_arms via ``product__<slug>``
    if needed. Storing display names would fork the JOIN key set
    yet again (the exact bug L3 PR 2 fixed for blueprints).

    Fail-open at every layer — the caller's journalctl log has
    already recorded the divergence; the DB write is a queryable
    bonus, not a correctness requirement.
    """
    try:
        import os

        dsn = os.environ.get("DATABASE_URL", "").strip()
        if not dsn:
            return

        # slugify the display names so the analyzer can JOIN back to
        # bandit_arms via product__<slug>.
        matcher_slug = slugify_product_name(matcher_name)
        selector_slug = slugify_product_name(selector_name)
        if not matcher_slug or not selector_slug:
            return

        # 2026-07-07 (test_migrated_site_no_bare_psycopg_connect_call):
        # affiliate_matcher.py is a _MIGRATED_SITE — bare psycopg.connect
        # is banned here. Use pg_connect + explicit niche_id so the
        # RLS policy on selector_divergences sees the correct tenant
        # for the INSERT (though there's no per-niche RLS filter today,
        # the discipline scales to Phase-2 multi-tenant without a
        # re-migration).
        from genlab_core.storage.tenant_context import pg_connect

        with pg_connect(dsn, niche_id=niche_id, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO selector_divergences (
                        niche_id, matcher_pick, selector_pick,
                        selector_score, agreed
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        niche_id,
                        matcher_slug,
                        selector_slug,
                        float(selector_score),
                        agreed,
                    ),
                )
                conn.commit()
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.debug(
            "[AffiliateMatch] divergence persistence failed: %s "
            "(journalctl log preserved; matcher path unaffected)",
            exc,
        )


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

    logger.debug(
        "[AffiliateMatch] Search text (%d chars): %s...", len(text_lower), text_lower[:100]
    )

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
    niche_max_price = (catalog.get("niches") or {}).get(niche_id, {}).get(
        "max_price_inr"
    ) or _DEFAULT_MAX_PRICE_INR
    # 2026-06-30 (OPS #2 + #6): products flagged ``source_tool: true``
    # are owned by the niche's PlatformAdaptation strategy
    # (BlackboxBrief's source-tool detector) — they should NEVER be
    # matched by the generic content-keyword path. Their keywords
    # exist so the catalog stays self-documenting + so the dashboard's
    # link-in-bio surface can index them, but generic matching against
    # the post copy ("the new midjourney v7 looks crazy") would lock
    # them in via the generic stage and prevent the source-detector
    # from gating attachment on the SOURCE video's tool name.
    enabled = [
        p for p in raw_niche_products if p.get("enabled", True) and not p.get("source_tool", False)
    ]
    niche_products = _price_filter(enabled, niche_max_price)
    if len(niche_products) < len(enabled):
        logger.debug(
            "[AffiliateMatch] Price filter for %s: %d/%d products under ₹%d",
            niche_id,
            len(niche_products),
            len(enabled),
            niche_max_price,
        )

    # L3 PR 5 (2026-07-07): collect ALL candidates with >= 1 hit for
    # optional bandit reranking. Prior loop only tracked the top-hits
    # product; we now also carry the runners-up so the ProductSelector
    # can rerank by (posterior × value) when the flag is on. Falls
    # through to prior behaviour when the flag is off.
    keyword_candidates: list[dict[str, Any]] = []

    for product in niche_products:
        keywords = product.get("keywords") or []
        hits, matched_kws = _keyword_hits(keywords, text_lower, return_matched=True)
        if hits > 0:
            keyword_candidates.append(product)
        if hits > best_hits:
            best_hits = hits
            best_product = product
            best_matched_keywords = matched_kws

    # R-31: raise the static catalog match threshold from "any single
    # hit" to ">= 2 hits", mirroring the existing seasonal-match bar
    # (line ~188). The audit found single-hit matches were noisy enough
    # to be misleading — one accidental keyword overlap (e.g. a gaming
    # blueprint mentioning a "trigger" word that's also in a peripheral
    # product's keyword list) routed real revenue to a tenuous CTA.
    # Tighter threshold here ⇒ falls through to evergreen (still a
    # calibrated revenue path) ⇒ overall match quality up, not lost
    # revenue. Single-hit matches stay logged at INFO for visibility.
    _STATIC_MIN_HITS = 2

    if best_product is not None and best_hits >= _STATIC_MIN_HITS:
        # L3 PR 5: bandit-driven rerank, observation-only by default.
        #
        # Rerank layer: consider only the candidates that passed the
        # keyword threshold (best_hits >= _STATIC_MIN_HITS AND ties on
        # a comparable hit count — currently we pass all >= 1-hit
        # candidates and let the bandit score them). The selector is
        # flag-gated + fail-open, so this call collapses to a no-op
        # when disabled or when no arms are registered yet.
        #
        # Observation-only: even when the selector picks a different
        # product than the matcher, we RETURN the matcher's pick and
        # only LOG the divergence. A future PR (L3 PR 12) will flip
        # the enforce flag per-niche after operator validation of the
        # divergence logs.
        matcher_pick = best_product
        _log_selector_divergence(
            niche_id=niche_id,
            matcher_pick=matcher_pick,
            candidates=keyword_candidates,
        )

        logger.debug(
            "[AffiliateMatch] Static match: '%s' (%d hits, keywords=%s)",
            best_product.get("name", ""),
            best_hits,
            best_matched_keywords,
        )
        return best_product

    if best_product is not None and best_hits == 1:
        # Log the rejected weak match so an operator scanning logs can
        # see whether a niche's catalog is under-tuned (lots of 1-hit
        # rejects mean keywords are too narrow). Fall through to the
        # evergreen path below.
        logger.info(
            "[AffiliateMatch] Rejected weak 1-hit match for %s: '%s' (kw=%s) — "
            "needs >= %d hits; falling through to evergreen.",
            niche_id,
            best_product.get("name", ""),
            best_matched_keywords,
            _STATIC_MIN_HITS,
        )
        # Reset so the evergreen branch doesn't think a real match exists.
        best_product = None
        best_hits = 0
        best_matched_keywords = []

    # 3. Evergreen fallback: every niche may designate one product as
    # `evergreen_default: true`. When specific keyword matching misses,
    # we serve the evergreen so the post still gets a monetization slot
    # instead of zero affiliate. Examples: gaming→Game Pass, anime→Figure
    # Collection, movies→Prime Video, sports→Fitness Tracker,
    # ai_creators→Claude Pro. These are niche-defining, broadly relevant
    # products that work as CTAs regardless of the specific story.
    evergreen = next(
        (p for p in enabled if p.get("evergreen_default") is True),
        None,
    )
    if evergreen is not None:
        logger.info(
            "[AffiliateMatch] Evergreen fallback for %s: '%s' (no keyword match)",
            niche_id,
            evergreen.get("name", ""),
        )
        return evergreen

    # No keyword match AND no evergreen configured. Caller can attempt
    # dynamic match (only re-enabled when PA-API is configured — see
    # affiliate_matcher.execute gate).
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
                name,
                product.get("name", "?"),
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
                "matched": 0,
                "skipped": 0,
                "cap_enforced": 0,
            }
            return context

        niche_id: str = context.get("niche_id", "")

        # Load catalog
        try:
            catalog = _load_catalog(self._catalog_path)
        except Exception as exc:
            logger.warning("[AffiliateMatch] Could not load catalog: %s — skipping", exc)
            context.setdefault("run_stats", {})["affiliate"] = {
                "matched": 0,
                "skipped": len(stories),
                "cap_enforced": 0,
                "error": str(exc),
            }
            return context

        # 2026-06-22 audit fix: per-niche affiliate disable. The audit
        # found that 4 of 5 niches (gaming/sports/anime/ai_creators)
        # had 12-19x worse affiliate click-through than movies because
        # of viewer-intent mismatch (Ronaldo trailer → Fitness Tracker
        # was a real produced reel). Until each niche has a per-reel-
        # specific catalog, operators can disable affiliate matching
        # entirely for poor-fit niches via:
        #   niches:
        #     gaming:
        #       affiliate_enabled: false
        #       products: [...]
        # Default ``true`` preserves backward compat. False → stage
        # short-circuits with skipped=len(stories) and no affiliate
        # fields written to any story.
        niche_cfg = (catalog.get("niches") or {}).get(niche_id) or {}
        if niche_cfg.get("affiliate_enabled", True) is False:
            logger.info(
                "[AffiliateMatch] niche=%s has affiliate_enabled=false — "
                "skipping affiliate matching for all %d stories",
                niche_id,
                len(stories),
            )
            context.setdefault("run_stats", {})["affiliate"] = {
                "matched": 0,
                "skipped": len(stories),
                "cap_enforced": 0,
                "disabled_by_niche": True,
            }
            return context

        # Load seasonal config once for the whole run
        try:
            seasonal_config = load_seasonal_config(self._seasonal_config_path)
        except Exception as exc:
            logger.warning("[AffiliateMatch] Could not load seasonal config: %s — using empty", exc)
            seasonal_config = {}

        catalog_settings: dict[str, Any] = catalog.get("settings") or {}
        # ``max_affiliate_posts_per_day`` is the per-niche-per-DAY cap.
        # The in-loop counter handles this run; the already-affiliated
        # blueprint count from the DB carries the rest of today.
        max_per_day: int = int(catalog_settings.get("max_affiliate_posts_per_day", 3))
        disclosure_map: dict[str, str] = catalog_settings.get("disclosure_text") or {}

        # Pre-charge the in-loop counter with today's already-affiliated
        # blueprints from the DB for this niche.  Without this, every
        # pipeline run effectively reset the cap — gaming could run at
        # 02:00 and 14:00 and produce 2*3=6 affiliated posts/day, not 3.
        existing_today = _count_today_affiliate_blueprints(niche_id)
        matched = existing_today
        skipped = 0
        cap_enforced = 0
        if existing_today:
            logger.info(
                "[AffiliateMatch] %s: %d affiliate blueprint(s) already exist today — "
                "cap remaining = %d/%d",
                niche_id,
                existing_today,
                max(0, max_per_day - existing_today),
                max_per_day,
            )

        for story in stories:
            # Respect daily cap (true daily, not per-run)
            if matched >= max_per_day:
                cap_enforced += 1
                logger.debug(
                    "[AffiliateMatch] Daily cap of %d reached — skipping story '%s'",
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

            # 1. Static catalog FIRST.  Curated products (with real
            #    networks, real prices, real keywords) beat LLM-extracted
            #    Amazon search URLs almost every time — and crucially, the
            #    static catalog has been hand-vetted for product fit.
            #
            #    Dynamic match falls back only when keyword matching against
            #    the catalog finds nothing.  Previously this was reversed and
            #    99% of blueprints got dynamic LLM matches, producing
            #    "Gemini 3.5 Flash book" / "Apples and Oranges bluray" /
            #    "AI-designed turbine book" — none of which exist.
            product = match_product(search_text, niche_id, catalog, seasonal_config)

            # 2. Dynamic LLM fallback when static catalog finds nothing —
            # gated on PA-API being configured. Without PA-API, dynamic_match
            # generates plausible-sounding but non-existent product names
            # ("Ronda Rousey vs Gina Carano jersey", "AI-designed turbine
            # book", "Gemini 3.5 Flash book") that ship as affiliate CTAs
            # pointing at Amazon search URLs that may return nothing. Setting
            # PAAPI_ACCESS_KEY + PAAPI_SECRET_KEY auto-re-enables.
            if product is None:
                from genlab_core.monetization.paapi_client import PaapiClient

                if PaapiClient().is_configured:
                    from genlab_core.monetization.dynamic_matcher import dynamic_match
                    from genlab_core.monetization.geo_link_resolver import NICHE_PRIMARY_GEO

                    geo = NICHE_PRIMARY_GEO.get(niche_id, "IN")
                    product = dynamic_match(search_text, niche_id, geo=geo)
                else:
                    logger.debug(
                        "[AffiliateMatch] PA-API not configured — skipping "
                        "dynamic fallback for '%s' (niche=%s)",
                        title[:60],
                        niche_id,
                    )
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
            #
            # 2026-06-19: pivoted from "link in 1st comment" → "link in bio".
            # The 1st-comment CTA was a dead promise — payload_builder.py
            # only populates first_comment_text for facebook/twitter, and
            # instagram.py:publish() has zero post_comment calls. Every IG
            # follower was hitting a CTA pointing to a comment that never
            # got posted. "Link in bio" routes to the dashboard's
            # link-in-bio page (review.aspirehub.ai/links/<slug>) which
            # has been the actual working monetization path since PR #272.
            price = product.get("price_inr", 0)
            if price and price < 1000:
                cta = f"🛒 Get {product_name} for ₹{price} — link in bio 👆"
            elif price and price < 5000:
                cta = f"🔥 {product_name} — only ₹{price}. Link in bio 👆"
            elif price:
                cta = f"⭐ {product_name} (₹{price}) — link in bio 👆"
            else:
                cta = f"🔗 Get {product_name} — link in bio 👆"

            story["affiliate_product"] = product_name
            story["affiliate_url"] = url
            story["affiliate_network"] = network_name
            story["affiliate_commission_pct"] = commission_pct
            story["affiliate_cta"] = cta
            story["affiliate_price_inr"] = int(product.get("price_inr", 0) or 0)
            story["_affiliate_disclosure_map"] = disclosure_map
            # L3 PR 2: canonical slug for click → blueprint JOIN in the
            # attribution query (affiliate_clicks.product_id uses slugs).
            story["product_slug"] = slugify_product_name(product_name)

            matched += 1
            logger.info(
                "[AffiliateMatch] Matched '%s' → %s via %s (%.1f%%)",
                title[:60],
                product_name,
                network_name,
                commission_pct,
            )

        # Subtract the pre-charged DB count so run_stats reflects this run.
        run_matched = max(0, matched - existing_today)
        context.setdefault("run_stats", {})["affiliate"] = {
            "matched": run_matched,
            "skipped": skipped,
            "cap_enforced": cap_enforced,
        }
        logger.info(
            "[AffiliateMatch] %d matched this run, %d skipped, %d cap-enforced "
            "(daily total now %d/%d for %s)",
            run_matched,
            skipped,
            cap_enforced,
            matched,
            max_per_day,
            niche_id,
        )
        return context
