"""Gemini 2.5 Flash video analysis — non-blocking pipeline enrichment.

Analyzes video content for: entities, products, visual quality, suggested hooks.
Cost: ~$0.005 per 60-second video.

Gracefully degrades: if GOOGLE_API_KEY missing or Gemini fails,
returns empty dict and pipeline continues with text-only metadata.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class VideoAnalyzer:
    """Analyze video content using Gemini 2.5 Flash."""

    def __init__(self):
        self._api_key = os.environ.get("GOOGLE_API_KEY", "")
        self._available = bool(self._api_key)
        if not self._available:
            logger.debug("[VideoAnalyzer] GOOGLE_API_KEY not set — video analysis disabled")

    @property
    def available(self) -> bool:
        return self._available

    def analyze(
        self, video_path: str | Path, niche_id: str = "", max_duration_seconds: int = 60
    ) -> dict[str, Any]:
        """Analyze a video file and return structured insights.

        Returns dict with: description, key_entities, visual_quality (0-1),
        suggested_hook (≤60 chars), detected_products, best_moment_seconds.
        Returns empty dict on any failure (non-blocking).
        """
        if not self._available:
            return {}

        video_path = Path(video_path)
        if not video_path.exists():
            return {}

        # Skip very large files (>100MB) to avoid upload timeouts
        if video_path.stat().st_size > 100 * 1024 * 1024:
            logger.debug("[VideoAnalyzer] File too large: %s", video_path.name)
            return {}

        try:
            import google.generativeai as genai

            genai.configure(api_key=self._api_key)

            # Upload video
            video_file = genai.upload_file(str(video_path))

            # Wait for processing
            import time

            for _ in range(30):  # max 30 seconds wait
                video_file = genai.get_file(video_file.name)
                if video_file.state.name == "ACTIVE":
                    break
                time.sleep(1)

            if video_file.state.name != "ACTIVE":
                logger.warning("[VideoAnalyzer] Video processing timed out")
                return {}

            model = genai.GenerativeModel("gemini-2.5-flash-preview-04-17")
            prompt = (
                f"Analyze this short video clip for a {niche_id or 'content'} channel. "
                "Return ONLY valid JSON with these fields:\n"
                '{"description": "2-3 sentence description of what happens in the video",'
                '"key_entities": ["list", "of", "people", "teams", "products", "shown"],'
                '"visual_quality": 0.85,'
                '"suggested_hook": "60 chars max, attention-grabbing hook for this video",'
                '"detected_products": ["any", "visible", "products", "or", "brands"],'
                '"best_moment_seconds": 5}'
            )

            response = model.generate_content([video_file, prompt])
            text = response.text.strip()

            # Parse JSON from response
            if text.startswith("```"):
                text = text.split("```")[1].lstrip("json\n")
            result = json.loads(text)

            # Cleanup uploaded file
            try:
                genai.delete_file(video_file.name)
            except Exception:
                pass

            logger.info(
                "[VideoAnalyzer] Analyzed %s: %d entities, quality=%.2f",
                video_path.name,
                len(result.get("key_entities", [])),
                result.get("visual_quality", 0),
            )
            return result

        except ImportError:
            logger.debug("[VideoAnalyzer] google-generativeai not installed")
            self._available = False
            return {}
        except Exception as exc:
            logger.warning("[VideoAnalyzer] Analysis failed: %s", str(exc)[:80])
            return {}
