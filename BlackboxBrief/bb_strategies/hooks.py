"""BB hook strategy — AI creator hook generation.

Migrated to BaseHookStrategy (Sprint 69). Uses the shared LLM-first +
template-fallback hook pipeline with AI-specific category classification
and placeholder substitution.

Previous version (82 lines) used a standalone _hooks.py formula engine
with engagement scoring. That engine is preserved at _hooks.py but no
longer called from the pipeline.
"""

from __future__ import annotations

import re
from pathlib import Path

from genlab_core.strategies import BaseHookStrategy

BB_ROOT = Path(__file__).resolve().parent.parent

# Keywords for classifying AI stories into hook categories
_TOOL_KEYWORDS = {"launch", "release", "drop", "update", "announce", "available", "beta", "alpha", "new version"}
_CREATOR_KEYWORDS = {"made", "created", "built", "generated", "pushed", "used", "demo", "recreated", "showed"}
_MILESTONE_KEYWORDS = {"million", "billion", "users", "downloads", "record", "fastest", "hit", "reached", "scored"}
_CONTROVERSY_KEYWORDS = {
    "debate",
    "controversy",
    "vs",
    "versus",
    "fight",
    "war",
    "lawsuit",
    "ban",
    "problem",
    "dangerous",
}
_CURIOSITY_KEYWORDS = {"secret", "hidden", "quietly", "nobody knows", "doesn't want", "behind the scenes"}
_VIRAL_KEYWORDS = {"viral", "views", "trending", "broke", "insane", "wild", "unreal", "out-performed"}


class BBHookStrategy(BaseHookStrategy):
    """Generate hooks for AI creator content.

    Categories: showcase, tool_launch, creator_showcase, controversy,
    demo_viral, default.

    Phase 2 C1 (2026-06-30): added ``showcase`` category that fires
    when the SOURCE declared content_type=showcase (independent of
    title keywords). Visual-first templates ("Watch this Xs Y output",
    "Spot the difference: real vs AI", etc.) so the operator's
    sources.yaml curation flows through to the hook style. Previously
    showcase content fell through to text-keyword classifiers and got
    news-style hooks ("OpenAI released X") that don't match the visual.
    """

    _title_fallback_label = "AI moment"

    def __init__(self) -> None:
        super().__init__(niche_id="ai_creators", niche_root=BB_ROOT)

    def _classify_story(self, story: dict) -> str:
        """Classify AI story into a hook category.

        Phase 2 C1: content_type takes precedence over text-keyword
        classification. When the source declares content_type=showcase
        (via sources.yaml), we trust that more than guessing from the
        title — the source operator KNOWS the content type, the
        title-keyword heuristic only GUESSES.
        """
        # Phase 2 C1: source-declared content_type wins. Looks in the
        # same three places as build_content_context (top-level,
        # gallery_metadata, source_config) so wherever the fetcher
        # stamped it gets picked up.
        content_type = _extract_content_type(story)
        if content_type == "showcase":
            return "showcase"

        title = (story.get("title") or "").lower()
        summary = (story.get("summary") or "").lower()
        text = f"{title} {summary}"

        if any(kw in text for kw in _TOOL_KEYWORDS):
            return "tool_launch"
        if any(kw in text for kw in _CREATOR_KEYWORDS):
            return "creator_showcase"
        if any(kw in text for kw in _MILESTONE_KEYWORDS):
            return "milestone"
        if any(kw in text for kw in _CONTROVERSY_KEYWORDS):
            return "controversy"
        if any(kw in text for kw in _CURIOSITY_KEYWORDS):
            return "curiosity"
        if any(kw in text for kw in _VIRAL_KEYWORDS):
            return "demo_viral"
        return "default"

    def _substitute_placeholders(self, formula: str, story: dict) -> str:
        """Replace {placeholders} in hook formulas with story data."""
        title = story.get("title", "")
        story.get("source", "")

        # Extract company/product names from title
        company = _extract_company(title)
        product = _extract_product(title)
        topic = _shorten_title(title)

        # Phase 2 C1 (2026-06-30): {duration}, {tool}, {output_type}
        # added for the showcase hook templates. Each has a sensible
        # fallback so showcase formulas don't get filtered out for
        # missing placeholders on a cold-start day.
        replacements = {
            "company": company,
            "product": product,
            "topic": topic,
            "short_title": topic,
            "claim": _extract_claim(title),
            "comparison": _extract_comparison(title),
            "number": _extract_number(title),
            "metric": "hours",
            "duration": _extract_duration(story),
            "tool": _extract_tool(story, product, company),
            "output_type": _extract_output_type(story, title),
        }

        result = formula
        for key, value in replacements.items():
            result = result.replace(f"{{{key}}}", value)

        # If any placeholders remain unfilled, this formula doesn't fit
        if re.search(r"\{[a-z_]+\}", result):
            return ""

        return result


# ── Extraction helpers ──────────────────────────────────────────

_KNOWN_COMPANIES = {
    "openai",
    "google",
    "anthropic",
    "meta",
    "microsoft",
    "nvidia",
    "adobe",
    "apple",
    "amazon",
    "stability",
    "midjourney",
    "runway",
    "luma",
    "pika",
    "kling",
    "sora",
    "deepseek",
    "mistral",
    "cohere",
    "hugging face",
    "replicate",
    "comfyui",
    "eleven labs",
}

