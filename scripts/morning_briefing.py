#!/usr/bin/env python3
"""
Gen Lab — Morning Briefing
Unified daily report: pipeline health + all intelligence systems.
Reads from intelligence_hub to surface trends, viral alerts, content memory,
posting schedule, and engagement baselines alongside token/disk/run health.

Run manually or via launchd (com.genlab.morning-briefing).

Usage:
    uv run --package genlab-core python3 scripts/morning_briefing.py
    uv run --package genlab-core python3 scripts/morning_briefing.py --json
    uv run --package genlab-core python3 scripts/morning_briefing.py --niche gaming
"""
import json
import logging
import os
import pathlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Run via: uv run --package genlab-core python scripts/morning_briefing.py

_ROOT = Path(__file__).resolve().parent.parent
from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")

import requests

logger = logging.getLogger("morning_briefing")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

BRIEFING_FILE = pathlib.Path.home() / ".genlab" / "morning_briefing.json"
HEALTH_FILE = pathlib.Path.home() / ".genlab" / "token_health.json"


# ── Infrastructure Checks ──────────────────────────────────────────

def check_token_health() -> dict[str, Any]:
    if HEALTH_FILE.exists():
        try:
            return json.loads(HEALTH_FILE.read_text())
        except json.JSONDecodeError:
            return {"error": "corrupt_health_file"}
    return {"error": "no_health_file"}


def check_recent_runs() -> dict[str, Any]:
    runs_dir = _ROOT / "BlackboxBrief" / ".tmp" / "runs"
    if not runs_dir.exists():
        return {"error": "no_runs_directory"}

    def _safe_mtime(p):
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    run_dirs = sorted(runs_dir.iterdir(), key=_safe_mtime, reverse=True)
    recent = []
    for d in run_dirs[:5]:
        if not d.is_dir():
            continue
        status_file = d / "status.json"
        if status_file.exists():
            try:
                status = json.loads(status_file.read_text())
                recent.append({
                    "run_id": d.name,
                    "status": status.get("status", "unknown"),
                    "started": status.get("started_at"),
                    "stories": status.get("stories_processed", 0),
                    "published": status.get("published", 0),
                })
            except (json.JSONDecodeError, KeyError):
                recent.append({"run_id": d.name, "status": "parse_error"})
        else:
            logs = list(d.glob("*.log"))
            recent.append({
                "run_id": d.name,
                "status": "no_status_file",
                "has_logs": len(logs) > 0,
                "modified": datetime.fromtimestamp(d.stat().st_mtime, tz=UTC).isoformat(),
            })
    return {"recent_runs": recent, "total_run_dirs": len(run_dirs)}


