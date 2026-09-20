#!/usr/bin/env python3
"""Copy every scheduled blueprint's media somewhere that will not be pruned.

WHY. 28 approved reels sit in the publish queue 1-7 days out. Their media lives
in per-run directories that prune to the last three runs. The two clocks were
never reconciled: the queue's horizon is a week, the retention is a few hours of
pipeline activity. Two ai_creators blueprints had already lost their media and
jammed the publisher for three days; the rest are on the same clock.

This is the emergency half -- minutes, no code change. The approval path does it
automatically afterwards.

SAFE BY CONSTRUCTION
    - copies, never moves: the run directory is left exactly as found
    - sha256 on both sides before the record is touched
    - one update per blueprint, rewriting visual_paths to the durable copy
    - a blueprint whose media is already gone is REPORTED, never rewritten
    - --apply is required; the default is a dry run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("media-backfill")

NICHES = ("ai_creators", "gaming", "sports", "movies", "anime")
DURABLE_ROOT = Path("/opt/genlab/.media/scheduled")


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _paths(fields: dict) -> list[str]:
    raw = fields.get("visual_paths")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = [raw]
    return [str(p) for p in (raw or []) if str(p).strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write; default is a dry run")
    ap.add_argument("--niche", default="", help="one niche, or all")
    a = ap.parse_args()

    sys.path.insert(0, "genlab-core/src")
    from genlab_core.http.backlog_client import BacklogClient
    from genlab_core.storage.postgres import PostgresBackend

    be = PostgresBackend()
    client = BacklogClient() if a.apply else None
    now = datetime.now().isoformat()
    copied = already = missing = failed = 0

    for n in [a.niche] if a.niche else NICHES:
        for r in be.find("blueprints", niche_id=n, max_records=400, order_by="created_at DESC"):
            f = r.get("fields") or {}
            if str(f.get("status")) != "VISUAL_READY":
                continue
            sf = str(f.get("scheduled_for") or "").strip()
            if not sf or sf < now:
                continue
            rid = r["id"]
            paths = [Path(p) for p in _paths(f)]
            if paths and all(str(p).startswith(str(DURABLE_ROOT)) for p in paths):
                already += 1
                continue
            live = [p for p in paths if p.exists()]
            if not paths or not live:
                missing += 1
                logger.warning(
                    "%s %s — media GONE (%s); left for the archive path",
                    n,
                    rid[:8],
                    str(f.get("title"))[:40],
                )
                continue

            dest = DURABLE_ROOT / rid
            new_paths = []
            try:
                if a.apply:
                    dest.mkdir(parents=True, exist_ok=True)
                for src in live:
                    tgt = dest / src.name
                    if a.apply:
                        shutil.copy2(src, tgt)
                        if sha256(src) != sha256(tgt):
                            raise OSError(f"sha mismatch for {src.name}")
                    new_paths.append(str(tgt))
                if a.apply:
                    client.blueprints.update(
                        rid, {"visual_paths": json.dumps(new_paths)}, typecast=True
                    )
                copied += 1
                logger.info(
                    "%s %s — %d file(s) -> %s%s",
                    n,
                    rid[:8],
                    len(new_paths),
                    dest,
                    "" if a.apply else "  [DRY RUN]",
                )
            except Exception as exc:  # noqa: BLE001 — one bad record must not stop the rest
                failed += 1
                logger.error("%s %s — copy FAILED: %s", n, rid[:8], exc, exc_info=True)

    logger.info(
        "%s: %d copied, %d already durable, %d media-gone, %d failed",
        "APPLIED" if a.apply else "DRY RUN",
        copied,
        already,
        missing,
        failed,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
