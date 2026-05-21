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
_CONTROVERSY_KEYWORDS = {"debate", "controversy", "vs", "versus", "fight", "war", "lawsuit", "ban", "problem", "dangerous"}
_CURIOSITY_KEYWORDS = {"secret", "hidden", "quietly", "nobody knows", "doesn't want", "behind the scenes"}
_VIRAL_KEYWORDS = {"viral", "views", "trending", "broke", "insane", "wild", "unreal", "out-performed"}


class BBHookStrategy(BaseHookStrategy):
    """Generate hooks for AI creator content.

    Categories: tool_launch, creator_showcase, controversy, demo_viral, default.
    """

    _title_fallback_label = "AI moment"

    def __init__(self) -> None:
        super().__init__(niche_id="ai_creators", niche_root=BB_ROOT)

    def _classify_story(self, story: dict) -> str:
        """Classify AI story into a hook category."""
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

        replacements = {
            "company": company,
            "product": product,
            "topic": topic,
            "short_title": topic,
            "claim": _extract_claim(title),
            "comparison": _extract_comparison(title),
            "number": _extract_number(title),
            "metric": "hours",
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
    "openai", "google", "anthropic", "meta", "microsoft", "nvidia",
    "adobe", "apple", "amazon", "stability", "midjourney", "runway",
    "luma", "pika", "kling", "sora", "deepseek", "mistral", "cohere",
    "hugging face", "replicate", "comfyui", "eleven labs",
}

_KNOWN_PRODUCTS = {
    "gpt", "chatgpt", "gpt-4", "gpt-5", "claude", "gemini", "sora",
    "midjourney", "dall-e", "stable diffusion", "kling", "runway",
    "luma", "pika", "flux", "seedance", "comfyui", "whisper",
    "copilot", "cursor", "v0", "devin", "ray", "wan",
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
