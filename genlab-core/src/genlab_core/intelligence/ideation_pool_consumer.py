"""Ideation pool consumer (Phase 4.E session 2).

Reserves top-scoring pending ideas from ``content_ideas_pool`` +
links each to a materialized blueprint. Called by the runner that
promotes ideas → blueprints when the trending-video source
returns insufficient stories for a niche's daily cadence.

## Reservation pattern

UPDATE ... RETURNING pattern to atomically claim ideas — no race
between concurrent runners (there is only one runner today, but
this pattern protects against future concurrency). Only claims
one at a time to keep the transaction small.

## Rollback

If the caller's downstream blueprint creation fails after
reservation, the caller can call ``release_reservation`` to flip
status back to 'pending'. Otherwise link_to_blueprint records
the consumed_by_blueprint_id + leaves status='consumed'.

## Fail-open

Every layer: no ideas / DB error / claim contention → returns
empty list. Caller degrades to "no fallback stories available"
which is the same state as before Phase 4.E existed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReservedIdea:
    """A pool row atomically claimed for a materialization pass."""
    id: str
    niche_id: str
    title: str
    hook_seed: str
    rationale: str
    score: float
    batch_id: str


def reserve_top_pending(conn, niche_id: str, limit: int = 1) -> list[ReservedIdea]:
    """Atomically claim up to ``limit`` top-scored pending ideas
    for materialization. Flips status='consumed' immediately —
    the caller has ~one transaction window to link to a blueprint
    or release.

    Returns empty list on any failure.
    """
    if limit <= 0:
        return []
    try:
        rows = conn.execute(
            """
            UPDATE content_ideas_pool
            SET status = 'consumed', consumed_at = NOW()
            WHERE id IN (
              SELECT id FROM content_ideas_pool
              WHERE niche_id = %s AND status = 'pending'
              ORDER BY score DESC, created_at DESC
              LIMIT %s
              FOR UPDATE SKIP LOCKED
            )
            RETURNING id::text, niche_id, title, hook_seed, rationale,
                      score, batch_id::text
            """,
            (niche_id, limit),
        ).fetchall()
        conn.commit()
    except Exception as exc:
        logger.warning(
            "[pool_consumer] reserve failed niche=%s: %s", niche_id, exc,
        )
        try:
            conn.rollback()
        except Exception:
            pass
        return []
    return [
        ReservedIdea(
            id=r.get("id") if hasattr(r, "get") else r[0],
            niche_id=r.get("niche_id") if hasattr(r, "get") else r[1],
            title=r.get("title") if hasattr(r, "get") else r[2],
            hook_seed=r.get("hook_seed") if hasattr(r, "get") else r[3],
            rationale=r.get("rationale") if hasattr(r, "get") else r[4],
            score=float(r.get("score") if hasattr(r, "get") else r[5]),
            batch_id=r.get("batch_id") if hasattr(r, "get") else r[6],
        )
        for r in rows or []
    ]


def link_to_blueprint(
    conn, idea_id: str, blueprint_id: str,
) -> bool:
    """Record the consumed_by_blueprint_id backpointer. Status is
    already 'consumed' from reserve — this just fills in the link
    for the analyzer.

    Returns False on failure but doesn't attempt rollback because
    the reservation is already committed."""
    try:
        conn.execute(
            """
            UPDATE content_ideas_pool
            SET consumed_by_blueprint_id = %s
            WHERE id = %s
            """,
            (blueprint_id, idea_id),
        )
        conn.commit()
        return True
    except Exception as exc:
        logger.warning(
            "[pool_consumer] link failed idea=%s: %s", idea_id, exc,
        )
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def release_reservation(conn, idea_id: str) -> bool:
    """Flip a consumed-but-unmaterialized row back to pending so a
    later run can retry. Only flips rows still without a blueprint
    link — a fully-consumed row is not touchable."""
    try:
        result = conn.execute(
            """
            UPDATE content_ideas_pool
            SET status = 'pending', consumed_at = NULL
            WHERE id = %s
              AND consumed_by_blueprint_id IS NULL
              AND status = 'consumed'
            """,
            (idea_id,),
        )
        conn.commit()
        return (result.rowcount or 0) > 0
    except Exception as exc:
        logger.warning(
            "[pool_consumer] release failed idea=%s: %s", idea_id, exc,
        )
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def count_pool_status(conn, niche_id: str) -> dict[str, int]:
    """{'pending': N, 'consumed': N, 'expired': N} for the niche.
    Used by session-3 card + operator dashboards."""
    try:
        rows = conn.execute(
            """
            SELECT status, COUNT(*)::int AS n
            FROM content_ideas_pool
            WHERE niche_id = %s
            GROUP BY status
            """,
            (niche_id,),
        ).fetchall()
    except Exception:
        return {"pending": 0, "consumed": 0, "expired": 0}
    out = {"pending": 0, "consumed": 0, "expired": 0}
    for r in rows or []:
        status = r.get("status") if hasattr(r, "get") else r[0]
        n = r.get("n") if hasattr(r, "get") else r[1]
        if status in out:
            out[status] = int(n or 0)
    return out
