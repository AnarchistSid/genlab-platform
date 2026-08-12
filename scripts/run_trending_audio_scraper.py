"""Runner for the trending-audio-scraper systemd timer.

Iterates all 5 niches, reads each niche's music_mood vocabulary from
visuals.yaml, and calls scrape_and_cache_trending_moods.

Per rule #26: exits 0 unless a genuine incident requires operator
paging. "Scraper found no tracks" is a data-side signal (LLM-usable
via cache staleness detection); don't route it to systemd exit code.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import yaml

from genlab_core.media.trending_audio_scraper import scrape_and_cache_trending_moods

logger = logging.getLogger(__name__)


_NICHE_VISUALS = {
    "ai_creators": "BlackboxBrief/config/visuals.yaml",
    "gaming": "CriticalRush/niches/gaming/config/visuals.yaml",
    "sports": "ClutchWire/niches/sports/config/visuals.yaml",
    "movies": "SpliceReel/niches/movies/config/visuals.yaml",
    "anime": "FrameDrift/niches/anime/config/visuals.yaml",
}


def _load_niche_moods(project_root: Path, niche_id: str) -> list[str]:
    rel = _NICHE_VISUALS.get(niche_id)
    if not rel:
        return []
    v_path = project_root / rel
    if not v_path.exists():
        logger.warning(
            "[trending_audio_scraper_runner] visuals.yaml missing niche=%s path=%s",
            niche_id, v_path,
        )
        return []
    try:
        y = yaml.safe_load(v_path.read_text()) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[trending_audio_scraper_runner] visuals.yaml parse failed "
            "niche=%s: %s", niche_id, exc,
        )
        return []
    dims = (y.get("intelligent_transform") or {}).get("dimensions") or {}
    music_mood = dims.get("music_mood") or {}
    moods = music_mood.get("moods") or []
    return [str(m) for m in moods]


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    from genlab_core.observability.flag_audit import log_active_flags
    try:
        log_active_flags(context="trending_audio_scraper")
    except Exception:
        pass

    project_root = Path(__file__).resolve().parents[1]
    niches = list(_NICHE_VISUALS.keys())

    successes = 0
    for niche in niches:
        moods = _load_niche_moods(project_root, niche)
        if not moods:
            logger.info(
                "[trending_audio_scraper_runner] niche=%s has no music_mood "
                "vocabulary — skipping", niche,
            )
            continue
        ok = scrape_and_cache_trending_moods(niche, moods)
        if ok:
            successes += 1
        else:
            logger.warning(
                "[trending_audio_scraper_runner] niche=%s scrape returned False "
                "(no data written)", niche,
            )

    logger.info(
        "[trending_audio_scraper_runner] complete successes=%d/%d niches",
        successes, len(niches),
    )
    # Rule #26: exit 0 always — "no data" is not an incident
    return 0


if __name__ == "__main__":
    sys.exit(main())
