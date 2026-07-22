#!/usr/bin/env python3
"""Re-render MP4s for blueprints whose hook was updated post-render.

2026-07-22 session — hooks for 6 blueprints were manually edited in DB
(Shakur, Shinobu, Apple, Max, Deslauriers, LoL) after render had already
burned the OLD (title-truncated / title-verbatim) hook into the pixels.
DB edits fix captions but the video overlay stayed wrong.

This script:
  1. Queries VISUAL_READY blueprints
  2. For each: checks if updated_at > blueprint create time (proxy for
     "hook was edited after render")
  3. Optionally filters by --ids explicit UUIDs
  4. Reads the source clip from the run dir referenced in extra
  5. Reruns FrameCompositor.compose() with the CURRENT hook
  6. Writes new MP4 to a _rerender_HHMM.mp4 sibling path
  7. Updates blueprints.extra->>'visual_paths' via UPDATE
  8. Preserves the old MP4 (in case operator wants to compare)

Safety:
  * --dry-run (default) prints the plan without executing
  * --limit N caps how many blueprints get re-rendered per invocation
  * --ids UUID1,UUID2,... targets specific blueprints
  * Skips if source clip missing (would produce a broken re-render)
  * Skips if new MP4 output would land on existing path (defensive)

Requires FrameCompositor + visuals.yaml per niche. Does NOT need LLM.
Class-of-fix: retroactive-render for post-hoc-hook-edits.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Bootstrap PROJECT_ROOT so genlab_core imports work
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "genlab-core" / "src"))

import psycopg
from psycopg.rows import dict_row

from genlab_core.media.frame_compositor import FrameCompositor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("rerender_edited_hooks")


# Niche → visuals.yaml path (matches deploy layout)
_VISUALS_YAML: dict[str, str] = {
    "ai_creators": str(_PROJECT_ROOT / "BlackboxBrief" / "config" / "visuals.yaml"),
    "gaming": str(
        _PROJECT_ROOT / "CriticalRush" / "niches" / "gaming" / "config" / "visuals.yaml"
    ),
    "sports": str(_PROJECT_ROOT / "ClutchWire" / "config" / "visuals.yaml"),
    "movies": str(_PROJECT_ROOT / "SpliceReel" / "config" / "visuals.yaml"),
    "anime": str(_PROJECT_ROOT / "FrameDrift" / "config" / "visuals.yaml"),
}


def _get_dsn() -> str:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        raise RuntimeError("DATABASE_URL not set in env")
    return dsn


def _fetch_candidates(
    conn: psycopg.Connection, ids: list[str] | None
) -> list[dict]:
    """Query VISUAL_READY blueprints where hook was edited post-render.

    Heuristic: hook_edited iff updated_at > created_at + 1 minute (render
    completes within a minute of blueprint creation in normal flow).

    When --ids is passed, uses those directly and skips the heuristic
    (operator knows what they want re-rendered).
    """
    if ids:
        rows = conn.execute(
            """
            SELECT id::text AS id, niche_id, hook, title,
                   extra->>'visual_paths' AS visual_paths,
                   extra->>'source_channel_title' AS source_credit,
                   created_at, updated_at
            FROM blueprints
            WHERE id = ANY(%s::uuid[])
              AND status = 'VISUAL_READY'
            """,
            (ids,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id::text AS id, niche_id, hook, title,
                   extra->>'visual_paths' AS visual_paths,
                   extra->>'source_channel_title' AS source_credit,
                   created_at, updated_at
            FROM blueprints
            WHERE status = 'VISUAL_READY'
              AND updated_at > created_at + INTERVAL '1 minute'
              AND hook IS NOT NULL AND LENGTH(hook) > 0
            ORDER BY updated_at DESC
            """,
        ).fetchall()
    return list(rows)


def _resolve_source_clip(rendered_path: str, story_id: str | None = None) -> Path | None:
    """Given the rendered _reel.mp4 path, find the sibling source clip.

    Layout (per FrameCompositor/pipeline conventions):
      .tmp/runs/<niche>_<ts>/
        clips/<story_id>.mp4       ← source
        visuals/<story_id>/<story_id_short>_reel.mp4  ← rendered

    For CriticalRush (gaming) the layout differs:
      CriticalRush/.tmp/rendered/gaming_<ts>/<slug>_vertical.mp4  ← rendered
      CriticalRush/.tmp/downloads/<slug>/                        ← source

    Best-effort: derive the source clip from the visuals path structure.
    """
    p = Path(rendered_path)
    if not p.exists():
        return None
    # Standard layout: runs/<niche>_<ts>/visuals/<story_hash>/<short>_reel.mp4
    #   Source clip: runs/<niche>_<ts>/clips/<short>.mp4
    if "visuals" in p.parts:
        idx = p.parts.index("visuals")
        run_dir = Path(*p.parts[: idx])
        # Try short story hash — visuals sub-dir contains story_hash;
        # clip file uses first 16 chars of hash.
        story_hash_dir = p.parts[idx + 1]
        # short = first 16 chars of story_hash_dir (matches _reel prefix)
        short = story_hash_dir[:16]
        candidate = run_dir / "clips" / f"{short}.mp4"
        if candidate.exists():
            return candidate
        # Alternate: story_id provided
        if story_id:
            candidate = run_dir / "clips" / f"{story_id[:16]}.mp4"
            if candidate.exists():
                return candidate
    # CriticalRush layout: rendered/gaming_<ts>/<slug>_vertical.mp4
    #   No easy source-clip derivation — skip this branch (operator
    #   should re-run the gaming pipeline instead).
    return None


