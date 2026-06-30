"""BlackboxBrief source-tool affiliate detection (OPS #2 + #6, 2026-06-30).

Goal
----
When a BlackboxBrief reel is rendered, look at the SOURCE video's
metadata (title + description + tags + channel) and check whether the
creator explicitly named an AI tool the video was *made with*
(Runway, Sora, Midjourney to start). If so, attach the corresponding
affiliate product + append a soft "Made with <Tool> — <link>"
disclosure to every platform's caption.

Why this is separate from the generic AffiliateMatch pipeline stage
-------------------------------------------------------------------
``genlab_core.monetization.affiliate_matcher.AffiliateMatch`` matches
on the *post copy* (hook + caption + title). That answers the
question "what product is this CONTENT about?" — useful for ad-style
products (a Razer headset matched to a gaming highlight reel).

The source-tool detector answers a different question — "what tool
was used to PRODUCE the source clip?" — which is honest, niche-
specific, creator-norm signal that doesn't require the post copy to
mention the tool. A Runway demo reel from a creator who only says
"this is wild" in the post should still credit Runway in the
disclosure; the generic keyword stage misses that because none of
the post copy mentions Runway.

Source-tool products in the catalog carry ``source_tool: true``,
which excludes them from the generic AffiliateMatch enabled-product
pool (see ``affiliate_matcher.match_product``). The two paths never
overlap.

Conservative rules (operator chose)
-----------------------------------
1. ONLY attach when the tool keyword appears in the SOURCE video
   metadata (title + description + tags + channel). Generic AI
   content with no specific tool mention gets nothing here.
2. First match wins — alphabetical-by-name is unstable across YAML
   reloads, so we iterate the catalog in declaration order.
3. ONLY applies to ai_creators (the catalog entries are also scoped
   ``niches: [ai_creators]`` as a defence-in-depth).
4. If the caption already carries an affiliate link (set by the
   upstream generic AffiliateMatch stage), don't double-attach —
   the upstream attachment wins and the source-tool disclosure is
   skipped for this run.

Public API
----------
* ``detect_source_tool(story, catalog_path=None) -> dict | None``
* ``build_disclosure(product) -> str``
* ``apply_source_tool_disclosure(content, story, catalog_path=None)
  -> str | None`` — convenience wrapper that mutates ``content`` and
  returns the attached tool name (or None when nothing was attached).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Catalog path — same constant the generic AffiliateMatch resolves
# (BB doesn't ship its own catalog; the genlab-core shared one is
# the single source of truth).
# Path: affiliate_match.py → bb_strategies/ → BlackboxBrief/ → GenLab/
_CATALOG_PATH = Path(__file__).resolve().parents[2] / "genlab-core" / "config" / "affiliate_catalog.yaml"

_NICHE_ID = "ai_creators"


def _build_search_text(story: dict[str, Any]) -> str:
    """Concatenate every source-video metadata field we have access to.

    Order is intentional — the most explicit fields first so substring
    matching is biased toward strongest signal. We deliberately do
    NOT include the GENERATED post copy (hook / caption) here — that
    would let the LLM's wording trigger a tool attachment instead of
    the source video's actual provenance.
    """
    parts: list[str] = []
    # Primary signal: the source video's own title.
    title = story.get("title") or ""
    parts.append(str(title))
    # Description snippet (from TrendingVideo.to_story → 'summary').
    summary = story.get("summary") or story.get("description_snippet") or ""
    parts.append(str(summary))
    # YouTube uploader tags (often where creators tag the tools used).
    tags = story.get("tags") or []
    if isinstance(tags, list):
        parts.append(" ".join(str(t) for t in tags))
    elif isinstance(tags, str):
        parts.append(tags)
    # Channel name (e.g. "Runway" official channel demos).
    channel = story.get("channel_name") or ""
    parts.append(str(channel))
    return " ".join(p for p in parts if p)


def _matches_keyword(text_lower: str, keyword: str) -> bool:
    """Case-insensitive word-boundary regex match.

    Mirrors ``affiliate_matcher._keyword_hits`` semantics so a
    "runway" keyword doesn't false-positive on a "runaway" post.
    """
    if not keyword:
        return False
    return bool(re.search(r"\b" + re.escape(keyword.lower().strip()) + r"\b", text_lower))


def _load_catalog(catalog_path: Path | None = None) -> dict[str, Any]:
    """Delegate to the canonical loader so we get env-var expansion.

    Returns an empty dict on any error (missing file, bad YAML) —
    every caller must treat None / no-match as the safe default.
    """
    from genlab_core.monetization.catalog_loader import load_catalog

    path = catalog_path or _CATALOG_PATH
    try:
        return load_catalog(path)
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("[BBSourceTool] Could not load catalog %s: %s", path, exc)
        return {}


def _select_url(product: dict[str, Any]) -> str:
    """Pick the first non-empty network URL for *product*.

    Source-tool entries currently use a single ``direct`` network
    (no affiliate program exists for any of the seeded tools at
    time of writing). The loop tolerates any network shape so when
    operator wires real affiliate codes (e.g. ``runway_affiliate``
    via PartnerStack), no code change is required.
    """
    networks = product.get("networks") or {}
    for net_info in networks.values():
        url = (net_info or {}).get("url", "")
        if url:
            return str(url)
    return ""


def detect_source_tool(
    story: dict[str, Any],
    catalog_path: Path | None = None,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return the first matching source-tool product, or None.

    Args:
        story: The story / blueprint dict. Reads ``title``, ``summary``,
            ``tags``, ``channel_name`` to build the source-video
            search text.
        catalog_path: Override for tests. None → use the canonical
            shared catalog.
        catalog: Pre-loaded catalog (for tests that already have it
            in memory). When provided, ``catalog_path`` is ignored.

    Returns:
        The matched product dict (with all fields including
        ``tool_keyword``, ``disclosure_template``, ``networks``) or
        None when no tool keyword matched the source-video text.

    Niche scoping:
        Only products whose ``niches`` list includes ``ai_creators``
        are considered. This is defence-in-depth — the catalog entries
        already live under ``niches.ai_creators.products`` and the
        BB strategy is the only caller, but the explicit check
        prevents accidental cross-niche pollution if the function is
        ever lifted into a shared module.
    """
    if catalog is None:
        catalog = _load_catalog(catalog_path)
    if not catalog:
        return None

    niche_products = (catalog.get("niches") or {}).get(_NICHE_ID, {}).get("products") or []
    if not niche_products:
        return None

    search_text = _build_search_text(story)
    if not search_text:
        return None
    text_lower = search_text.lower()

    for product in niche_products:
        # Only consider source-tool flagged + enabled products.
        if not product.get("source_tool", False):
            continue
        if product.get("enabled", True) is False:
            continue
        # Defence-in-depth niche scope check.
        allowed_niches = product.get("niches") or []
        if allowed_niches and _NICHE_ID not in allowed_niches:
            continue
        keyword = (product.get("tool_keyword") or "").strip()
        if not keyword:
            continue
        if _matches_keyword(text_lower, keyword):
            logger.info(
                "[BBSourceTool] Source-tool match: '%s' (keyword=%r) for story '%s'",
                product.get("name", ""),
                keyword,
                str(story.get("title", ""))[:80],
            )
            return product

    return None