_KNOWN_PRODUCTS = {
    "gpt",
    "chatgpt",
    "gpt-4",
    "gpt-5",
    "claude",
    "gemini",
    "sora",
    "midjourney",
    "dall-e",
    "stable diffusion",
    "kling",
    "runway",
    "luma",
    "pika",
    "flux",
    "seedance",
    "comfyui",
    "whisper",
    "copilot",
    "cursor",
    "v0",
    "devin",
    "ray",
    "wan",
}


def _extract_company(title: str) -> str:
    title_lower = title.lower()
    for company in _KNOWN_COMPANIES:
        if company in title_lower:
            # Return with original casing from title
            idx = title_lower.find(company)
            return title[idx : idx + len(company)]
    return ""


def _extract_product(title: str) -> str:
    title_lower = title.lower()
    for product in _KNOWN_PRODUCTS:
        if product in title_lower:
            idx = title_lower.find(product)
            return title[idx : idx + len(product)]
    return ""


def _extract_claim(title: str) -> str:
    """Extract the main claim/action from the title."""
    # Remove company/product names and return the rest
    result = title
    for name in _KNOWN_COMPANIES | _KNOWN_PRODUCTS:
        result = re.sub(re.escape(name), "", result, flags=re.IGNORECASE)
    result = re.sub(r"\s{2,}", " ", result).strip(" —-:,.")
    return result[:50] if result else title[:50]


def _extract_comparison(title: str) -> str:
    """Extract comparison target (after 'vs', 'versus', 'compared to')."""
    match = re.search(r"(?:vs\.?|versus|compared to|better than)\s+(.+?)(?:\s*[—\-,.]|$)", title, re.IGNORECASE)
    return match.group(1).strip()[:30] if match else ""


def _extract_number(title: str) -> str:
    """Extract the first significant number from the title."""
    match = re.search(r"\b(\d+(?:\.\d+)?[KMBx%]?)\b", title)
    return match.group(1) if match else ""


def _shorten_title(title: str) -> str:
    """Shorten title to 6 words max."""
    words = title.split()[:6]
    return " ".join(words)


# ── Phase 2 C1: showcase-template extractors ──────────────────────


def _extract_content_type(story: dict) -> str:
    """Best-effort extraction of content_type from a story.

    Mirrors genlab_core.learning.linucb._extract_content_type so the
    classifier and the LinUCB feature read the SAME signal. Looks in:
      1. Top-level ``content_type``
      2. ``gallery_metadata.content_type``
      3. ``source_config.content_type``
    Returns lowercased string or "" when unset.
    """
    ct = story.get("content_type")
    if isinstance(ct, str) and ct:
        return ct.strip().lower()
    gm = story.get("gallery_metadata") or {}
    if isinstance(gm, dict):
        ct = gm.get("content_type")
        if isinstance(ct, str) and ct:
            return ct.strip().lower()
    sc = story.get("source_config") or {}
    if isinstance(sc, dict):
        ct = sc.get("content_type")
        if isinstance(ct, str) and ct:
            return ct.strip().lower()
    return ""


def _extract_duration(story: dict) -> str:
    """Extract a duration string like "30" for showcase hook templates.

    Prefers ``duration_seconds`` from the story (set by the YouTube/
    pytrends fetcher). Falls back to "30" — a sensible default for
    short-form video. Returns the number as a string (formula does
    "{duration}s ..." so we don't double-format the 's' suffix).
    """
    d = story.get("duration_seconds")
    if isinstance(d, int | float) and d > 0:
        # Round to nearest 5s for nicer copy ("30s" not "27s")
        rounded = max(5, int(round(d / 5.0)) * 5)
        return str(rounded)
    return "30"


def _extract_tool(story: dict, product: str, company: str) -> str:
    """Pick the best 'tool' name to slot into showcase hooks.

    Preference order:
      1. Known product extracted from title (e.g. "Sora", "Midjourney")
      2. Known company extracted from title (e.g. "OpenAI")
      3. ``gallery_metadata.model`` (pixwith stamps the model name)
      4. "AI" as a safe fallback so the hook still reads naturally
    """
    if product:
        return product
    if company:
        return company
    gm = story.get("gallery_metadata") or {}
    if isinstance(gm, dict):
        model = gm.get("model")
        if isinstance(model, str) and model.strip():
            return model.strip()
    return "AI"


_OUTPUT_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "video": ("video", "clip", "reel", "short", "film", "animation"),
    "image": ("image", "picture", "render", "art", "artwork", "photo"),
    "song": ("song", "music", "track", "audio", "voice"),
    "app": ("app", "website", "site", "demo", "tool"),
}


def _extract_output_type(story: dict, title: str) -> str:
    """Heuristically pick "video"/"image"/"song"/"app"/"output" for the
    {output_type} placeholder in showcase hooks.

    Looks in title + gallery_metadata.content_type (pixwith uses values
    like "ai_video" or "image"). Falls back to "output" — generic but
    natural in "Watch this 30s Sora output".
    """
    title_lower = title.lower()
    for output_type, keywords in _OUTPUT_TYPE_KEYWORDS.items():
        if any(kw in title_lower for kw in keywords):
            return output_type
    gm = story.get("gallery_metadata") or {}
    if isinstance(gm, dict):
        ct = gm.get("content_type")
        if isinstance(ct, str) and ct:
            ct_lower = ct.lower().replace("_", " ")
            for output_type, keywords in _OUTPUT_TYPE_KEYWORDS.items():
                if any(kw in ct_lower for kw in keywords):
                    return output_type
    return "output"