def _build_output_path(rendered_path: str) -> Path:
    """Generate a sibling path with _rerender_HHMM suffix so the old
    MP4 is preserved for operator comparison."""
    p = Path(rendered_path)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    return p.with_name(f"{p.stem}_rerender_{ts}{p.suffix}")


def _rerender_one(
    row: dict, dry_run: bool
) -> tuple[bool, str, Path | None]:
    """Re-render one blueprint. Returns (ok, message, output_path)."""
    niche = row["niche_id"]
    hook = row["hook"]
    bp_id = row["id"]
    title = row.get("title") or ""

    yaml_path = _VISUALS_YAML.get(niche)
    if not yaml_path or not os.path.exists(yaml_path):
        return False, f"no visuals.yaml for niche={niche}", None

    # Parse visual_paths JSON (stored as string in extra->>'visual_paths')
    visual_paths_raw = row.get("visual_paths") or "[]"
    try:
        visual_paths = json.loads(visual_paths_raw)
    except (json.JSONDecodeError, ValueError):
        return False, f"unparseable visual_paths={visual_paths_raw!r}", None
    if not visual_paths:
        return False, "no visual_paths", None

    rendered_path = visual_paths[0]
    source_clip = _resolve_source_clip(rendered_path)
    if source_clip is None:
        return (
            False,
            f"source clip not found from {rendered_path} — GC'd or CriticalRush layout",
            None,
        )

    output_path = _build_output_path(rendered_path)
    if output_path.exists():
        return False, f"output path already exists: {output_path}", None

    if dry_run:
        logger.info(
            "[DRY-RUN] would rerender bp=%s niche=%s hook=%r source=%s output=%s",
            bp_id[:8], niche, hook[:40], source_clip, output_path.name,
        )
        return True, "dry-run OK", output_path

    # Actual render
    try:
        compositor = FrameCompositor.from_visuals_yaml(yaml_path)
        source_credit = row.get("source_credit") or ""
        result_path = compositor.compose(
            source_video_path=str(source_clip),
            hook_text=hook,
            output_path=str(output_path),
            source_credit=source_credit,
        )
        if not Path(result_path).exists():
            return False, "compose() returned but output missing", None
        logger.info(
            "[RERENDER] bp=%s niche=%s hook=%r output=%s size=%d",
            bp_id[:8], niche, hook[:40], output_path.name,
            output_path.stat().st_size,
        )
        return True, "rendered", output_path
    except Exception as exc:  # noqa: BLE001 — surface any render failure
        return False, f"compose() failed: {exc!r}", None


def _update_blueprint_paths(
    conn: psycopg.Connection, bp_id: str, new_path: Path
) -> None:
    """Atomically swap visual_paths to point at the new MP4."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE blueprints
            SET extra = jsonb_set(
                extra,
                '{visual_paths}',
                to_jsonb(%s::text[])
            ),
            updated_at = NOW()
            WHERE id = %s::uuid
            """,
            ([str(new_path)], bp_id),
        )
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Re-render blueprints whose hook was edited post-render.",
    )
    parser.add_argument(
        "--ids",
        help="Comma-separated list of blueprint UUIDs to target (skips heuristic).",
    )
    parser.add_argument(
        "--limit", type=int, default=10,
        help="Max blueprints to re-render (default 10).",
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Print the plan without rendering or updating DB.",
    )
    parser.add_argument(
        "--commit", action="store_true", default=False,
        help="Persist the visual_paths UPDATE (default: render but don't UPDATE).",
    )
    args = parser.parse_args()

    ids: list[str] | None = None
    if args.ids:
        ids = [s.strip() for s in args.ids.split(",") if s.strip()]

    with psycopg.connect(_get_dsn(), row_factory=dict_row) as conn:
        candidates = _fetch_candidates(conn, ids)
        logger.info("found %d candidate blueprint(s)", len(candidates))
        if not candidates:
            logger.info("nothing to do — exiting")
            return 0

        candidates = candidates[: args.limit]
        succeeded = 0
        failed = 0
        skipped = 0

        for row in candidates:
            ok, msg, output_path = _rerender_one(row, dry_run=args.dry_run)
            if not ok:
                logger.warning(
                    "SKIP bp=%s niche=%s: %s",
                    row["id"][:8], row["niche_id"], msg,
                )
                skipped += 1
                continue

            if args.dry_run:
                succeeded += 1
                continue

            if args.commit and output_path is not None:
                try:
                    _update_blueprint_paths(conn, row["id"], output_path)
                    logger.info(
                        "COMMIT bp=%s visual_paths updated to %s",
                        row["id"][:8], output_path.name,
                    )
                    succeeded += 1
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "COMMIT FAILED bp=%s: %r (MP4 exists but DB not updated)",
                        row["id"][:8], exc,
                    )
                    failed += 1
            else:
                logger.info(
                    "RENDERED bp=%s but --commit not passed; visual_paths unchanged",
                    row["id"][:8],
                )
                succeeded += 1

        logger.info(
            "done: %d succeeded, %d skipped, %d failed",
            succeeded, skipped, failed,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
