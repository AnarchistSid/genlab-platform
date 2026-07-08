"""Filesystem-report loaders shared by multiple health-monitor check modules.

Extracted from ``health_monitor.py`` (2026-07-08 god-module split, DEV-1)
because ``_load_recent_reports``, ``_load_clip_index``, and
``_load_recent_metrics`` are called from more than one ``checks/*.py``
submodule. Keeping them here (vs any single check file) avoids a
sibling-import cycle.

Also holds the ``RUNS_DIR`` + ``NICHES`` module-level constants — these
belong with the report loaders because that's the primary read path.

**Patch surface**: ``genlab_core.monitoring.health_monitor.RUNS_DIR`` is
patched by ``test_fetcher_silent_failure_check.py``. Consumers therefore
resolve ``RUNS_DIR`` through the facade module (see the check modules'
``_facade.RUNS_DIR`` usage) so the monkeypatch takes effect.
"""

from __future__ import annotations

import json
import os
import pathlib
from datetime import UTC, datetime, timedelta

RUNS_DIR = pathlib.Path(os.environ.get("GENLAB_PROJECT_ROOT", "/opt/genlab")) / ".tmp" / "runs"
NICHES = ["ai_creators", "gaming", "sports", "movies", "anime"]


def _load_recent_reports(niche_id: str, days: int = 3) -> list[dict]:
    """Load run_report.json files for a niche from the last N days."""
    from genlab_core.monitoring import health_monitor as _facade

    prefix = f"{niche_id}_"
    cutoff = datetime.now(UTC) - timedelta(days=days)
    reports = []
    runs_dir = _facade.RUNS_DIR
    if not runs_dir.exists():
        return reports
    for d in sorted(runs_dir.iterdir(), reverse=True):
        if not d.name.startswith(prefix) or not d.is_dir():
            continue
        report = d / "run_report.json"
        if not report.exists():
            continue
        try:
            data = json.loads(report.read_text())
            ts = datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00"))
            if ts < cutoff:
                break
            data["_run_dir"] = str(d)
            reports.append(data)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return reports


def _load_clip_index(run_dir: str) -> dict:
    """Load clip_index.json from a run directory."""
    ci = pathlib.Path(run_dir) / "clip_index.json"
    if ci.exists():
        try:
            return json.loads(ci.read_text())
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


def _load_recent_metrics(niche_id: str, max_runs: int = 10) -> list[tuple[str, list[dict]]]:
    """Load metrics.jsonl files for the most-recent N runs of a niche.

    Returns a list of ``(run_dir_name, entries)`` tuples ordered from
    NEWEST to OLDEST. Each ``entries`` list contains the parsed JSONL
    lines from that run's ``metrics.jsonl`` (the pipeline summary
    line with ``_type=pipeline_summary`` is filtered OUT — we only
    care about per-stage measurements here).

    Returns an empty list if RUNS_DIR doesn't exist or no runs match.
    """
    from genlab_core.monitoring import health_monitor as _facade

    runs_dir = _facade.RUNS_DIR
    if not runs_dir.exists():
        return []
    prefix = f"{niche_id}_"
    out: list[tuple[str, list[dict]]] = []
    for d in sorted(runs_dir.iterdir(), reverse=True):
        if not d.name.startswith(prefix) or not d.is_dir():
            continue
        mpath = d / "metrics.jsonl"
        if not mpath.exists():
            continue
        entries: list[dict] = []
        try:
            for line in mpath.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # Skip the pipeline summary footer line
                if obj.get("_type") == "pipeline_summary":
                    continue
                entries.append(obj)
        except (OSError, ValueError):
            continue
        out.append((d.name, entries))
        if len(out) >= max_runs:
            break
    return out
