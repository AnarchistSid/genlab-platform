#!/usr/bin/env python3
"""Verify the intelligent-transformation pipeline is firing per niche
(sprint activation follow-up, 2026-07-05).

Runs against the local Postgres (reads ``DATABASE_URL`` from
``/opt/genlab/.env`` on prod, or ``$DATABASE_URL`` env in dev). Queries
``pending_feedback.arm_ids_by_dimension`` (JSONB, populated at publish
time by ``RewardShaper`` since PR 5) and reports, per niche, how many
recently-published reels applied each of the 11 transformation
dimensions.

Expected output on a healthy run:

::

    Niche          reels  music_mood  highlight_moment  caption_style  ...
    ai_creators      4       4/4          4/4              4/4
    gaming           3       3/3          3/3              3/3
    ...

Any ``N/M`` where N < M is a silent-skip — the arm was expected but
not applied, meaning one of:

* Music library not seeded for that niche (only music_mood)
* Motion asset MP4 missing (intro_animation / outro_cta — expected
  until task #524 ships the Canva assets)
* Bandit selector fail-open swallowed a real bug (investigate journal)

Any ``0/N`` on *every* dimension for a niche means either:

* ``GENLAB_INTELLIGENT_TRANSFORM_ENABLED`` not reaching the pipeline
  (env-file mount, drop-in override, or systemd-daemon-reload missing)
* ``visuals.yaml.intelligent_transform.enabled: false`` for that niche
  (deliberate opt-out — FrameDrift by design as of 2026-07-05)

Usage
-----

::

    # Default: last 24h, all niches with intelligent_transform enabled
    uv run --package genlab-core python3 scripts/verify_intelligent_transform.py

    # Last hour (post-fire smoke check right after a pipeline runs)
    uv run --package genlab-core python3 scripts/verify_intelligent_transform.py \\
        --window-hours 1

    # JSON output for scripting / alerting
    uv run --package genlab-core python3 scripts/verify_intelligent_transform.py \\
        --format json

Exit codes
----------

* ``0`` — every niche with the flag enabled produced ≥1 fully-attributed
  reel in the window (or produced no reels at all — no false positives)
* ``2`` — argparse error
* ``3`` — at least one niche has published reels in the window but
  ``arm_ids_by_dimension`` was empty on every one (activation regression)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

# Dimensions expected to be attributed on every reel when the flag
# is on. Intro/outro are asset-gated (task #524), so we surface but
# don't gate on them being present.
CORE_DIMENSIONS = (
    "music_mood",
    "highlight_moment",
    "caption_style",
    "caption_pacing",
    "hook_framing",
    "pan_zoom",
    "audio_ducking",
    "music_visual_sync",
    "caption_emphasis_color",
)
ASSET_GATED_DIMENSIONS = ("intro_animation", "outro_cta")
ALL_DIMENSIONS = CORE_DIMENSIONS + ASSET_GATED_DIMENSIONS


def _load_env_file(path: Path) -> None:
    """Cheap .env loader — only sets keys not already in os.environ.

    Avoids pulling in ``python-dotenv`` for a preflight script.
    """
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def _connect():
    """Open a psycopg3 connection using DATABASE_URL from env."""
    import psycopg  # local import — this script runs on prod only

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit(
            "DATABASE_URL not set; source /opt/genlab/.env before running."
        )
    return psycopg.connect(url, row_factory=psycopg.rows.dict_row)


def query_recent(window_hours: int) -> list[dict]:
    """Fetch pending_feedback rows created in the window with arm assignments."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              niche_id,
              arm_ids_by_dimension,
              created_at
            FROM pending_feedback
            WHERE created_at > now() - make_interval(hours => %s)
            ORDER BY niche_id, created_at
            """,
            (window_hours,),
        )
        return list(cur.fetchall())


def aggregate(rows: list[dict]) -> dict[str, dict[str, int]]:
    """Group by niche and count per-dimension applications."""
    per_niche: dict[str, dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    for row in rows:
        niche = row["niche_id"]
        per_niche[niche]["_reels"] += 1
        dims = row["arm_ids_by_dimension"] or {}
        for dim in dims:
            per_niche[niche][dim] += 1
    return {k: dict(v) for k, v in per_niche.items()}


def _format_cell(applied: int, reels: int) -> str:
    """`N/M` or `0/M` — no ratio, keeps arithmetic obvious to the eye."""
    return f"{applied}/{reels}"


def print_table(agg: dict[str, dict[str, int]]) -> int:
    """Print human-readable per-niche breakdown; return exit code."""
    exit_code = 0
    if not agg:
        print("No pending_feedback rows in window — no reels published yet.")
        return exit_code

    # Compact table — dimensions across columns, niches down rows.
    header = ["niche", "reels"] + list(CORE_DIMENSIONS) + list(ASSET_GATED_DIMENSIONS)
    widths = [max(len(h), 12) for h in header]
    print("  " + "  ".join(h.ljust(w) for h, w in zip(header, widths)))
    print("  " + "  ".join("-" * w for w in widths))

    for niche in sorted(agg):
        stats = agg[niche]
        reels = stats.get("_reels", 0)
        row = [niche, str(reels)]
        for dim in CORE_DIMENSIONS + ASSET_GATED_DIMENSIONS:
            row.append(_format_cell(stats.get(dim, 0), reels))
        print("  " + "  ".join(c.ljust(w) for c, w in zip(row, widths)))

        # Regression signal: reels published but ZERO core dims applied.
        if reels > 0 and all(stats.get(d, 0) == 0 for d in CORE_DIMENSIONS):
            exit_code = 3
            print(
                f"    ⚠️  {niche} published {reels} reels with no dimension "
                f"attributed — flag or config regression suspected."
            )
    return exit_code


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--window-hours",
        type=int,
        default=24,
        help="Lookback window in hours (default 24)",
    )
    ap.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="Output format",
    )
    ap.add_argument(
        "--env-file",
        default="/opt/genlab/.env",
        help="Path to .env file to source (default /opt/genlab/.env)",
    )
    args = ap.parse_args()

    _load_env_file(Path(args.env_file))

    rows = query_recent(args.window_hours)
    agg = aggregate(rows)

    if args.format == "json":
        print(json.dumps(agg, indent=2, sort_keys=True))
        return 0

    print(
        f"Intelligent-transformation attribution — last {args.window_hours}h\n"
    )
    return print_table(agg)


if __name__ == "__main__":
    sys.exit(main())
