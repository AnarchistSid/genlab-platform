#!/usr/bin/env python3
"""Idempotent Pixabay music-bed seeder for AudioReplacer's per-niche
library (sprint activation follow-up, 2026-07-05).

Reads a JSON manifest of ``{niche_id: {mood: [{cdn_url, title, id},
...]}}`` and downloads each track to the corresponding niche's
``assets/music_beds/<mood>/*.mp3`` directory. Already-present files are
skipped, so re-running is safe.

Manifest is versioned at ``scripts/seed/pixabay_music_manifest.json``.
When the tracks feel stale (typically 3-6 months), re-generate the
manifest per ``scripts/seed/README.md`` and re-run this script.

Usage
-----

::

    # Default — reads scripts/seed/pixabay_music_manifest.json, downloads
    # to /opt/genlab/<niche>/assets/music_beds/<mood>/*.mp3
    uv run python3 scripts/seed_pixabay_music.py

    # Dry-run — print what would be downloaded, don't touch disk
    uv run python3 scripts/seed_pixabay_music.py --dry-run

    # Custom manifest (e.g. testing an updated version)
    uv run python3 scripts/seed_pixabay_music.py --manifest /tmp/new.json

    # Custom project root (e.g. dev machine)
    uv run python3 scripts/seed_pixabay_music.py --project-root ~/GenLab

Exit codes
----------

* ``0`` — every file either already exists or downloaded successfully
* ``1`` — one or more downloads failed (transient CDN error or link rot)
* ``2`` — argparse error
* ``3`` — manifest file missing or malformed

Design notes
------------

Uses stdlib ``urllib`` — this script may run on the prod VPS during a
deploy freeze when the ``.venv`` dep-lock is being rolled back. No third-
party deps means the script keeps working regardless of environment
state.

Cloudflare fronts ``pixabay.com`` (search + track detail pages), but
NOT ``cdn.pixabay.com`` (the download endpoint). The manifest sidesteps
Cloudflare entirely because we grabbed CDN URLs during manifest
generation. Direct downloads work with any User-Agent.

Skip logic keys off filename — same track_id → same filename → skipped
on re-run. That's why the filename embeds the numeric ID.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# niche_id → filesystem path under project root. Matches the layout the
# AudioReplacer reads (``<niche_root>/assets/music_beds/<mood>/*.mp3``).
NICHE_ROOTS: dict[str, str] = {
    "ai_creators": "BlackboxBrief",
    "gaming": "CriticalRush/niches/gaming",
    "sports": "ClutchWire",
    "movies": "SpliceReel",
    # anime deliberately absent — FrameDrift is not in the activated
    # 4-niche set as of 2026-07-05. If it later activates, add its
    # entry and add ``anime`` moods to the manifest.
}

_SLUG_RE = re.compile(r"[^a-z0-9_]+")
_USER_AGENT = "Mozilla/5.0 GenLab-seed"


def _slug(title: str) -> str:
    """Filesystem-safe slug from a track title."""
    slug = _SLUG_RE.sub("", title.lower().replace(" ", "_"))
    return slug[:40] or "track"


def _target_path(project_root: Path, niche: str, mood: str, track: dict) -> Path:
    return (
        project_root
        / NICHE_ROOTS[niche]
        / "assets"
        / "music_beds"
        / mood
        / f"{_slug(track['title'])}_{track['id']}.mp3"
    )


def _download(url: str, path: Path, *, timeout: int = 60) -> None:
    """Fetch ``url`` to ``path`` using stdlib. Raises URLError on failure."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        # Write to a .part sibling first, then rename — cheap crash-safety.
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(resp.read())
        tmp.rename(path)


def seed(
    manifest: dict,
    project_root: Path,
    *,
    dry_run: bool = False,
    sleep_between: float = 0.3,
) -> tuple[int, int, int]:
    """Download every track in the manifest. Returns (downloaded,
    skipped, failed) counts."""
    downloaded = skipped = failed = 0
    for niche, moods in manifest.items():
        if niche not in NICHE_ROOTS:
            print(f"  ⚠️  skipping unknown niche {niche!r} (add to NICHE_ROOTS)")
            continue
        for mood, tracks in moods.items():
            print(f"== {niche}/{mood} ==")
            for track in tracks:
                target = _target_path(project_root, niche, mood, track)
                if target.is_file():
                    print(f"  SKIP (exists) {target.name}")
                    skipped += 1
                    continue
                if dry_run:
                    print(f"  DRY  {target.name} <- {track['cdn_url']}")
                    downloaded += 1
                    continue
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _download(track["cdn_url"], target)
                    size = target.stat().st_size
                    print(f"  OK   {target.name} ({size:,} bytes)")
                    downloaded += 1
                    time.sleep(sleep_between)
                except urllib.error.URLError as exc:
                    print(f"  FAIL {target.name}: {exc}")
                    failed += 1
    return downloaded, skipped, failed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).parent / "seed" / "pixabay_music_manifest.json",
        help="Path to the manifest JSON (default: scripts/seed/pixabay_music_manifest.json)",
    )
    ap.add_argument(
        "--project-root",
        type=Path,
        default=Path("/opt/genlab"),
        help="Project root to resolve niche dirs against (default: /opt/genlab)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be downloaded, do not touch disk",
    )
    args = ap.parse_args()

    if not args.manifest.is_file():
        print(f"Manifest not found: {args.manifest}", file=sys.stderr)
        return 3
    try:
        manifest = json.loads(args.manifest.read_text())
    except json.JSONDecodeError as exc:
        print(f"Manifest is not valid JSON: {exc}", file=sys.stderr)
        return 3

    print(f"Seeding from {args.manifest.name} into {args.project_root}\n")
    downloaded, skipped, failed = seed(
        manifest, args.project_root, dry_run=args.dry_run
    )

    print()
    print("==== SEED SUMMARY ====")
    print(f"  Downloaded: {downloaded}")
    print(f"  Skipped (already present): {skipped}")
    print(f"  Failed: {failed}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
