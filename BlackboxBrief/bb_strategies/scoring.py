"""BB scoring strategy — wraps dedupe_rank_items.py."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from genlab_core.strategies import ScoringStrategy

BB_ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)


class BBScoringStrategy(ScoringStrategy):
    """Three-pass dedup, clustering, and scoring of parsed AI/tech items."""

    def __init__(self) -> None:
        pass

    def execute(self, context: Any) -> Any:
        from bb_strategies._scoring import (
            cluster_stories,
            dedup_pass_title,
            dedup_pass_url,
            filter_low_quality,
            load_weights,
            rank_items,
        )

        run_id = context.get("run_id", "")
        run_dir = BB_ROOT / ".tmp" / "runs" / run_id

        # Load parsed items from disk (written by content_research stage)
        parsed_path = run_dir / "parsed_items.json"
        if not parsed_path.exists():
            logger.error("[ai_creators] parsed_items.json not found at %s", parsed_path)
            return context

        with open(parsed_path) as f:
            parsed = json.load(f)

        items = parsed.get("items", [])
        logger.info("[ai_creators] Scoring %d items", len(items))

        # Quarantine injection-flagged items
        flagged_items = [i for i in items if i.get("injection_flags")]
        if flagged_items:
            items = [i for i in items if not i.get("injection_flags")]

        config = load_weights()
        threshold = config.get("novelty_threshold", 0.85)
        total_start = time.time()

        # Three-pass dedup
        items, url_removed = dedup_pass_url(items)
        items, title_removed = dedup_pass_title(items, threshold)
        items, quality_removed = filter_low_quality(items)

        # Score, rank, cluster
        items = rank_items(items, config)
        items, clustering_stats = cluster_stories(items, config)

        total_time = round(time.time() - total_start, 2)

        output = {
            "run_id": run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "input_count": parsed.get("item_count", 0),
            "output_count": len(items),
            "dedup_stats": {
                "injection_quarantined": len(flagged_items),
                "url_duplicates_removed": url_removed,
                "title_duplicates_removed": title_removed,
                "quality_filtered": quality_removed,
            },
            "clustering_stats": clustering_stats,
            "stories": items,
        }

        out_path = run_dir / "ranked_stories.json"
        with open(out_path, "w") as f:
            json.dump(output, f, indent=2)

        logger.info(
            "[ai_creators] Ranked %d stories in %.1fs (url-%d title-%d quality-%d)",
            len(items), total_time, url_removed, title_removed, quality_removed,
        )

        # Also filter context["stories"] with visual potential gate
        from genlab_core.scoring.composite_scorer import score_visual_potential

        stories = context.get("stories", [])
        visual_passed = []
        visual_rejected = 0
        for s in stories:
            vp = score_visual_potential(s, "ai_creators")
            s["visual_potential"] = vp
            if vp >= 0.3:
                visual_passed.append(s)
            else:
                visual_rejected += 1
                logger.info(
                    "[ai_creators] Visual potential rejected (%.1f): %s",
                    vp, s.get("title", "")[:60],
                )
        context["stories"] = visual_passed

        # Trim to top N by score (from context stories — YouTube trending + RSS)
        top_n = 5
        if len(context["stories"]) > top_n:
            context["stories"] = context["stories"][:top_n]

        context.setdefault("run_stats", {})["scoring"] = {
            "input_count": parsed.get("item_count", 0),
            "scored_count": len(items),
            "url_removed": url_removed,
            "title_removed": title_removed,
            "quality_filtered": quality_removed,
            "visual_rejected": visual_rejected,
        }
        return context
