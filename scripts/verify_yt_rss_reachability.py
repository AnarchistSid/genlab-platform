#!/usr/bin/env python3
"""Verify YouTube RSS channel URLs in all niche sources.yaml files.

Task #626 (2026-07-09) — roadmap E6. Health check for the YouTube
RSS feeds that seed BlackboxBrief (and any future niche that adds
``youtube_channels:`` blocks to its ``config/sources.yaml``).

## Why this exists

RSS URLs silently degrade — a channel gets renamed, the ID changes,
YouTube changes RSS availability policies, etc. Without a periodic
check, we discover the failure only when the downstream fetcher
returns fewer stories than expected. This script surfaces breakage
early.

## What it checks

Walks ``**/config/sources.yaml`` for any entries under
``youtube_channels:`` and issues a ``HEAD`` on each URL. Exits
non-zero if any URL returns non-200 or times out.

## Usage

```bash
uv run python scripts/verify_yt_rss_reachability.py
uv run python scripts/verify_yt_rss_reachability.py --niche BlackboxBrief
```

Recommended cadence: weekly systemd timer, or manually before adding
new channels to config. Not currently wired to a timer — E6 covered
only the one-off audit; timer wiring is a follow-up.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TIMEOUT_S = 10
URL_RE = re.compile(r"https://www\.youtube\.com/feeds/videos\.xml\?channel_id=[A-Za-z0-9_-]+")
NAME_RE = re.compile(r'name:\s*"([^"]+)"')


def find_config_files(niche: str | None = None) -> list[Path]:
    """Return the sources.yaml files to check.

    Filters by niche directory name when provided; otherwise walks
    the repo for any ``**/config/sources.yaml``.
    """
    all_files = sorted(REPO_ROOT.glob("*/config/sources.yaml"))
    # Filter out .venv / node_modules / dot-dirs.
    files = [f for f in all_files if not any(p.startswith(".") for p in f.parts)]
    if niche:
        files = [f for f in files if f.parts[-3] == niche]
    return files


def check_url(url: str) -> tuple[int, str]:
    """HEAD ``url`` and return (status_code, error_msg).

    Falls back to GET if HEAD returns 405 — YouTube's RSS endpoint
    historically accepted HEAD but any future block-on-method change
    would break the audit; falling through to GET keeps the check
    robust to that.
    """
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return resp.status, ""
    except urllib.error.HTTPError as e:
        if e.code == 405:
            # Retry with GET on 405 method-not-allowed.
            try:
                with urllib.request.urlopen(url, timeout=TIMEOUT_S) as resp:
                    return resp.status, ""
            except Exception as get_e:
                return 0, f"GET fallback failed: {get_e!s}"
        return e.code, str(e)
    except Exception as e:
        return 0, str(e)


def extract_channels(path: Path) -> list[tuple[str, str]]:
    """Return list of (url, display_name) tuples from ``path``.

    Naïve line-based scan is enough — sources.yaml is small and the
    YT-RSS URL pattern is unambiguous. Full YAML parse would work
    too but adds a dep and doesn't buy anything here.
    """
    text = path.read_text(encoding="utf-8")
    channels: list[tuple[str, str]] = []
    for match in URL_RE.finditer(text):
        url = match.group(0)
        # Look forward up to 3 lines for the name: attribute.
        after = text[match.end() : match.end() + 200]
        name_match = NAME_RE.search(after)
        name = name_match.group(1) if name_match else "(unnamed)"
        channels.append((url, name))
    return channels


def main() -> int:
    ap = argparse.ArgumentParser(description="Check YT RSS reachability.")
    ap.add_argument(
        "--niche",
        help="Restrict to one niche directory (e.g. BlackboxBrief). Default: all niches.",
    )
    ap.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-URL PASS lines; only print failures + summary.",
    )
    args = ap.parse_args()

    config_files = find_config_files(args.niche)
    if not config_files:
        print("No sources.yaml files found", file=sys.stderr)
        return 2

    total = 0
    failed: list[tuple[str, str, int, str]] = []

    for config in config_files:
        channels = extract_channels(config)
        if not channels:
            continue
        rel = config.relative_to(REPO_ROOT)
        print(f"\n=== {rel} ({len(channels)} channels) ===")
        for url, name in channels:
            total += 1
            status, err = check_url(url)
            if status == 200:
                if not args.quiet:
                    print(f"  PASS  {name}")
            else:
                marker = f"FAIL[{status or '???'}]"
                print(f"  {marker}  {name}  {url}  {err[:80]}")
                failed.append((str(rel), name, status, url))

    print(f"\n--- Summary: {total - len(failed)}/{total} reachable ---")
    if failed:
        print("\nFailures:")
        for rel, name, status, url in failed:
            print(f"  {rel}  {name}  [{status}]  {url}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