def check_criticalrush_runs() -> dict[str, Any]:
    logs_dir = _ROOT / "CriticalRush" / ".tmp" / "logs"
    rendered_dir = _ROOT / "CriticalRush" / ".tmp" / "rendered"
    result = {}
    if logs_dir.exists():
        log_files = sorted(logs_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if log_files:
            latest = log_files[0]
            result["latest_log"] = latest.name
            result["latest_log_time"] = datetime.fromtimestamp(
                latest.stat().st_mtime, tz=UTC
            ).isoformat()
    if rendered_dir.exists():
        rendered = list(rendered_dir.glob("*.mp4"))
        result["rendered_videos"] = len(rendered)
        if rendered:
            latest = max(rendered, key=lambda p: p.stat().st_mtime)
            result["latest_render"] = latest.name
    return result


def check_disk_usage() -> dict[str, Any]:
    import shutil
    total, used, free = shutil.disk_usage("/")
    paths = {
        "GenLab": str(_ROOT),
        "CS .tmp": str(_ROOT / "BlackboxBrief" / ".tmp"),
        "CR .tmp": str(_ROOT / "CriticalRush" / ".tmp"),
    }
    sizes = {}
    for label, path in paths.items():
        if os.path.exists(path):
            size = sum(f.stat().st_size for f in pathlib.Path(path).rglob("*") if f.is_file())
            sizes[label] = f"{size / (1024**3):.1f}GB"
    return {
        "disk_free": f"{free / (1024**3):.0f}GB",
        "disk_used_pct": f"{(used / total) * 100:.0f}%",
        "project_sizes": sizes,
    }


def check_youtube_quota() -> dict[str, Any]:
    client_id = os.getenv("YOUTUBE_CLIENT_ID")
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET")
    refresh_token = os.getenv("YOUTUBE_REFRESH_TOKEN")
    if not all([client_id, client_secret, refresh_token]):
        return {"status": "no_credentials"}
    try:
        resp = requests.post("https://oauth2.googleapis.com/token", data={
            "client_id": client_id, "client_secret": client_secret,
            "refresh_token": refresh_token, "grant_type": "refresh_token",
        }, timeout=10)
        if not resp.ok:
            return {"status": "token_refresh_failed"}
        token = resp.json().get("access_token")
        resp2 = requests.get(
            "https://www.googleapis.com/youtube/v3/channels",
            params={"part": "id", "mine": "true"},
            headers={"Authorization": f"Bearer {token}"}, timeout=10,
        )
        if resp2.ok:
            return {"status": "ok"}
        if "quotaExceeded" in resp2.text or "uploadLimitExceeded" in resp2.text:
            return {"status": "quota_exceeded"}
        return {"status": f"error_{resp2.status_code}"}
    except Exception as e:
        return {"status": f"error: {str(e)[:80]}"}


# ── Build Briefing ──────────────────────────────────────────────────

def build_briefing(niche_id: str | None = None) -> dict[str, Any]:
    from intelligence_hub import (
        get_all_optimal_times,
        get_baselines,
        get_cached_trends,
        get_memory_stats,
        get_recent_viral_alerts,
    )

    # Infrastructure
    briefing = {
        "generated_at": datetime.now(UTC).isoformat(),
        "niche_id": niche_id,
        "token_health": check_token_health(),
        "content_scraper_runs": check_recent_runs(),
        "criticalrush": check_criticalrush_runs(),
        "youtube_quota": check_youtube_quota(),
        "disk": check_disk_usage(),
    }

    # Intelligence systems
    briefing["intelligence"] = {
        "content_memory": get_memory_stats(niche_id),
        "viral_alerts": get_recent_viral_alerts(),
        "trends": get_cached_trends(niche_id),
        "optimal_times": get_all_optimal_times(),
        "baselines": get_baselines(),
    }

    # Issues detection
    issues = []
    th = briefing["token_health"]
    if isinstance(th, dict):
        for platform, status in th.items():
            if isinstance(status, dict) and status.get("status") == "expired":
                issues.append(f"TOKEN EXPIRED: {platform}")
            elif isinstance(status, dict) and status.get("status") == "expiring_soon":
                issues.append(f"TOKEN EXPIRING: {platform}")

    yt = briefing["youtube_quota"]
    if yt.get("status") == "quota_exceeded":
        issues.append("YouTube API quota exceeded")

    # Intelligence warnings
    intel = briefing["intelligence"]
    mem = intel["content_memory"]
    if mem.get("total_posts", 0) == 0:
        issues.append("Content memory empty — run --ingest")
    if not intel["trends"]:
        issues.append("No trend data — run trend_signals.py --full")
    if not intel["baselines"]:
        issues.append("No engagement baselines — run viral_detector.py --baseline")
    if not any(t for t in intel["optimal_times"].values() if t):
        issues.append("No posting schedule — run posting_optimizer.py")

    viral = intel["viral_alerts"]
    if viral:
        issues.append(f"{len(viral)} viral/trending alert(s)")

    briefing["issues"] = issues
    briefing["issue_count"] = len(issues)

    return briefing


# ── Print ───────────────────────────────────────────────────────────

def print_briefing(briefing: dict[str, Any]) -> None:
    from intelligence_hub import KNOWN_NICHES

    print(f"\n{'=' * 64}")
    print("  Gen Lab — Morning Briefing")
    niche = briefing.get("niche_id")
    if niche:
        brand = KNOWN_NICHES.get(niche, {}).get("brand", niche)
        print(f"  Channel: {brand} ({niche})")
    print(f"  {briefing['generated_at'][:19]}Z")
    print(f"{'=' * 64}")

    # Issues
    issues = briefing.get("issues", [])
    if issues:
        print(f"\n  ISSUES ({len(issues)})")
        for issue in issues:
            print(f"    ! {issue}")
    else:
        print("\n  No issues detected")

    # Infrastructure
    yt = briefing.get("youtube_quota", {})
    disk = briefing.get("disk", {})
    print("\n  INFRASTRUCTURE")
    print(f"    YouTube API:  {yt.get('status', '?')}")
    print(f"    Disk free:    {disk.get('disk_free', '?')} ({disk.get('disk_used_pct', '?')} used)")
    for label, size in disk.get("project_sizes", {}).items():
        print(f"      {label}: {size}")

    # Pipeline runs
    cs = briefing.get("content_scraper_runs", {})
    runs = cs.get("recent_runs", [])
    if runs:
        print(f"\n  PIPELINE RUNS (last {len(runs)})")
        for r in runs[:3]:
            print(f"    {r.get('run_id', '?')[:30]}  [{r.get('status', '?')}]")

    cr = briefing.get("criticalrush", {})
    if cr:
        print("\n  CRITICALRUSH")
        if cr.get("latest_render"):
            print(f"    Latest: {cr['latest_render']}")
        print(f"    Videos: {cr.get('rendered_videos', 0)}")

    # Intelligence
    intel = briefing.get("intelligence", {})

    mem = intel.get("content_memory", {})
    print("\n  CONTENT MEMORY")
    print(f"    Posts:       {mem.get('total_posts', 0)}")
    print(f"    Viral:       {mem.get('viral_posts', 0)}")
    print(f"    Avg engage:  {mem.get('avg_engagement', 0):.0f}")
    if mem.get("by_platform"):
        for p, c in mem["by_platform"].items():
            print(f"      {p:14s} {c}")

    viral = intel.get("viral_alerts", [])
    if viral:
        print(f"\n  VIRAL ALERTS ({len(viral)})")
        for a in viral[-3:]:
            print(f"    {a.get('level', '?'):8s} [{a.get('platform', '?').upper():10s}] "
                  f"{a.get('multiplier', 0)}x — {a.get('text', '')[:45]}")

    trends = intel.get("trends", {})
    daily = trends.get("daily_trending", [])
    if daily:
        print("\n  TRENDING NOW")
        for t in daily[:5]:
            print(f"    - {t.get('query', '?')}")
    niche_trends = trends.get("niche", {})
    hot = niche_trends.get("hot_topics", [])
    if hot:
        print("\n  NICHE TRENDS")
        for t in hot[:3]:
            arrow = {"rising": "^", "falling": "v", "stable": "="}.get(t.get("direction", ""), "?")
            print(f"    {arrow} {t.get('keyword', '?'):20s} score: {t.get('score', 0):.0f}")

    times = intel.get("optimal_times", {})
    active = {p: t for p, t in times.items() if t}
    if active:
        print("\n  POSTING SCHEDULE (IST)")
        for p, t in active.items():
            print(f"    {p:14s} {t}")

    baselines = intel.get("baselines", {})
    if baselines:
        print("\n  ENGAGEMENT BASELINES")
        for p, b in baselines.items():
            print(f"    {p:14s} {b:>8.0f}")

    print(f"\n{'=' * 64}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Morning Briefing")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--niche", type=str, help="Filter by niche_id")
    args = parser.parse_args()

    briefing = build_briefing(args.niche)

    BRIEFING_FILE.parent.mkdir(parents=True, exist_ok=True)
    BRIEFING_FILE.write_text(json.dumps(briefing, indent=2, default=str))

    if args.json:
        print(json.dumps(briefing, indent=2, default=str))
    else:
        print_briefing(briefing)
        print(f"\n  Saved to: {BRIEFING_FILE}")
