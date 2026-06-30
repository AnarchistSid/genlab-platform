"""StrategistReport persisters.

Two implementations:
1. JsonlPersister — writes one JSON line per report to a file. Used for
   PR Strategist-1 to defer the alembic migration risk; reports are still
   durable + grep-able + recoverable.
2. PostgresPersister — placeholder shipping in PR Strategist-1b (a small
   follow-up commit) after alembic head state is verified.

Both implement the ReportPersister Protocol from strategist.py.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from genlab_core.intelligence.proposal_schema import StrategistReport

logger = logging.getLogger(__name__)


class JsonlPersister:
    """Append each report as a JSON line to a file. Thread-safe.

    Path convention: `/opt/genlab/.tmp/strategist_reports/{niche_id}.jsonl`
    Each line is a complete StrategistReport.model_dump_json() output;
    operator dashboard (PR Strategist-2) reads + sorts by run_at to surface
    the latest.
    """

    def __init__(self, base_dir: str | Path):
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def persist(self, report: StrategistReport) -> None:
        """Append the report as one JSON line. Atomic per-line write."""
        path = self._base / f"{report.niche_id}.jsonl"
        line = report.model_dump_json() + "\n"
        with self._lock, path.open("a", encoding="utf-8") as f:
            f.write(line)
        logger.info(
            "strategist.persisted niche=%s week_of=%s path=%s",
            report.niche_id,
            report.week_of,
            path,
        )

    def list_reports(self, niche_id: str) -> list[StrategistReport]:
        """Read all reports for a niche, newest first. Returns [] if file missing."""
        path = self._base / f"{niche_id}.jsonl"
        if not path.exists():
            return []
        reports = []
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    reports.append(StrategistReport.model_validate_json(line))
                except Exception as exc:
                    logger.warning("persister.skip_malformed_line err=%s line=%s", exc, line[:80])
        return sorted(reports, key=lambda r: r.run_at, reverse=True)
