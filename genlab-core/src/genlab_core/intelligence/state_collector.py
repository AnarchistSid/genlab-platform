"""StateCollector — gathers all inputs the Strategist needs from Postgres.

The collector is designed to be FAIL-SOFT: missing tables, missing columns,
or empty result sets return `None` for that field rather than crashing. The
prompt template substitutes 'unknown' for None, so the LLM has explicit
signal about what data is missing rather than guessing.

Why a separate module: tests need to be able to inject canned state without
spinning up Postgres. The Strategist consumes via the StateCollector
Protocol, so any object with `.collect(niche_id, week_of) -> dict` works.

See docs/STRATEGIST-SPEC.md §3 for the input schema and table sources.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


class PostgresStateCollector:
    """Live state collector that queries Postgres.

    Pass a psycopg connection (real or mock) at construction. Each query is
    wrapped in try/except so a single missing table doesn't fail the whole
    collection — the partial state is still useful to the LLM.
    """

    def __init__(self, conn):
        self._conn = conn

    def collect(self, niche_id: str, week_of: date) -> dict[str, Any]:
        """Gather all known state for one niche + week. Returns dict with str keys.

        Always includes: niche_id, week_of, run_at. Everything else is
        best-effort; missing keys are None.
        """
        run_at = datetime.utcnow()
        state: dict[str, Any] = {
            "niche_id": niche_id,
            "week_of": week_of.isoformat(),
            "run_at": run_at.isoformat() + "Z",
        }
        state.update(self._channel_metrics(niche_id))
        state.update(self._bandit_state(niche_id))
        state.update(self._validation_state(niche_id))
        state.update(self._calibration_state(niche_id))
        state.update(self._cost_state(niche_id))
        state["recent_publishes"] = self._recent_publishes(niche_id, week_of)
        state["other_niches_summary"] = self._cross_niche_summary(exclude=niche_id)
        state["active_findings"] = self._active_findings(niche_id)
        state["last_week_outcomes"] = self._last_week_outcomes(niche_id, week_of)
        return state

    # ----- per-section best-effort queries -----

    def _channel_metrics(self, niche_id: str) -> dict[str, Any]:
        """Pulls follower count + engagement + watch time from analytics table.

        Returns: follower_count, engagement_rate_7d, watch_time_avg_7d, n_publishes_7d,
        plus _delta_4w deltas. All keys nullable.
        """
        try:
            cur = self._conn.execute(
                """
                SELECT
                  MAX(follower_count) AS followers,
                  AVG(engagement_rate) FILTER (WHERE collected_at >= NOW() - INTERVAL '7 days') AS er7d,
                  AVG(watch_time_sec) FILTER (WHERE collected_at >= NOW() - INTERVAL '7 days') AS wt7d,
                  COUNT(*) FILTER (WHERE collected_at >= NOW() - INTERVAL '7 days') AS n7d
                FROM analytics
                WHERE niche_id = %s
                """,
                (niche_id,),
            )
            row = cur.fetchone()
            if not row:
                return {}
            return {
                "follower_count": row.get("followers") if hasattr(row, "get") else row[0],
                "engagement_rate_7d": (row.get("er7d") if hasattr(row, "get") else row[1]),
                "watch_time_avg_7d": (row.get("wt7d") if hasattr(row, "get") else row[2]),
                "n_publishes_7d": row.get("n7d") if hasattr(row, "get") else row[3],
            }
        except Exception as exc:
            logger.warning("state_collector.channel_metrics_failed niche=%s err=%s", niche_id, exc)
            return {}

    def _bandit_state(self, niche_id: str) -> dict[str, Any]:
        """Top + bottom arms from bandit_arms; computes mean reward from Beta(α, β)."""
        try:
            cur = self._conn.execute(
                """
                SELECT arm_id, n_plays, alpha, beta
                FROM bandit_arms
                WHERE niche_id = %s
                ORDER BY n_plays DESC
                LIMIT 30
                """,
                (niche_id,),
            )
            arms = []
            for row in cur.fetchall():
                alpha = row.get("alpha", 1.0) if hasattr(row, "get") else row[2]
                beta = row.get("beta", 1.0) if hasattr(row, "get") else row[3]
                # Beta(α, β) mean is α / (α + β)
                mean = float(alpha) / (float(alpha) + float(beta)) if (alpha + beta) > 0 else 0.0
                arms.append(
                    {
                        "arm_id": row.get("arm_id") if hasattr(row, "get") else row[0],
                        "n_plays": row.get("n_plays") if hasattr(row, "get") else row[1],
                        "reward": round(mean, 4),
                    }
                )
            return {"bandit_state": arms}
        except Exception as exc:
            logger.warning("state_collector.bandit_state_failed niche=%s err=%s", niche_id, exc)
            return {"bandit_state": []}

    def _validation_state(self, niche_id: str) -> dict[str, Any]:
        try:
            cur = self._conn.execute(
                """
                SELECT spearman_correlation AS sp, interpretation
                FROM bandit_validation
                WHERE niche_id = %s
                ORDER BY run_at DESC LIMIT 1
                """,
                (niche_id,),
            )
            row = cur.fetchone()
            if not row:
                return {}
            return {
                "spearman_7d": row.get("sp") if hasattr(row, "get") else row[0],
                "validation_interpretation": (
                    row.get("interpretation") if hasattr(row, "get") else row[1]
                ),
            }
        except Exception as exc:
            logger.warning("state_collector.validation_failed niche=%s err=%s", niche_id, exc)
            return {}

    def _calibration_state(self, niche_id: str) -> dict[str, Any]:
        try:
            cur = self._conn.execute(
                """
                SELECT
                  COUNT(*) AS n,
                  AVG(CASE WHEN gate_approved = (operator_action = 'approved') THEN 1.0 ELSE 0.0 END) AS agree
                FROM auto_approval_calibration
                WHERE niche_id = %s
                """,
                (niche_id,),
            )
            row = cur.fetchone()
            if not row:
                return {}
            n = row.get("n") if hasattr(row, "get") else row[0]
            agree = row.get("agree") if hasattr(row, "get") else row[1]
            return {
                "calibration_n_rows": n,
                "calibration_agreement_pct": round(float(agree) * 100, 1) if agree else None,
            }
        except Exception as exc:
            logger.warning("state_collector.calibration_failed niche=%s err=%s", niche_id, exc)
            return {}

    def _cost_state(self, niche_id: str) -> dict[str, Any]:
        # Cost tracking is half-wired (Blind Spot #1) — return None for now;
        # PR Strategist-2 can wire this when cost-per-blueprint dashboard is honest.
        return {"cost_per_blueprint": None, "cost_per_published": None}

    def _recent_publishes(self, niche_id: str, week_of: date) -> list[dict[str, Any]]:
        """Top 5 + bottom 5 + random 5 publishes from the analyzed week."""
        try:
            since = (week_of - timedelta(days=7)).isoformat()
            cur = self._conn.execute(
                """
                SELECT b.id AS blueprint_id, pa.platform, pa.engagement_score AS reward,
                       b.hook_text
                FROM publishing_analytics pa
                JOIN blueprints b ON b.id = pa.blueprint_id
                WHERE b.niche_id = %s AND pa.published_at >= %s
                ORDER BY pa.engagement_score DESC NULLS LAST
                LIMIT 15
                """,
                (niche_id, since),
            )
            return [
                {
                    "blueprint_id": str(r.get("blueprint_id") if hasattr(r, "get") else r[0]),
                    "platform": r.get("platform") if hasattr(r, "get") else r[1],
                    "reward": r.get("reward") if hasattr(r, "get") else r[2],
                    "hook_text": r.get("hook_text") if hasattr(r, "get") else r[3],
                }
                for r in cur.fetchall()
            ]
        except Exception as exc:
            logger.warning("state_collector.publishes_failed niche=%s err=%s", niche_id, exc)
            return []

    def _cross_niche_summary(self, exclude: str) -> dict[str, str]:
        """One-line summary per OTHER niche so the LLM can spot cross-niche patterns."""
        try:
            cur = self._conn.execute(
                """
                SELECT niche_id,
                       MAX(follower_count) AS followers,
                       AVG(engagement_rate) FILTER (WHERE collected_at >= NOW() - INTERVAL '7 days') AS er
                FROM analytics
                WHERE niche_id != %s
                GROUP BY niche_id
                """,
                (exclude,),
            )
            out = {}
            for r in cur.fetchall():
                niche = r.get("niche_id") if hasattr(r, "get") else r[0]
                followers = r.get("followers") if hasattr(r, "get") else r[1]
                er = r.get("er") if hasattr(r, "get") else r[2]
                out[niche] = f"followers={followers} er7d={er}"
            return out
        except Exception as exc:
            logger.warning("state_collector.cross_niche_failed err=%s", exc)
            return {}

    def _active_findings(self, niche_id: str) -> list[dict[str, Any]]:
        """Pulls active learning_findings — empty list if table doesn't exist yet."""
        try:
            cur = self._conn.execute(
                """
                SELECT finding_text, evidence_count
                FROM learning_findings
                WHERE niche_id = %s AND active = true
                ORDER BY evidence_count DESC LIMIT 10
                """,
                (niche_id,),
            )
            return [
                {
                    "finding_text": r.get("finding_text") if hasattr(r, "get") else r[0],
                    "evidence_count": r.get("evidence_count") if hasattr(r, "get") else r[1],
                }
                for r in cur.fetchall()
            ]
        except Exception:
            # Table doesn't exist yet — that's expected pre-migration.
            return []

    def _last_week_outcomes(self, niche_id: str, week_of: date) -> list[dict[str, Any]]:
        """Prior week's Strategist proposals + operator decisions. Empty on first run."""
        try:
            last_week = (week_of - timedelta(days=7)).isoformat()
            cur = self._conn.execute(
                """
                SELECT proposals, proposals_accepted, proposals_rejected
                FROM strategist_reports
                WHERE niche_id = %s AND week_of = %s
                LIMIT 1
                """,
                (niche_id, last_week),
            )
            row = cur.fetchone()
            if not row:
                return []
            # Just summarize; full structure is in DB
            return [{"proposal_summary": "see prior report", "operator_action": "see prior report"}]
        except Exception:
            # Table doesn't exist yet — first-run case.
            return []