def build_disclosure(product: dict[str, Any]) -> str:
    """Render the ``disclosure_template`` with the product's URL.

    Returns an empty string when the template or URL is missing —
    callers must treat empty as "do not append".
    """
    template = product.get("disclosure_template") or ""
    if not template:
        return ""
    url = _select_url(product)
    if not url:
        return ""
    try:
        return template.format(url=url)
    except (KeyError, IndexError):
        # Malformed template — fail open (no disclosure).
        logger.warning(
            "[BBSourceTool] Disclosure template %r malformed for product %r",
            template,
            product.get("name", ""),
        )
        return ""


def _caption_already_has_affiliate(content: dict[str, Any]) -> bool:
    """Return True when the upstream AffiliateMatch stage already
    set affiliate fields on the story / blueprint OR when any
    platform caption already contains an existing "Made with" line.

    Two-layer check:
        1. ``affiliate_url`` / ``affiliate_product`` on the upstream
           dict (set by ``affiliate_matcher.execute`` on the parent
           story). If set, the generic match already attached
           something — don't double-stack.
        2. Caption substring scan for "Made with " (case-insensitive).
           This catches the second-run case where someone re-runs
           platform adaptation against an already-adapted blueprint.
    """
    # Layer 1: the generic AffiliateMatch sets these on the upstream
    # story dict. content is just the per-platform sub-dict, so we
    # can't read those here directly; the caller must pass the
    # parent story dict separately. For safety we ALSO scan the
    # captions for "Made with " — that catches re-runs and any
    # other path that already added a disclosure.
    for platform_key in ("instagram", "youtube", "x_twitter", "facebook", "tiktok", "threads"):
        block = content.get(platform_key) or {}
        text_fields = [block.get("caption", ""), block.get("tweet", ""), block.get("title", "")]
        for text in text_fields:
            if text and "made with " in text.lower():
                return True
    return False


