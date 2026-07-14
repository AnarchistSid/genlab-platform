"""StrategistReport persisters.

Two implementations:
1. JsonlPersister — writes one JSON line per report to a file. Useful for
   local dev / offline replay; reports are durable, grep-able, recoverable.
2. PostgresPersister — writes to the `strategist_reports` table created
   in migration y6t7u8v9w0x1. Production default.

Both implement the ReportPersister Protocol from strategist.py.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

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

    def persist(
        self,
        report: StrategistReport,
        *,
        cost_usd: float | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        """Append the report as one JSON line. Atomic per-line write.

        Cost telemetry kwargs are accepted for Protocol conformance but
        ignored — JsonlPersister writes the report JSON only. Operators
        who need cost telemetry from JSONL should use PostgresPersister
        (which writes cost_usd/input_tokens/output_tokens columns) or
        embed the CallResult in a wrapper JSON above this layer.
        """
        del cost_usd, input_tokens, output_tokens  # Protocol conformance
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


class PostgresPersister:
    """Persist StrategistReports to the strategist_reports table.

    Pass a psycopg connection (real or mock). The persister does not own
    the connection lifecycle — caller manages connect/close. This makes
    testing simple (inject mock) and matches the pattern used elsewhere
    in genlab-core (see source_performance.py).

    Idempotency: ON CONFLICT (niche_id, week_of) DO UPDATE — re-running
    Strategist for the same week overwrites the previous report. The
    `week_unique_per_niche` UNIQUE constraint from the migration enforces
    this at the DB level.
    """

    UPSERT_SQL = """
        INSERT INTO strategist_reports (
          id, niche_id, week_of, run_at,
          inputs_json, detected_phase, phase_evidence,
          proposals, causal_hypotheses, universal_playbook_proposals,
          weekly_summary, cost_usd, input_tokens, output_tokens
        ) VALUES (
          %(id)s, %(niche_id)s, %(week_of)s, %(run_at)s,
          %(inputs_json)s, %(detected_phase)s, %(phase_evidence)s,
          %(proposals)s, %(causal_hypotheses)s, %(universal_playbook_proposals)s,
          %(weekly_summary)s, %(cost_usd)s, %(input_tokens)s, %(output_tokens)s
        )
        ON CONFLICT (niche_id, week_of) DO UPDATE SET
          run_at = EXCLUDED.run_at,
          inputs_json = EXCLUDED.inputs_json,
          detected_phase = EXCLUDED.detected_phase,
          phase_evidence = EXCLUDED.phase_evidence,
          proposals = EXCLUDED.proposals,
          causal_hypotheses = EXCLUDED.causal_hypotheses,
          universal_playbook_proposals = EXCLUDED.universal_playbook_proposals,
          weekly_summary = EXCLUDED.weekly_summary,
          cost_usd = EXCLUDED.cost_usd,
          input_tokens = EXCLUDED.input_tokens,
          output_tokens = EXCLUDED.output_tokens
    """

    def __init__(self, conn, inputs_snapshot: dict[str, Any] | None = None):
        self._conn = conn
        # The Strategist passes its collected state via the constructor or per-call;
        # PR Strategist-2 will refactor to pass via persist() args directly. For
        # PR Strategist-1b this stays simple — inputs default to empty dict.
        self._inputs = inputs_snapshot or {}

    def persist(
        self,
        report: StrategistReport,
        *,
        cost_usd: float | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        """UPSERT the report + cost telemetry.

        2026-07-14: cost/tokens kwargs are now wired from
        ``Strategist.run_for_niche`` via ``call_result``. Prior to
        this fix the persister hardcoded them to None with a "PR
        Strategist-2 will wire that" TODO that shipped 3 months ago
        and stayed unresolved — every strategist_reports row from
        2026-04 through 2026-07-12 has NULL cost/tokens columns.
        """
        params = {
            "id": str(report.id),
            "niche_id": report.niche_id,
            "week_of": report.week_of,
            "run_at": report.run_at,
            "inputs_json": json.dumps(self._inputs),
            "detected_phase": report.detected_phase.value,
            "phase_evidence": report.phase_evidence,
            "proposals": report.model_dump_json(include={"proposals"}),
            "causal_hypotheses": report.model_dump_json(include={"causal_hypotheses"}),
            "universal_playbook_proposals": report.model_dump_json(
                include={"universal_playbook_proposals"}
            ),
            "weekly_summary": report.weekly_summary,
            "cost_usd": cost_usd,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
        # Extract the inner arrays from the JSON dumps — model_dump_json with
        # include= returns the wrapping object; we want the inner list as JSONB.
        params["proposals"] = json.dumps([p.model_dump(mode="json") for p in report.proposals])
        params["causal_hypotheses"] = json.dumps(
            [h.model_dump(mode="json") for h in report.causal_hypotheses]
        )
        params["universal_playbook_proposals"] = json.dumps(
            [p.model_dump(mode="json") for p in report.universal_playbook_proposals]
        )
        self._conn.execute(self.UPSERT_SQL, params)
        self._conn.commit()
        logger.info(
            "strategist.persisted_pg niche=%s week_of=%s id=%s",
            report.niche_id,
            report.week_of,
            report.id,
        )

    def get_latest(self, niche_id: str) -> StrategistReport | None:
        """Fetch the most recent report for a niche, or None if no rows."""
        cur = self._conn.execute(
            """
            SELECT id, niche_id, week_of, run_at, detected_phase, phase_evidence,
                   proposals, causal_hypotheses, universal_playbook_proposals,
                   weekly_summary
            FROM strategist_reports
            WHERE niche_id = %s
            ORDER BY run_at DESC
            LIMIT 1
            """,
            (niche_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return self._row_to_report(row)

    def list_unreviewed(self, limit: int = 20) -> list[StrategistReport]:
        """Reports the operator hasn't reviewed yet, newest first. Used by
        the dashboard banner (PR Strategist-2) to surface pending work."""
        cur = self._conn.execute(
            """
            SELECT id, niche_id, week_of, run_at, detected_phase, phase_evidence,
                   proposals, causal_hypotheses, universal_playbook_proposals,
                   weekly_summary
            FROM strategist_reports
            WHERE reviewed_at IS NULL
            ORDER BY run_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        return [self._row_to_report(r) for r in cur.fetchall()]

    @staticmethod
    def _row_to_report(row) -> StrategistReport:
        """Hydrate a DB row back into a StrategistReport. Tolerant of dict-row
        OR positional-row factories — works with either psycopg row_factory."""
        get = (lambda k, i: row.get(k)) if hasattr(row, "get") else (lambda k, i: row[i])
        return StrategistReport(
            id=get("id", 0),
            niche_id=get("niche_id", 1),
            week_of=get("week_of", 2),
            run_at=get("run_at", 3),
            detected_phase=get("detected_phase", 4),
            phase_evidence=get("phase_evidence", 5),
            proposals=get("proposals", 6) or [],
            causal_hypotheses=get("causal_hypotheses", 7) or [],
            universal_playbook_proposals=get("universal_playbook_proposals", 8) or [],
            weekly_summary=get("weekly_summary", 9),
        )
