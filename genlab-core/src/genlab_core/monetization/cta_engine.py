"""CTA injection engine for affiliate-matched stories.

Modifies blueprint fields dicts to inject platform-specific calls-to-action
and disclosure text for affiliate products.

Usage:
    from genlab_core.monetization.cta_engine import inject_cta
    fields = inject_cta(fields, story)
"""

from __future__ import annotations

import re
from typing import Any


def inject_cta(fields: dict[str, Any], story: dict[str, Any]) -> dict[str, Any]:
    """Inject platform-specific CTAs into blueprint fields.

    Modifies:
    - ``caption``: append ``\\n\\n🔗 {product_name} — link in bio`` before hashtags,
      then append Instagram disclosure.
    - ``youtube_content``: prepend ``🔗 {product_name}: {url}\\n\\n``,
      then append YouTube disclosure.
    - ``facebook_content``: append ``\\n\\n🔗 Get {product_name}: {url}``,
      then append Facebook disclosure.
    - ``twitter_content``: left unchanged (link goes in reply thread).

    Returns the modified fields dict (same object, mutated in-place for convenience,
    but also returned for composable usage).
    """
    product_name: str = story.get("affiliate_product", "")
    url: str = story.get("affiliate_url", "")

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

    # ── Instagram caption ──────────────────────────────────────────────────────
    caption: str = fields.get("caption", "") or ""
    if caption:
        hashtag_match = re.search(r"((?:\s*#\w+)+\s*)$", caption)
        cta_snippet = f"\n\n🔗 {product_name} — link in bio"
        if hashtag_match:
            insert_pos = hashtag_match.start()
            caption = caption[:insert_pos] + cta_snippet + caption[insert_pos:]
        else:
            caption = caption + cta_snippet
        if ig_disclosure:
            caption = caption.rstrip() + f"\n{ig_disclosure}"
        fields["caption"] = caption

    # ── YouTube content ────────────────────────────────────────────────────────
    yt_content: str = fields.get("youtube_content", "") or ""
    if yt_content or url:
        prefix = f"🔗 {product_name}: {url}\n\n"
        yt_content = prefix + yt_content
        if yt_disclosure:
            yt_content = yt_content.rstrip() + f"\n\n{yt_disclosure}"
        fields["youtube_content"] = yt_content

    # ── Facebook content ───────────────────────────────────────────────────────
    fb_content: str = fields.get("facebook_content", "") or ""
    if fb_content or url:
        fb_content = fb_content.rstrip() + f"\n\n🔗 Get {product_name}: {url}"
        if fb_disclosure:
            fb_content = fb_content.rstrip() + f"\n{fb_disclosure}"
        fields["facebook_content"] = fb_content

    # Twitter: no modification — link goes in reply thread
    return fields
