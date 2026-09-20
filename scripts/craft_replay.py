#!/usr/bin/env python3
"""Run CraftRenderStage on an EXISTING blueprint, without a pipeline fire.

WHY THIS EXISTS. Proving the craft chain needed an ACTION blueprint, and the
only fire available produced none: every fresh URL had been blueprinted ninety
minutes earlier by a manual fire, so dedup correctly returned nothing. The
alternative on the table was to seed a story and teach the publisher a
`no_publish` marker -- changing the publish path to run a test, which is the
wrong shape. The records already exist; replay the stage on one.

It is also permanent. When a kit or a template changes, craft is re-run on a
known blueprint and the two deliverables compared, with no fire and no publish.

WHAT IT WILL NOT DO
    - touch `visual_paths` (the legacy reel is the published artifact)
    - touch `scheduled_for` (a replay must never enter the publish queue)
    - run without the source clip on disk
Both invariants are asserted before and after, not assumed.
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("craft-replay")

#: Written by PushToBacklog since 7935ac08. Blueprints created before it carry
#: none of them, which is exactly the population worth replaying.
PROPAGATED = ("clip_path", "is_highlight", "source_url")

#: Never modified by a replay. Compared byte-for-byte before and after.
FROZEN = ("visual_paths", "scheduled_for")


def _clip_for(story_id: str) -> str:
    """The downloaded clip for a story, from any run's clip index."""
    best = ""
    for p in sorted(Path("/opt/genlab/.tmp/runs").glob("*/clip_index.json")):
        try:
            clips = (json.loads(p.read_text()).get("clips") or {}).get(story_id) or {}
        except (OSError, ValueError):
            continue
        cp = clips.get("clip_path") or ""
        if cp and Path(cp).exists():
            best = cp
    return best


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--blueprint", required=True, help="record id (or its unique prefix)")
    ap.add_argument("--niche", default="sports")
    ap.add_argument("--dry-run", action="store_true", help="report what would change, run nothing")
    ap.add_argument(
        "--is-highlight",
        choices=("true", "false"),
        help=(
            "ASSERT the highlight flag for a record that predates its persistence. "
            "Stories written before 2026-09-20 do not carry is_highlight -- the fetcher "
            "set it in memory and the store never wrote it -- so an old blueprint cannot "
            "supply it. This is an operator ASSERTION, logged as one; it is not inferred."
        ),
    )
    a = ap.parse_args()

    sys.path.insert(0, "genlab-core/src")
    from genlab_core.pipeline.stages.craft_render import CraftRenderStage
    from genlab_core.rendering.render_engine import craft_publishes, dual_render_enabled
    from genlab_core.storage.postgres import PostgresBackend

    be = PostgresBackend()
    rows = be.find("blueprints", niche_id=a.niche, max_records=400, order_by="created_at DESC")
    match = [r for r in rows if str(r.get("id", "")).startswith(a.blueprint)]
    if len(match) != 1:
        logger.error("blueprint %r matched %d records", a.blueprint, len(match))
        return 2
    row = match[0]
    bp = dict(row.get("fields") or {})
    bp["record_id"] = row["id"]

    # 1. THE CLIP IS THE PRECONDITION. Craft cannot plan without footage, and a
    #    replay that skips for want of it has proved nothing.
    story_id = bp.get("story_id") or ""
    clip = bp.get("clip_path") or _clip_for(story_id)
    if not clip or not Path(clip).exists():
        logger.error("no source clip on disk for story %s (clip_path=%r)", story_id, clip)
        return 3
    logger.info("clip: %s", clip)

    # 2. BACKFILL ONLY WHAT IS ABSENT, and say so. These are the fields
    #    PushToBacklog writes now; this record predates that.
    backfill = {}
    if not bp.get("clip_path"):
        backfill["clip_path"] = clip
    if bp.get("is_highlight") is None:
        # FROM THE STORY, NOT FROM THE SOURCE STRING. The fetcher decides
        # is_highlight and writes it on the story; inferring it here from
        # `source.startswith("youtube")` would be an invented input, which is
        # what put the archive's own window out of a verification run once
        # already. If the story does not carry it, refuse.
        story = None
        if story_id:
            found = be.find(
                "stories", niche_id=a.niche, max_records=400, order_by="created_at DESC"
            )
            story = next(
                (
                    s_
                    for s_ in found
                    if (s_.get("fields") or {}).get("story_id") == story_id
                    or str(s_.get("id", "")) == story_id
                ),
                None,
            )
        sf = (story or {}).get("fields") or {}
        if a.is_highlight is not None:
            backfill["is_highlight"] = a.is_highlight == "true"
            logger.warning(
                "is_highlight=%s ASSERTED BY OPERATOR (story %s does not carry it)",
                backfill["is_highlight"],
                story_id or "<none>",
            )
        elif "is_highlight" in sf:
            backfill["is_highlight"] = bool(sf["is_highlight"])
        else:
            logger.error(
                "story %s carries no is_highlight; refusing to infer it from the source "
                "string. Pass a blueprint whose story has the flag.",
                story_id or "<none>",
            )
            return 5
    if not bp.get("source_url"):
        backfill["source_url"] = bp.get("video_url") or ""
    for k, v in backfill.items():
        logger.info("backfill %s = %r (was absent)", k, v)
    bp.update(backfill)

    frozen_before = {k: copy.deepcopy(bp.get(k)) for k in FROZEN}
    logger.info(
        "engine: dual=%s craft_publishes=%s",
        dual_render_enabled(a.niche, {}),
        craft_publishes(a.niche, {}),
    )
    if a.dry_run:
        logger.info(
            "dry run: would replay %s with %d backfilled field(s)", row["id"], len(backfill)
        )
        return 0

    # 3. THE SAME CONTEXT THE STAGE SEES ON A FIRE.
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    run_id = f"replay-{day}-{row['id'][:8]}"
    run_dir = Path("/opt/genlab/.tmp/runs") / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    context = {
        "niche_id": a.niche,
        "niche_config": {},
        "blueprints": [bp],
        "stories": [],
        "run_id": run_id,
        "run_dir": str(run_dir),
        "project_root": "/opt/genlab",
        "replay": True,
        "run_stats": {},
    }

    CraftRenderStage().execute(context)

    # 4. THE INVARIANTS, CHECKED RATHER THAN TRUSTED.
    rc = 0
    for k in FROZEN:
        if bp.get(k) != frozen_before[k]:
            logger.error("REPLAY MUTATED %s: %r -> %r", k, frozen_before[k], bp.get(k))
            rc = 4
    if rc == 0:
        logger.info("invariants hold: %s unchanged", ", ".join(FROZEN))

    logger.info("craft stats: %s", json.dumps(context.get("run_stats", {}).get("render", {})))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
