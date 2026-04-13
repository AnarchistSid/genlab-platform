"""Dynamic affiliate product matching via LLM-extracted entities.

Instead of matching to a static product catalog, this module uses Claude
to extract the main subject from content (e.g., "Re:Zero", "Apex Legends",
"Mortal Kombat 2") and generates a contextually relevant Amazon Associates
search URL that will earn commission on any purchase the visitor makes.

Why this works:
- Amazon Associates uses a 24-hour cookie window
- ANY purchase made after clicking our link credits our tag
- A search URL ("re:zero manga") sends the user into Amazon's catalog where
  they're likely to buy the specific item they want
- No catalog maintenance required — works for any topic
- Always relevant to the actual content
"""
from __future__ import annotations

import logging
import os
import re
import urllib.parse
from typing import Any

logger = logging.getLogger(__name__)


# Per-niche merchandise types to append to the search query for better targeting
_MERCH_TYPES_BY_NICHE: dict[str, list[str]] = {
    "anime": ["manga", "merchandise", "figure", "poster", "art book"],
    "gaming": ["game", "merchandise", "controller", "accessories", "guide book"],
    "movies": ["bluray", "soundtrack", "poster", "art book", "merchandise"],
    "sports": ["jersey", "merchandise", "training gear", "book", "accessories"],
    "ai_creators": ["book", "course", "guide", "ebook", "training"],
}

# Amazon search URL templates per geo
_AMAZON_SEARCH_TEMPLATES = {
    "IN": "https://www.amazon.in/s?k={query}&tag={tag}",
    "US": "https://www.amazon.com/s?k={query}&tag={tag}",
}


def _get_amazon_tag(geo: str) -> str:
    """Get the Amazon Associates tag for a geo."""
    if geo == "IN":
        return os.environ.get("AMAZON_IN_AFFILIATE_TAG", "aspirehub-21")
    return os.environ.get("AMAZON_US_AFFILIATE_TAG", "aspirehub06-20")


def extract_subject(text: str, niche_id: str) -> str | None:
    """Use Claude to extract the main subject from content.

    Returns the most product-searchable phrase (e.g., game name, anime title,
    movie name, athlete, AI tool). Returns None if no clear subject found.

    Cost: ~150 input + ~15 output tokens per call ≈ $0.00004
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None

    # Truncate content
    content_truncated = text[:400].strip()
    if not content_truncated or len(content_truncated) < 10:
        return None

    niche_label = {
        "anime": "anime/manga series",
        "gaming": "video game",
        "movies": "movie/film/show",
        "sports": "sport/team/athlete",
        "ai_creators": "AI tool/model/topic",
    }.get(niche_id, "subject")

    prompt = (
        f"Extract the main {niche_label} from this content. Return ONLY the name, "
        f"nothing else. If no clear subject, return 'none'.\n\n"
        f"Content: {content_truncated}\n\n"
        f"Subject:"
    )

    try:
        from genlab_core.writing.llm_client import AnthropicLLMClient

        client = AnthropicLLMClient(api_key=api_key, model="claude-haiku-4-5-20251001")
        answer = client.complete(
            system="You extract product-searchable subjects from content. Reply with ONLY the subject name (3-5 words max), or 'none'.",
            user=prompt,
            max_tokens=20,
            temperature=0.0,
        ).strip()

        # Clean up the response
        answer = answer.strip("\"'.,;:!? \n\t")
        # Remove any prefixes the LLM might add
        answer = re.sub(r"^(subject|name|the|a|an):\s*", "", answer, flags=re.IGNORECASE)

        if not answer or answer.lower() == "none" or len(answer) < 2:
            return None
        if len(answer) > 60:
            # Too long, likely an error
            return None

        return answer

    except Exception as e:
        logger.debug("[DynamicMatcher] Subject extraction failed: %s", e)
        return None


def build_search_url(subject: str, niche_id: str, geo: str = "IN") -> str:
    """Build an Amazon search URL with our affiliate tag.

    The search query combines the extracted subject with niche-appropriate
    merchandise types to surface buyable products.
    """
    if not subject:
        return ""

    # Pick the first merch type for the niche (most general)
    merch_types = _MERCH_TYPES_BY_NICHE.get(niche_id, ["merchandise"])
    merch = merch_types[0]

    # Build search query
    query = f"{subject} {merch}"
    encoded = urllib.parse.quote_plus(query)

    template = _AMAZON_SEARCH_TEMPLATES.get(geo, _AMAZON_SEARCH_TEMPLATES["IN"])
    tag = _get_amazon_tag(geo)
    return template.format(query=encoded, tag=tag)


def dynamic_match(text: str, niche_id: str, geo: str = "IN") -> dict[str, Any] | None:
    """End-to-end dynamic match: extract subject → build Amazon search URL.

    Returns a product-like dict compatible with the static catalog format,
    or None if subject extraction fails.
    """
    subject = extract_subject(text, niche_id)
    if not subject:
        return None

    url = build_search_url(subject, niche_id, geo)
    if not url:
        return None

    merch = _MERCH_TYPES_BY_NICHE.get(niche_id, ["merchandise"])[0]
    product_name = f"{subject} {merch}"

    logger.info(
        "[DynamicMatcher] %s: extracted subject '%s' -> %s",
        niche_id, subject, product_name,
    )

    # Return in the same format as static catalog products
    return {
        "name": product_name,
        "subject": subject,
        "price_inr": 0,  # Variable — Amazon search results
        "is_dynamic": True,
        "networks": {
            "amazon" if geo == "IN" else "amazon_us": {
                "url": url,
                "commission_pct": 4.0,  # Amazon Associates average
            }
        },
    }
