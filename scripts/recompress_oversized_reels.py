"""V5: Re-encode oversized rendered reels with bitrate caps.

Identifies VISUAL_READY blueprint reels with bitrate >5 Mbps OR file size
>30MB, then re-encodes them via ffmpeg with:
  -c:v libx264 -crf 22 -maxrate 5M -bufsize 10M -preset medium -c:a copy

Output replaces the master in-place (atomic via temp file). The existing
reel already has layout/logo/hook baked in — we just need to shrink it
to platform-acceptable bitrate so the per-platform transcode at publish
time has a reasonable starting point.

Why this beats full re-render via FrameCompositor:
  - No source clip dependency (some are gone from .tmp/runs/)
  - 30-60s per file vs 2-5min for full re-render
  - Layout/branding/hook preservation guaranteed (we don't touch the
    rendered frame contents, just re-encode the bitstream)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

os.environ.setdefault("GENLAB_USE_POSTGRES", "true")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("recompress")

DSN = os.environ["DATABASE_URL"]
SIZE_LIMIT_MB = 30
BITRATE_LIMIT_MBPS = 5
TARGET_CRF = 22
TARGET_MAXRATE = "5M"
TARGET_BUFSIZE = "10M"
FFMPEG_TIMEOUT = 600


def fetch_targets() -> list[dict]:
    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SET app.niche_id = 'all'")
            cur.execute("""
                SELECT id, niche_id, title, extra->>'visual_paths' AS visual_paths
                FROM blueprints
                WHERE status = 'VISUAL_READY'
                  AND created_at > NOW() - INTERVAL '7 days'
                ORDER BY niche_id, scheduled_for
            """)
            return list(cur.fetchall())


def probe(path: Path) -> tuple[int, int] | None:
    """Return (size_mb, bitrate_mbps) or None if probe fails."""
    if not path.exists():
        return None
    try:
        size_bytes = path.stat().st_size
        size_mb = size_bytes // (1024 * 1024)
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=bit_rate",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        bitrate = int(result.stdout.strip()) if result.stdout.strip().isdigit() else 0
        bitrate_mbps = bitrate // 1_000_000
        return size_mb, bitrate_mbps
    except Exception as exc:
        logger.warning("Probe failed for %s: %s", path, exc)
        return None


def needs_recompress(probe_result: tuple[int, int] | None) -> bool:
    if probe_result is None:
        return False
    size_mb, bitrate_mbps = probe_result
    return size_mb > SIZE_LIMIT_MB or bitrate_mbps > BITRATE_LIMIT_MBPS


def recompress(path: Path) -> bool:
    """Re-encode in-place with bitrate cap. Atomic via temp file."""
    tmp = path.with_suffix(".recompress.mp4")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(path),
        "-c:v",
        "libx264",
        "-crf",
        str(TARGET_CRF),
        "-maxrate",
        TARGET_MAXRATE,
        "-bufsize",
        TARGET_BUFSIZE,
        "-preset",
        "medium",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(tmp),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=FFMPEG_TIMEOUT,
        )
        if result.returncode != 0:
            logger.error("ffmpeg failed for %s: %s", path.name, result.stderr[-300:])
            tmp.unlink(missing_ok=True)
            return False
        shutil.move(str(tmp), str(path))
        return True
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg timeout (%ds) for %s", FFMPEG_TIMEOUT, path.name)
        tmp.unlink(missing_ok=True)
        return False
    except Exception as exc:
        logger.exception("ffmpeg crash for %s: %s", path.name, exc)
        tmp.unlink(missing_ok=True)
        return False


def main() -> int:
    targets = fetch_targets()
    logger.info("Scanning %d VISUAL_READY blueprints", len(targets))

    succeeded = 0
    skipped_already_ok = 0
    failed = 0

    for bp in targets:
        try:
            paths = json.loads(bp["visual_paths"] or "[]")
        except json.JSONDecodeError:
            continue
        if not paths:
            continue
        path = Path(paths[0])
        if not path.exists():
            logger.warning("[%s] missing: %s", bp["id"], path)
            continue

        before = probe(path)
        if not needs_recompress(before):
            skipped_already_ok += 1
            continue

        sz_mb, br_mbps = before
        logger.info(
            "[%s] %s/%s  before: %d MB / %d Mbps  re-encoding...",
            str(bp["id"])[:8],
            bp["niche_id"],
            path.name,
            sz_mb,
            br_mbps,
        )
        if not recompress(path):
            failed += 1
            continue
        after = probe(path)
        if after:
            logger.info(
                "[%s]   after:  %d MB / %d Mbps",
                str(bp["id"])[:8],
                after[0],
                after[1],
            )
        succeeded += 1

    logger.info(
        "V5 done: %d re-encoded, %d already OK, %d failed",
        succeeded,
        skipped_already_ok,
        failed,
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
