"""OMDb enrichment client for SpliceReel.

Called selectively from MovieScoringStrategy for high-scoring candidates only.
Free tier: 1,000 requests/day. Returns IMDB rating, RT score, Metacritic, box office.
Never called in bulk — only for items above pre_enrich_threshold (default 0.45).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class OMDbResult:
    """Enrichment data returned from OMDb for a single film."""

    __slots__ = (
        "imdb_id", "imdb_rating", "rt_score",
        "metacritic_score", "box_office", "awards",
    )

    def __init__(
        self,
        imdb_id: str = "",
        imdb_rating: float | None = None,
        rt_score: int | None = None,
        metacritic_score: int | None = None,
        box_office: str = "",
        awards: str = "",
    ) -> None:
        self.imdb_id = imdb_id
        self.imdb_rating = imdb_rating
        self.rt_score = rt_score
        self.metacritic_score = metacritic_score
        self.box_office = box_office
        self.awards = awards


class OMDbClient:
    """Selective OMDb enrichment client. Use as context manager."""

    BASE = "https://www.omdbapi.com"

    DAILY_LIMIT = 900  # Conservative (free tier is 1000)

    _daily_count = 0
    _day_start = time.time()
    _lock = threading.Lock()

    def __init__(self, api_key: str, timeout: int = 8) -> None:
        self._api_key = api_key
        self._client = httpx.Client(timeout=timeout)

    def _check_daily_limit(self) -> None:
        """Enforce daily request limit. Raises RuntimeError if exceeded."""
        with self._lock:
            now = time.time()
            if now - OMDbClient._day_start > 86400:
                OMDbClient._daily_count = 0
                OMDbClient._day_start = now
            if OMDbClient._daily_count >= self.DAILY_LIMIT:
                raise RuntimeError(
                    f"OMDB daily limit ({self.DAILY_LIMIT}) reached. Resuming tomorrow."
                )
            OMDbClient._daily_count += 1

    def enrich_by_title(self, title: str, year: int | None = None) -> OMDbResult | None:
        """Fetch enrichment data for a film by title.

        Returns None on any failure. Never raises.
        """
        if not self._api_key or self._api_key.startswith("${"):
            logger.debug("[omdb] API key not set — skipping '%s'", title)
            return None

        try:
            self._check_daily_limit()
            params: dict[str, Any] = {
                "apikey": self._api_key,
                "t": title,
                "type": "movie",
                "plot": "short",
            }
            if year:
                params["y"] = str(year)

            resp = self._client.get(self.BASE, params=params)
            resp.raise_for_status()
            data = resp.json()

            if data.get("Response") == "False":
                logger.debug("[omdb] not found: '%s' — %s", title, data.get("Error", ""))
                return None

            return self._parse_response(data)

        except Exception as e:
            logger.warning("[omdb] enrichment failed for '%s': %s", title, e)
            return None

    @staticmethod
    def _parse_response(data: dict) -> OMDbResult:
        """Parse OMDb JSON response into OMDbResult."""
        imdb_rating: float | None = None
        raw_imdb = data.get("imdbRating", "N/A")
        if raw_imdb != "N/A":
            try:
                imdb_rating = float(raw_imdb)
            except ValueError:
                pass

        rt_score: int | None = None
        for rating in data.get("Ratings", []):
            if rating.get("Source") == "Rotten Tomatoes":
                try:
                    rt_score = int(rating["Value"].replace("%", ""))
                except (ValueError, KeyError):
                    pass

        metacritic: int | None = None
        raw_mc = data.get("Metascore", "N/A")
        if raw_mc != "N/A":
            try:
                metacritic = int(raw_mc)
            except ValueError:
                pass

        return OMDbResult(
            imdb_id=data.get("imdbID", ""),
            imdb_rating=imdb_rating,
            rt_score=rt_score,
            metacritic_score=metacritic,
            box_office=data.get("BoxOffice", ""),
            awards=data.get("Awards", ""),
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