def _append_to_caption(content: dict[str, Any], disclosure: str) -> int:
    """Append *disclosure* to every platform caption that has one.

    Returns the number of platforms touched. Platforms with empty
    captions are skipped (the disclosure is content-relative and
    only meaningful next to actual copy).

    Per-platform field mapping mirrors ``BasePlatformAdaptationStrategy``:
        instagram.caption, facebook.caption, tiktok.caption,
        threads.caption, x_twitter.tweet.
    YouTube's title is bound by the ≤40-char rule so we skip it —
    YouTube descriptions aren't currently in the adapt dict.
    """
    touched = 0
    for key, field in (
        ("instagram", "caption"),
        ("facebook", "caption"),
        ("tiktok", "caption"),
        ("threads", "caption"),
        ("x_twitter", "tweet"),
    ):
        block = content.get(key) or {}
        existing = block.get(field, "")
        if not existing:
            continue
        # Don't append twice if for some reason this exact disclosure
        # is already present (idempotency for re-runs against the
        # same blueprint dict).
        if disclosure and disclosure in existing:
            continue
        block[field] = f"{existing.rstrip()}\n\n{disclosure}"
        content[key] = block
        touched += 1
    return touched


def apply_source_tool_disclosure(
    content: dict[str, Any],
    story: dict[str, Any],
    catalog_path: Path | None = None,
    catalog: dict[str, Any] | None = None,
) -> str | None:
    """Detect → disclose → tag the blueprint.

    Side effects (when a tool matches AND the blueprint isn't already
    affiliated):
        1. Append disclosure to every non-empty platform caption.
        2. Set ``affiliate_product`` / ``affiliate_url`` /
           ``affiliate_network`` / ``affiliate_source`` on *story*
           so the row written to ``blueprints`` carries the tag for
           downstream analytics.

    Returns:
        The matched tool name (e.g. ``"Runway"``) on success, or
        None when nothing was attached.
    """
    # Pre-flight: respect existing affiliate attachments. The upstream
    # AffiliateMatch stage runs BEFORE platform adaptation so if it
    # found a generic-keyword match, ``story["affiliate_url"]`` is
    # already populated — we defer to it.
    if story.get("affiliate_url") or story.get("affiliate_product"):
        logger.debug(
            "[BBSourceTool] Skipping — story already has affiliate (%s)",
            story.get("affiliate_product", "?"),
        )
        return None

    # Belt-and-suspenders: if any caption already says "Made with " we
    # treat that as a prior pass and don't double-attach.
    if _caption_already_has_affiliate(content):
        logger.debug("[BBSourceTool] Skipping — caption already has 'Made with' line")
        return None

    product = detect_source_tool(story, catalog_path=catalog_path, catalog=catalog)
    if product is None:
        return None

    disclosure = build_disclosure(product)
    if not disclosure:
        return None

    n_touched = _append_to_caption(content, disclosure)
    if n_touched == 0:
        logger.debug("[BBSourceTool] No captions had room for disclosure")
        return None

    name = str(product.get("name", "") or "")
    url = _select_url(product)
    story["affiliate_product"] = name
    story["affiliate_url"] = url
    story["affiliate_network"] = "source_tool"
    story["affiliate_source"] = "source_tool_detection"
    # Signal to ``cta_engine.inject_cta`` that the disclosure is
    # already in the caption — skip the generic bandit-CTA layer so
    # the post doesn't end up with both "Made with Runway —" AND
    # "🔗 Get Runway 👇". Read on the YouTube/Facebook/Twitter
    # branches too so the source-tool path stays the single source
    # of truth for the disclosure on every platform.
    story["_skip_cta_inject"] = True
    # Commission_pct is 0.0 for the seeded entries (no real affiliate
    # programs yet); kept for parity with the generic stage's columns.
    networks = product.get("networks") or {}
    for net_info in networks.values():
        if (net_info or {}).get("url"):
            story["affiliate_commission_pct"] = float(net_info.get("commission_pct", 0.0))
            break

    logger.info(
        "[BBSourceTool] Attached '%s' disclosure across %d platforms (story=%s)",
        name,
        n_touched,
        str(story.get("title", ""))[:80],
    )
    return name


__all__ = [
    "detect_source_tool",
    "build_disclosure",
    "apply_source_tool_disclosure",
]
