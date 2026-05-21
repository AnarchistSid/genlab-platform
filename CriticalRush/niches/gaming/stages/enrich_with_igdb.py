"""Pipeline stage: Enrich stories with IGDB game metadata.

Runs after FETCH and before EXTRACT_MEDIA. Calls IGDBClient.enrich_story()
on each story missing igdb_game_id or steam_app_id.

Usage:
    stage = EnrichWithIGDB()
    context = stage.execute(context)
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore[assignment]

from genlab_core.cost.model_router import get_model
from genlab_core.settings import settings
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

_LLM_RETRYABLE = (
    (
        anthropic.APIConnectionError,
        anthropic.APITimeoutError,
        anthropic.InternalServerError,
        anthropic.RateLimitError,
    )
    if anthropic is not None
    else (Exception,)
)

logger = logging.getLogger(__name__)

# Patterns that indicate the rest of the headline is editorial, not a game name.
_EDITORIAL_SPLITS = re.compile(
    r"\s*(?:"
    r"(?:is\s+the\s+#?\d)|"  # "is the #1 seller..."
    r"(?:players?\s+(?:are|accidentally|need|can't))|"  # "players accidentally..."
    r"(?:day\s+one\s+check)|"  # "day one check-in"
    r"(?:launches?\b)|"  # "Launches, Immediately..."
    r"(?:impressions?\b)|"  # "impressions"
    r"(?:review\b)|"  # "Review"
    r"(?:preview\b)|"  # "Preview"
    r"(?:guide\b)|"  # "Guide"
    r"(?:tips?\b)|"  # "Tips"
    r"(?:best\s)|"  # "Best Marathon characters..."
    r"(?:\d+\s+things?\b)|"  # "7 Things..."
    r"(?:how\s+to\b)|"  # "How to..."
    r"(?:promises?\b)|"  # "Promises War On..."
    r"(?:update\b)|"  # "update"
    r"(?:patch\s+notes?\b)|"  # "patch notes"
    r"(?:dlc\b)|"  # "DLC"
    r"(?:announced?\b)|"  # "announced"
    r"(?:trailer\b)"  # "trailer"
    r")",
    re.IGNORECASE,
)

# Common suffixes to strip from the extracted game name.
_SUFFIX_NOISE = re.compile(r"\s*[-–—:,]\s*$")


def _extract_game_name(headline: str) -> str | None:
    """Extract the likely game name from an article headline.

    Strategy: split on editorial patterns, take the first segment,
    clean trailing punctuation. If the result is too short (<3 chars)
    or too long (>60 chars), return None to skip enrichment.
    """
    if not headline:
        return None

    # Split on first editorial pattern match
    parts = _EDITORIAL_SPLITS.split(headline, maxsplit=1)
    candidate = parts[0].strip()

    # Clean trailing punctuation/separators
    candidate = _SUFFIX_NOISE.sub("", candidate).strip()

    # Sanity checks
    if len(candidate) < 3 or len(candidate) > 60:
        return None

    # If candidate is same as headline, it might be a clean game name already
    return candidate


def _extract_game_name_llm(headline: str, api_key: str) -> str | None:
    """Fallback: ask Claude Haiku to extract the game name from a headline."""

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(_LLM_RETRYABLE),
        reraise=True,
    )
    def _call(client: anthropic.Anthropic) -> str:
        response = client.messages.create(
            model=get_model("extract_game_name"),
            max_tokens=60,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Given this gaming article headline, what game is it about? "
                        "Reply with only the game name, nothing else. If you cannot "
                        "determine the game, reply with exactly: unknown\n\n"
                        f"Headline: {headline}"
                    ),
                }
            ],
        )
        return response.content[0].text.strip()

    try:
        client = anthropic.Anthropic(api_key=api_key)
        result = _call(client)

        # Cost tracking (best-effort; _call returns text only, so estimate tokens)
        from genlab_core.intelligence.cost_accumulator import get_accumulator

        acc = get_accumulator()
        if acc:
            # Approximate: short prompt (~50 tokens in, ~10 tokens out)
            acc.record_llm(get_model("extract_game_name"), 50, len(result.split()))

        if result.lower() == "unknown" or len(result) < 2 or len(result) > 80:
            return None
        logger.info("[ENRICH] LLM extracted game name '%s' from '%s'", result, headline)
        return result
    except Exception as e:
        logger.warning("[ENRICH] LLM game name extraction failed: %s", e)
        return None


class EnrichWithIGDB:
    """Enrich pipeline stories with IGDB game metadata."""

    INTER_STORY_DELAY_SECONDS = 0.26  # IGDB rate limit: 4 req/sec

    def __init__(self):
        self._client = None
        self._name_cache: dict[str, str | None] = {}  # headline → game name

    def _get_client(self):
        """Lazy-initialize the IGDB client."""
        if self._client is None:
            from niches.gaming.tools.igdb_client import IGDBClient

            self._client = IGDBClient()
        return self._client

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        stories = context.get("stories", [])
        client = self._get_client()

        enriched_count = 0
        skipped_count = 0
        failed_count = 0

        for i, story in enumerate(stories):
            title = story.get("title", "")

            # Skip stories that already have IGDB data
            if story.get("igdb_game_id") and story.get("steam_app_id"):
                skipped_count += 1
                continue

            # Extract the game name from the headline before querying IGDB
            game_name = _extract_game_name(title)
            if not game_name:
                # LLM fallback: ask Claude Haiku to identify the game
                if title in self._name_cache:
                    game_name = self._name_cache[title]
                else:
                    api_key = settings.anthropic_api_key
                    if api_key:
                        game_name = _extract_game_name_llm(title, api_key)
                    self._name_cache[title] = game_name
                if not game_name:
                    logger.info("[ENRICH] Could not extract game name from '%s'", title)
                    failed_count += 1
                    continue

            try:
                before_keys = set(story.keys())
                result = client.search_game(game_name)
                if result:
                    for key, value in result.items():
                        if value is not None:
                            story[key] = value
                    logger.info(
                        "[ENRICH] '%s' → game='%s' → igdb=%s",
                        title,
                        game_name,
                        result.get("igdb_game_id"),
                    )
                after_keys = set(story.keys())

                if after_keys - before_keys:
                    enriched_count += 1
                else:
                    failed_count += 1
            except Exception as e:
                logger.warning("[ENRICH] Failed for '%s': %s", title, e)
                failed_count += 1

            if i < len(stories) - 1:
                time.sleep(self.INTER_STORY_DELAY_SECONDS)

        unlocked = sum(1 for s in stories if s.get("igdb_game_id") or s.get("steam_app_id"))

        context.setdefault("run_stats", {})["igdb_enrichment"] = {
            "enriched_count": enriched_count,
            "skipped_count": skipped_count,
            "failed_count": failed_count,
        }

        logger.info(
            "[ENRICH] %d enriched, %d already had IDs, %d failed "
            "— IGDB clip sourcing unlocked for %d stories",
            enriched_count,
            skipped_count,
            failed_count,
            unlocked,
        )

        return context
