#!/usr/bin/env python3
"""Phase 4.E session 1 — weekly content ideator runner.

Fires weekly (Sun 05:30 UTC) via ``genlab-content-ideator.timer``.
Per niche:

  1. Pull input signals:
       - trend topics (trend_anticipation artifact)
       - competitor hooks (competitor_content_deltas, top delta)
       - top hook styles (hook_style_guidance latest)
       - persona.yaml (via persona_drift.load_persona)
       - recent hooks (last-14d blueprints)
  2. Call ``generate_ideas`` (LLM, budget-gated).
  3. Persist to ``content_ideas_pool`` with fresh batch_id.
  4. Optional: expire pending ideas older than 30d.

## Idempotency

Fresh batch_id per run — no dedup on title across batches. This is
DELIBERATE: a topic that was "topical" 2 weeks ago may be worth
re-ideating this week with fresh trend context. Session 2 writer
consumes the freshest pending batch's ideas first.

## Cost

5 niches × 1 Haiku call/week ≈ $0.02/week.

## Usage

    uv run python scripts/run_content_ideator.py
    uv run python scripts/run_content_ideator.py --dry-run
    uv run python scripts/run_content_ideator.py --niche gaming

## Exit codes

  * 0 — completed
  * 1 — DATABASE_URL unset
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from pathlib import Path

logger = logging.getLogger("run_content_ideator")

ACTIVE_NICHES = ("ai_creators", "anime", "gaming", "movies", "sports")

# Expire pending ideas older than N days on each run — keeps the
# pool signal-fresh and prevents session-2 writer from selecting
# stale concepts.
_EXPIRE_AFTER_DAYS = 30


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--niche", default=None)
    return ap.parse_args(argv)


def _fetch_trend_topics(niche_id: str) -> list[str]:
    """Read the latest trend-anticipation artifact for the niche.
    Same $GENLAB_TMP/trend-anticipation/{YYYYMMDD}-{niche}.json
    convention as the sibling readers."""
    tmp = os.environ.get("GENLAB_TMP")
    root = Path(tmp) if tmp else Path.cwd() / ".tmp"
    dir_ = root / "trend-anticipation"
    if not dir_.exists():
        return []
    matches = sorted(
        dir_.glob(f"*-{niche_id}.json"),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    if not matches:
        return []
    try:
        data = json.loads(matches[0].read_text())
    except Exception:
        return []
    ranking = data.get("ranking") or []
    return [
        str(r.get("topic", "")).strip()
        for r in ranking[:10]
        if isinstance(r, dict) and r.get("topic")
    ]


def _fetch_competitor_hooks(conn, niche_id: str, limit: int = 5) -> list[str]:
    """Top-delta competitor hook titles from Phase 3.A. Fail-open []."""
    try:
        rows = conn.execute(
            """
            SELECT competitor_title
            FROM competitor_content_deltas
            WHERE niche_id = %s
              AND delta_ratio >= 5.0
              AND competitor_title IS NOT NULL
              AND computed_at >= NOW() - INTERVAL '14 days'
            ORDER BY delta_ratio DESC NULLS LAST
            LIMIT %s
            """,
            (niche_id, limit),
        ).fetchall()
    except Exception as exc:
        logger.warning("[ideator] competitor query failed niche=%s: %s",
                       niche_id, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return []
    return [
        r.get("competitor_title") if hasattr(r, "get") else r[0]
        for r in rows or []
        if (r.get("competitor_title") if hasattr(r, "get") else r[0])
    ]


def _fetch_top_styles(conn, niche_id: str) -> list[str]:
    """Just the style names from Phase 4.C guidance."""
    from genlab_core.writing.style_guidance import load_latest_guidance
    styles, _ = load_latest_guidance(conn, niche_id)
    return [s.style_name for s in styles]


def _fetch_recent_hooks(conn, niche_id: str, limit: int = 10) -> list[str]:
    """Last N hooks for this niche (dedup guard for LLM)."""
    try:
        rows = conn.execute(
            """
            SELECT hook_text FROM blueprints
            WHERE niche_id = %s AND hook_text IS NOT NULL AND hook_text != ''
              AND updated_at >= NOW() - INTERVAL '14 days'
            ORDER BY updated_at DESC LIMIT %s
            """,
            (niche_id, limit),
        ).fetchall()
    except Exception as exc:
        logger.warning("[ideator] recent-hooks query failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return []
    return [
        r.get("hook_text") if hasattr(r, "get") else r[0]
        for r in rows or []
    ]


def _expire_stale(conn) -> int:
    """Mark ideas older than _EXPIRE_AFTER_DAYS as expired."""
    try:
        result = conn.execute(
            """
            UPDATE content_ideas_pool
            SET status = 'expired'
            WHERE status = 'pending'
              AND created_at < NOW() - (%s || ' days')::INTERVAL
            """,
            (_EXPIRE_AFTER_DAYS,),
        )
        return result.rowcount or 0
    except Exception as exc:
        logger.warning("[ideator] expire failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return 0


def _persist_batch(
    conn, batch_id: str, batch, dry_run: bool,
) -> int:
    """INSERT one row per idea in the batch. Returns count written."""
    if dry_run or not batch.ideas:
        return 0
    written = 0
    for idea in batch.ideas:
        try:
            conn.execute(
                """
                INSERT INTO content_ideas_pool
                  (niche_id, title, hook_seed, rationale, source_signals,
                   score, batch_id)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s::uuid)
                """,
                (
                    batch.niche_id, idea.title, idea.hook_seed,
                    idea.rationale, json.dumps(batch.source_signals),
                    idea.score, batch_id,
                ),
            )
            written += 1
        except Exception as exc:
            logger.warning(
                "[ideator] persist failed niche=%s idea=%r: %s",
                batch.niche_id, idea.title[:30], exc,
            )
    return written


def _run_niche(conn, niche_id: str, dry_run: bool) -> dict:
    from genlab_core.intelligence.content_ideator import generate_ideas
    from genlab_core.quality.persona_drift import load_persona

    counts = {"ideas": 0, "persisted": 0, "cost_usd": 0.0}

    trend_topics = _fetch_trend_topics(niche_id)
    competitor_hooks = _fetch_competitor_hooks(conn, niche_id)
    top_styles = _fetch_top_styles(conn, niche_id)
    recent_hooks = _fetch_recent_hooks(conn, niche_id)
    persona = load_persona(niche_id)

    print(
        f"  {niche_id} signals: trends={len(trend_topics)} "
        f"competitors={len(competitor_hooks)} styles={len(top_styles)} "
        f"recent={len(recent_hooks)} persona={'yes' if persona else 'no'}"
    )

    batch = generate_ideas(
        niche_id, persona, trend_topics, competitor_hooks,
        top_styles, recent_hooks,
    )
    counts["ideas"] = len(batch.ideas)
    counts["cost_usd"] = batch.llm_cost_usd

    if not batch.ideas:
        print(f"    → 0 ideas (LLM empty / budget / no signals)")
        return counts

    print(f"    → {len(batch.ideas)} ideas (cost=${batch.llm_cost_usd:.4f})")
    for i, idea in enumerate(batch.ideas[:3]):
        print(f"      #{i + 1} score={idea.score:.2f} {idea.title[:60]}")

    if dry_run:
        return counts

    batch_id = str(uuid.uuid4())
    counts["persisted"] = _persist_batch(conn, batch_id, batch, dry_run)
    if counts["persisted"] > 0:
        conn.commit()
    return counts


def main(argv=None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        logger.error("DATABASE_URL unset")
        return 1

    niches = (args.niche,) if args.niche else ACTIVE_NICHES

    import psycopg
    from psycopg.rows import dict_row

    print(f"\nContent ideator run (niches={list(niches)})")
    totals = {"ideas": 0, "persisted": 0, "cost_usd": 0.0}
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        # Expire stale first so it doesn't pollute the pool view
        expired = _expire_stale(conn)
        if expired:
            conn.commit()
            print(f"  expired {expired} stale ideas (>30d pending)")

        for niche_id in niches:
            counts = _run_niche(conn, niche_id, args.dry_run)
            for k in ("ideas", "persisted"):
                totals[k] += counts[k]
            totals["cost_usd"] += counts["cost_usd"]

    logger.info(
        "[ideator] totals: ideas=%d persisted=%d cost=$%.4f",
        totals["ideas"], totals["persisted"], totals["cost_usd"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
