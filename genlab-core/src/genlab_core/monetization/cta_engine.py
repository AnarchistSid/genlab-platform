"""CTA injection engine for affiliate-matched stories.

Modifies blueprint fields dicts to inject platform-specific calls-to-action
and disclosure text for affiliate products.

The engine uses a Thompson Sampling bandit (CTABandit) to select the
best-performing CTA variant for each platform, falling back to hardcoded
defaults if the bandit is unavailable.

Usage:
    from genlab_core.monetization.cta_engine import inject_cta
    fields = inject_cta(fields, story)
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlparse

logger = logging.getLogger(__name__)


def is_cta_injection_enabled(niche_id: str) -> bool:
    """Return True iff the niche's monetization.yaml explicitly opts in.

    Reads ``cta_injection_enabled`` (bool) from the niche's
    ``monetization.yaml``. Default False when the file, key, or niche is
    missing — CTA injection is off unless the operator explicitly enables it.

    Path resolution mirrors ``publish_all_platforms.py`` (gaming's nested
    layout first, then flat) so the helper works uniformly across the 5
    channels. The check is fail-safe: any I/O or YAML error returns False.
    """
    try:
        import yaml as _yaml

        from genlab_core.pipeline.cli import NICHE_DIR_NAMES, _resolve_genlab_root

        dir_name = NICHE_DIR_NAMES.get(niche_id)
        if not dir_name:
            return False
        niche_root = Path(_resolve_genlab_root()) / dir_name
        candidates = [
            niche_root / "niches" / niche_id / "config" / "monetization.yaml",
            niche_root / "config" / "monetization.yaml",
        ]
        for path in candidates:
            if path.is_file():
                with path.open("r", encoding="utf-8") as f:
                    data = _yaml.safe_load(f) or {}
                return bool(data.get("cta_injection_enabled", False))
        return False
    except Exception as exc:
        logger.debug(
            "[cta_injection] flag lookup failed for niche=%s (%s) — defaulting off",
            niche_id,
            exc,
        )
        return False


def append_utm_params(
    url: str,
    niche_id: str = "",
    blueprint_id: str = "",
    utm_source: str = "genlab",
    utm_medium: str = "affiliate",
) -> str:
    """Append UTM tracking parameters to an affiliate URL.

    Pattern: &utm_source=genlab&utm_medium=affiliate&utm_campaign={niche_id}&utm_content={blueprint_id}

    Preserves existing query parameters. Skips if URL already has utm_source.
    """
    if not url:
        return url
    parsed = urlparse(url)
    existing = parse_qs(parsed.query, keep_blank_values=True)

    # Don't double-add UTM params
    if "utm_source" in existing:
        return url

    utm = {
        "utm_source": utm_source,
        "utm_medium": utm_medium,
    }
    if niche_id:
        utm["utm_campaign"] = niche_id
    if blueprint_id:
        utm["utm_content"] = blueprint_id

    separator = "&" if parsed.query else "?"
    utm_str = urlencode(utm)
    return f"{url}{separator}{utm_str}"


# ── Platform caption length limits ─────────────────────────────────────────────
_PLATFORM_LIMITS: dict[str, int] = {
    "instagram": 2200,
    "youtube": 5000,
    "facebook": 63206,
}

# ── Module-level bandit singleton ──────────────────────────────────────────────
_bandit = None


def _get_bandit():
    """Lazy-initialize a module-level CTABandit singleton."""
    global _bandit
    if _bandit is not None:
        return _bandit
    try:
        from genlab_core.monetization.cta_bandit import CTABandit

        _bandit = CTABandit()
        return _bandit
    except Exception as e:
        logger.warning("[CTAEngine] Failed to initialize CTABandit: %s", e)
        return None


def get_bandit():
    """Public accessor for the module-level CTABandit singleton.

    Used by the click tracker to update bandit state from reward signals.
    """
    return _get_bandit()


def _enforce_length(content: str, platform: str, cta_len: int, disclosure_len: int) -> str:
    """Truncate the original caption (not CTA/disclosure) if over platform limit.

    Args:
        content: The full content string (original + CTA + disclosure).
        platform: Platform name for limit lookup.
        cta_len: Length of the CTA + disclosure portion that must be preserved.
        disclosure_len: Length of disclosure portion (subset of cta_len, for logging).

    Returns:
        The content, truncated if necessary.
    """
    limit = _PLATFORM_LIMITS.get(platform, 0)
    if limit <= 0 or len(content) <= limit:
        return content
    # Calculate how much original text we can keep
    overage = len(content) - limit
    # Find where the original content ends (before CTA was appended)
    original_len = len(content) - cta_len
    if original_len <= 0:
        return content  # CTA alone exceeds limit, nothing to truncate
    new_original_len = max(0, original_len - overage)
    # Rebuild: truncated original + preserved CTA/disclosure tail
    truncated_original = content[:new_original_len]
    cta_tail = content[original_len:]
    logger.debug(
        "[CTAEngine] Truncated %s caption from %d to %d chars (limit=%d)",
        platform,
        len(content),
        len(truncated_original) + len(cta_tail),
        limit,
    )
    return truncated_original + cta_tail


def _product_slug(name: str) -> str:
    """Slugify a product name to match the affiliate catalog's slug.

    2026-07-14 (monetization audit F1): delegate to the canonical
    :func:`slugify_product_name` in ``affiliate_matcher`` so both
    sides of the click→blueprint JOIN produce the same slug. Prior
    state was a simpler `.lower().replace(" ", "-")` — diverged on
    double-spaces, underscores, and leading/trailing hyphens. A name
    like ``"Xbox Game Pass  Ultimate"`` (double-space) or
    ``"Prime_Video"`` produced different slugs on the two sides →
    silently 0-attributed every click for those products.

    Same class-of-bug as writer-wire attribution + IG cross-channel
    leak: same invariant, two paths, divergent conventions.
    """
    from genlab_core.monetization.affiliate_matcher import slugify_product_name

    return slugify_product_name(name)


_TRACKING_DOMAIN_WARNING_EMITTED = False


# 2026-08-12 (F-QB-0701): FTC/ASCI disclosure MUST appear before the
# platform "more" fold on IG/FB/Threads. Prior behaviour appended
# `#ad` to the CTA block at the caption tail (after hashtags +
# hook + attribution + product line) — well past the ~100 char fold.
# QB-2026-08 verified 17/17 movies affiliate posts non-compliant.
_DISCLOSURE_HEAD_CHAR_LIMIT: int = 100

# Case-insensitive substrings that count as valid FTC disclosure per
# the pre-publish compliance gate. Kept as a module constant so future
# additions (new platform requirements) are in one place.
#
# "made with " covers the BlackboxBrief source-tool disclosure path
# (bb_strategies.affiliate_match.apply_source_tool_disclosure) which
# prepends "Made with <Tool> — <link>" as its affiliate marker.
# Treating it as a valid marker keeps us from stacking a second #ad
# on top of BB's already-present disclosure.
_DISCLOSURE_MARKERS: tuple[str, ...] = (
    "#ad",
    "#affiliate",
    "#sponsored",
    "sponsored",
    "paid partnership",
    "paid promotion",
    "made with ",
)


def _has_disclosure_in_head(text: str, char_limit: int = _DISCLOSURE_HEAD_CHAR_LIMIT) -> bool:
    """Does ``text[:char_limit]`` contain any recognised disclosure marker?"""
    if not text:
        return False
    head = text[:char_limit].lower()
    return any(marker in head for marker in _DISCLOSURE_MARKERS)


def _ensure_top_disclosure(text: str, prepend: str = "#ad") -> str:
    """Prepend ``prepend`` to ``text`` iff no disclosure marker is
    already present in the first ``_DISCLOSURE_HEAD_CHAR_LIMIT`` chars.

    Idempotent: safe to call multiple times. Returns unchanged text
    when already compliant so a caption written with an explicit
    ``#ad`` prefix by the LLM stays untouched.

    FTC 16 CFR 255 / ASCI guideline: sponsored content must disclose
    "clearly and conspicuously" — burying #ad past a "more" fold on
    a 100-char preview does not satisfy that standard.
    """
    if not text:
        return text
    if _has_disclosure_in_head(text):
        return text
    return f"{prepend} {text}"


def _tracked_url(raw_url: str, product_name: str, *, niche_id: str, attribution_id: str) -> str:
    """Route the published affiliate link through the /links/go redirect so
    clicks are tracked (R-23).

    The dashboard redirect (``/links/go/<slug>``) resolves the slug against the
    affiliate catalog, logs the click to ``affiliate_clicks`` keyed on the
    ``?bp`` param (R-23/CD-5), and 302s to the real URL — gracefully falling
    back to the channel page if the slug is unknown, so a published link never
    breaks.

    Two operating modes for the ``GENLAB_DOMAIN`` env var (2026-06-19):

    * **Strict (production)** — ``GENLAB_REQUIRE_TRACKING_DOMAIN=1``: raises
      ``RuntimeError`` if ``GENLAB_DOMAIN`` is unset. This catches the silent
      monetization-bypass bug (100% of YT/FB/Threads CTAs lost attribution
      while the env var was empty for an unknown duration). Mirrors the
      SR-A/C/D ``GENLAB_REQUIRE_TENANT_GUC`` two-mode pattern.
    * **Soft (CI / local dev)** — strict-mode flag unset: emits a one-time
      ``WARNING`` log line on first hit, then falls back to the raw UTM URL.
      Tests don't need to set ``GENLAB_DOMAIN`` to avoid breakage.
    """
    global _TRACKING_DOMAIN_WARNING_EMITTED

    domain = os.environ.get("GENLAB_DOMAIN", "").strip().rstrip("/")
    slug = _product_slug(product_name)

    if not domain:
        strict = os.environ.get("GENLAB_REQUIRE_TRACKING_DOMAIN", "").strip() == "1"
        if strict:
            raise RuntimeError(
                "GENLAB_DOMAIN is required for affiliate click attribution "
                "(GENLAB_REQUIRE_TRACKING_DOMAIN=1 is set). Set it to your "
                "public dashboard URL (e.g. https://review.example.com). "
                "Without it, /links/go redirects don't work and per-post "
                "attribution is silently lost. To intentionally bypass "
                "tracking, unset GENLAB_REQUIRE_TRACKING_DOMAIN."
            )
        if not _TRACKING_DOMAIN_WARNING_EMITTED:
            logger.warning(
                "GENLAB_DOMAIN is unset — affiliate clicks will bypass "
                "/links/go and per-post attribution will be silently lost. "
                "Set GENLAB_DOMAIN to your public dashboard URL. "
                "Set GENLAB_REQUIRE_TRACKING_DOMAIN=1 to make this a hard error."
            )
            _TRACKING_DOMAIN_WARNING_EMITTED = True
        return append_utm_params(raw_url, niche_id=niche_id, blueprint_id=attribution_id)

    if not slug:
        # Legitimate fallback — not every product has a slug
        return append_utm_params(raw_url, niche_id=niche_id, blueprint_id=attribution_id)

    if not domain.startswith(("http://", "https://")):
        domain = f"https://{domain}"
    base = f"{domain}/links/go/{slug}"
    return f"{base}?bp={quote(attribution_id)}" if attribution_id else base


def inject_cta(fields: dict[str, Any], story: dict[str, Any]) -> dict[str, Any]:
    """Inject platform-specific CTAs into blueprint fields.

    Uses the CTABandit to select the best CTA variant via Thompson Sampling.
    Falls back to hardcoded CTA formats if the bandit is unavailable.

    Modifies:
    - ``caption``: append CTA variant before hashtags, then append Instagram disclosure.
    - ``youtube_content``: prepend CTA variant with URL, then append YouTube disclosure.
    - ``facebook_content``: append CTA variant with URL, then append Facebook disclosure.
    - ``twitter_content``: left unchanged (link goes in reply thread).

    Stores the selected variant arm_id as ``affiliate_cta_variant`` on the fields dict
    for downstream attribution.

    Returns the modified fields dict (same object, mutated in-place for convenience,
    but also returned for composable usage).
    """
    product_name: str = story.get("affiliate_product", "")
    raw_url: str = story.get("affiliate_url", "")
    product_price: int = int(story.get("affiliate_price_inr", 0) or 0)

    def _build_cta_text(price: int, name: str) -> str:
        """Build a price-aware, action-oriented CTA. Falls back to generic if no price."""
        if price and price < 1000:
            return f"🛒 Get {name} for ₹{price} 👇"
        if price and price < 5000:
            return f"🔥 {name} — ₹{price} 👇"
        if price:
            return f"⭐ {name} (₹{price}) 👇"
        return f"🔗 Get {name} 👇"

    # R-23/CD-5: route the published link through the /links/go redirect so
    # clicks are tracked. Attribute to candidate_id since the blueprint row
    # doesn't exist yet at inject time (blueprint_id is empty here), which is
    # why utm_content was previously always blank. The redirect logs the click
    # + 302s; falls back to the raw UTM URL when no public domain is configured.
    niche_id: str = story.get("niche_id", "") or fields.get("niche_id", "")
    attribution_id: str = (
        story.get("blueprint_id")
        or fields.get("blueprint_id")
        or fields.get("candidate_id")
        or story.get("candidate_id")
        or ""
    )
    url = _tracked_url(raw_url, product_name, niche_id=niche_id, attribution_id=attribution_id)

    # Disclosure texts — pulled from the story if the AffiliateMatch stage stored them,
    # otherwise fall back to empty strings.
    disclosure_map: dict[str, str] = story.get("_affiliate_disclosure_map") or {}
    ig_disclosure: str = disclosure_map.get("instagram", "#affiliate")
    yt_disclosure: str = disclosure_map.get(
        "youtube",
        "This description contains affiliate links — we may earn a small commission at no extra cost to you.",
    )
    fb_disclosure: str = disclosure_map.get("facebook", "#affiliate")

    if not product_name:
        return fields

    # 2026-06-30 (OPS #2 + #6): the BlackboxBrief source-tool path
    # appends the "Made with <Tool> — <link>" disclosure DIRECTLY to
    # every platform caption before push_to_backlog runs. When that
    # path fires it sets ``story["_skip_cta_inject"] = True`` so the
    # generic bandit-CTA layer below short-circuits — without this
    # gate the post would carry both the source-tool disclosure and
    # a generic "🔗 Get Runway 👇" CTA across IG / FB first-comment /
    # YouTube description, which is the dropshipper-stack shape the
    # rest of this function works hard to avoid (already_disclosed +
    # link-in-bio + made-with checks).
    if story.get("_skip_cta_inject"):
        return fields

    bandit = _get_bandit()

    # Track selected variant arm_ids per platform for attribution
    selected_variants: list[str] = []

    # ── Instagram caption ──────────────────────────────────────────────────────
    caption: str = fields.get("caption", "") or ""
    if caption:
        # Check for existing affiliate CTA to avoid duplication.
        # 2026-06-30 (OPS #2 + #6): "made with " covers the
        # BlackboxBrief source-tool disclosure path
        # (bb_strategies.affiliate_match.apply_source_tool_disclosure)
        # which appends "Made with <Tool> — <link>" directly to the
        # caption BEFORE this stage runs. Without this token in the
        # skip-check, both the source-tool disclosure AND a generic
        # bandit CTA would land in the same caption — exactly the
        # double-CTA dropshipper shape the rest of this function
        # works hard to avoid.
        caption_lower = caption.lower()
        if (
            "link in bio" in caption_lower
            or "1st comment" in caption_lower
            or "made with " in caption_lower
        ):
            pass  # CTA already present, skip injection
        else:
            hashtag_match = re.search(r"((?:\s*#\w+)+\s*)$", caption)

            # 2026-06-19: pivoted from "(1st comment)" → "(link in bio)".
            # The 1st-comment promise was never delivered — payload_builder
            # only populates first_comment_text for facebook/twitter and
            # instagram.py never calls post_comment. The bio link is the
            # actual working monetization surface (review.aspirehub.ai/
            # links/<slug>) post-PR #272.
            ig_cta_text = _build_cta_text(product_price, product_name) + " (link in bio)"
            if bandit:
                try:
                    variant = bandit.select(platform="instagram")
                    # Bandit variants are templates; use them but inject price if set
                    rendered = variant.format(product_name=product_name)
                    if product_price and "{price}" in variant:
                        rendered = variant.format(product_name=product_name, price=product_price)
                    ig_cta_text = rendered
                    selected_variants.append(variant.arm_id)
                except Exception as e:
                    logger.debug("[CTAEngine] Bandit select failed for instagram: %s", e)

            # Place disclosure BEFORE the CTA for FTC/ASCI compliance.
            # Skip if the LLM-written caption already carries a disclosure
            # marker (#ad / #affiliate / "sponsored") — stacking two
            # disclosures is what made captions look like dropshipper ads.
            cta_parts = []
            caption_lower_full = caption.lower()
            already_disclosed = any(
                marker in caption_lower_full
                for marker in ("#ad", "#affiliate", "#sponsored", "sponsored")
            )
            if ig_disclosure and not already_disclosed:
                cta_parts.append(ig_disclosure)
            cta_parts.append(ig_cta_text)
            cta_snippet = "\n\n" + "\n".join(cta_parts)
            if hashtag_match:
                insert_pos = hashtag_match.start()
                caption = caption[:insert_pos] + cta_snippet + caption[insert_pos:]
            else:
                caption = caption + cta_snippet
            # 2026-08-12 (F-QB-0701): FTC/ASCI disclosure must land in
            # first 100 chars, before the IG "more" fold. Idempotent
            # — no double-#ad when the LLM already opens with a marker.
            # Runs BEFORE _enforce_length so length is calculated on
            # the final shape.
            caption = _ensure_top_disclosure(caption)
            # Enforce Instagram caption length limit
            caption = _enforce_length(caption, "instagram", len(cta_snippet), len(ig_disclosure))
        fields["caption"] = caption

    # ── YouTube content ────────────────────────────────────────────────────────
    yt_content: str = fields.get("youtube_content", "") or ""
    if url and (yt_content or product_name):
        # Select CTA variant via bandit, fallback to hardcoded format
        yt_cta_text = f"🔗 {product_name}: {url}"
        if bandit:
            try:
                variant = bandit.select(platform="youtube")
                yt_cta_text = variant.format(product_name=product_name, url=url)
                selected_variants.append(variant.arm_id)
            except Exception as e:
                logger.debug("[CTAEngine] Bandit select failed for youtube: %s", e)

        prefix = f"{yt_cta_text}\n\n"
        yt_content = prefix + yt_content
        if yt_disclosure:
            yt_content = yt_content.rstrip() + f"\n\n{yt_disclosure}"
        # Enforce YouTube description length limit
        cta_added_len = len(prefix) + len(f"\n\n{yt_disclosure}") if yt_disclosure else len(prefix)
        yt_content = _enforce_length(yt_content, "youtube", cta_added_len, len(yt_disclosure))
        fields["youtube_content"] = yt_content

    # ── Facebook content ───────────────────────────────────────────────────────
    # Facebook downranks posts with external URLs in the main caption. Keep
    # the main caption clean (body + disclosure only) and emit the affiliate
    # CTA as facebook_first_comment so the FB publisher posts it as a
    # comment after the main post lands.
    fb_content: str = fields.get("facebook_content", "") or ""
    if url and (fb_content or product_name):
        # Select CTA variant via bandit, fallback to hardcoded format
        fb_cta_text = f"🔗 Get {product_name}: {url}"
        if bandit:
            try:
                variant = bandit.select(platform="facebook")
                fb_cta_text = variant.format(product_name=product_name, url=url)
                selected_variants.append(variant.arm_id)
            except Exception as e:
                logger.debug("[CTAEngine] Bandit select failed for facebook: %s", e)

        # Main caption: body + disclosure only (no URL)
        if fb_disclosure:
            fb_disclosure_snippet = f"\n\n{fb_disclosure}"
            fb_content = fb_content.rstrip() + fb_disclosure_snippet
            fb_content = _enforce_length(
                fb_content,
                "facebook",
                len(fb_disclosure_snippet),
                len(fb_disclosure_snippet),
            )
        # 2026-08-12 (F-QB-0701): FTC/ASCI disclosure at head. Runs after
        # length enforcement (FB doesn't have a tight cap that would
        # clash with a 4-char prepend). Idempotent — noop when the LLM
        # or fb_disclosure block above already opens with a marker.
        fb_content = _ensure_top_disclosure(fb_content)
        fields["facebook_content"] = fb_content

        # First-comment: the affiliate CTA itself, posted after main post
        fields["facebook_first_comment"] = fb_cta_text

    # ── Instagram first-comment (2026-07-17 Layer 2 monetization) ─────────────
    # IG bio-link CTR is 0.1-0.3%; pinned first-comment CTR is 2-8% =
    # 20-80× improvement with ZERO extra traffic. IG allows external
    # URLs in comments (unlike caption where external URLs downrank).
    # Reads by payload_builder → InstagramClient.publish() posts as
    # first comment via post_reply().
    if url and product_name:
        ig_cta_text = f"🔗 Get {product_name}: {url}"
        if bandit:
            try:
                variant = bandit.select(platform="instagram")
                if variant.arm_id != "default":
                    ig_cta_text = variant.format(product_name=product_name, url=url)
                    selected_variants.append(variant.arm_id)
            except Exception as e:
                logger.debug("[CTAEngine] Bandit select failed for instagram: %s", e)
        fields["instagram_first_comment"] = ig_cta_text

    # ── YouTube first-comment (2026-07-17 Layer 2 monetization) ───────────────
    # YT comments support clickable URLs and are prominently displayed
    # under Shorts. Same 20-80× CTR improvement over bio-link routing.
    # Posted via YouTubeClient.post_reply (comments.insert).
    if url and product_name:
        yt_cta_text = f"🔗 Get {product_name}: {url}"
        if bandit:
            try:
                variant = bandit.select(platform="youtube")
                if variant.arm_id != "default":
                    yt_cta_text = variant.format(product_name=product_name, url=url)
                    selected_variants.append(variant.arm_id)
            except Exception as e:
                logger.debug("[CTAEngine] Bandit select failed for youtube: %s", e)
        fields["youtube_first_comment"] = yt_cta_text

    # ── Twitter / X first-reply ───────────────────────────────────────────────
    # X links in the main tweet body get downranked; standard creator
    # practice is to drop the affiliate URL as a self-reply. Main
    # tweet_text stays clean (LLM-written).
    if url and product_name:
        tw_disclosure = disclosure_map.get("twitter", "#ad")
        tw_cta_text = f"🔗 {product_name}: {url}"
        if bandit:
            try:
                variant = bandit.select(platform="twitter")
                if variant.arm_id != "default":
                    tw_cta_text = variant.format(product_name=product_name, url=url)
                    selected_variants.append(variant.arm_id)
            except Exception as e:
                logger.debug("[CTAEngine] Bandit select failed for twitter: %s", e)
        # Build reply: disclosure on its own line, then CTA. Cap at 280.
        reply_text = f"{tw_disclosure}\n{tw_cta_text}" if tw_disclosure else tw_cta_text
        if len(reply_text) > 280:
            reply_text = reply_text[:277].rstrip() + "..."
        fields["twitter_first_comment"] = reply_text

    # ── Threads content ─────────────────────────────────────────────────────
    # Threads doesn't support clickable links in the main post body, but
    # replies DO carry URLs. Same first-comment pattern as FB/IG/YT: main
    # post mentions the product with a "check first reply" hint, then a
    # separate reply carries the actual affiliate URL (posted by
    # `ThreadsClient.post_reply` after successful publish — wired
    # 2026-07-22 via `payload_builder.py:329-330`).
    th_content: str = fields.get("threads_content", "") or ""
    if product_name and th_content:
        if product_name.lower() not in th_content.lower():
            th_disclosure = disclosure_map.get("threads", "#ad #affiliate")
            th_cta_text = f"{product_name} — check first reply"
            if bandit:
                try:
                    variant = bandit.select(platform="threads")
                    if variant.arm_id != "default":
                        th_cta_text = variant.format(product_name=product_name)
                        selected_variants.append(variant.arm_id)
                except Exception as e:
                    logger.debug("[CTAEngine] Bandit select failed for threads: %s", e)
            th_cta = f"\n\n{th_disclosure}\n{th_cta_text}"
            th_content = th_content.rstrip() + th_cta
            # 2026-08-12 (F-QB-0701): FTC/ASCI disclosure at head, BEFORE
            # length enforcement so the 500-char Threads cap accounts for it.
            th_content = _ensure_top_disclosure(th_content)
            if len(th_content) > 500:
                # Threads has 500 char limit — truncate middle, keep CTA tail.
                overage = len(th_content) - 500
                th_content = th_content[: len(th_content) - len(th_cta) - overage] + th_cta
            fields["threads_content"] = th_content

    # ── Threads first-comment (2026-07-22 Layer 2 monetization) ───────────
    # Wire gap: previously, `threads_content` got the "check first reply"
    # hint but no `threads_first_comment` was ever set — the reply
    # never got posted (payload_builder had no elif, threads.py had no
    # post_reply call after publish). Same class-of-bug as tonights
    # Threads-dispatch fixes (f9f186c2, 2898cc1e — insights fetcher).
    #
    # Follow the FB/IG shape verbatim: bare URL + product name, no
    # disclosure duplicated (already in the main post). Bandit-selected
    # variant hookup mirrors YT.
    if url and product_name:
        th_first_comment = f"🔗 Get {product_name}: {url}"
        if bandit:
            try:
                variant = bandit.select(platform="threads_reply")
                if variant.arm_id != "default":
                    th_first_comment = variant.format(product_name=product_name, url=url)
                    selected_variants.append(variant.arm_id)
            except Exception as e:
                logger.debug("[CTAEngine] Bandit select failed for threads_reply: %s", e)
        # Threads reply cap: 500 chars (same as parent post)
        if len(th_first_comment) > 500:
            th_first_comment = th_first_comment[:497].rstrip() + "..."
        fields["threads_first_comment"] = th_first_comment

    # Store the selected variant arm_id for downstream attribution
    if selected_variants:
        fields["affiliate_cta_variant"] = ",".join(selected_variants)

    return fields
