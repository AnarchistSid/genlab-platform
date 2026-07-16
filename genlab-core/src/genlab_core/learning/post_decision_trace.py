"""Per-post decision-trace UPSERT helpers.

Captures (bandit score, gate prediction, operator decision, actual
engagement) per blueprint in a SINGLE post_decision_trace row.
Downstream analysis (drift detection, exploration calibration,
AUTO #2 readiness check, the bandit_validation harness) can draw
from one canonical source instead of re-joining 4 tables.

Three writer surfaces — each idempotent via UPSERT on blueprint_id:

  1. record_bandit_pick — called at push time (PushToBacklog stage).
     Writes bandit_arm_id + bandit_predicted_score + bandit_confidence.

  2. record_operator_decision — called from calibration_helper after
     calibration_logger.log. Writes gate_verdict + gate_confidence +
     operator_action.

  3. record_engagement_window — called from metric_collector at the
     24h and 168h windows. Writes the engagement counters + the
     final computed_reward.

Each function:
  * Is fail-OPEN — returns False on DSN missing / DB error / invalid
    input. Never raises. NEVER blocks the caller's primary path.
  * Uses INSERT ... ON CONFLICT (blueprint_id) DO UPDATE so the row
    is enriched across the 3 events instead of being duplicated. The
    UPDATE only sets the fields the caller passed (others preserved
    via COALESCE / explicit field list).
  * Defers psycopg + storage imports until call time so module
    import is cheap.

The table is created by migration x5s6t7u8v9w0. See the migration's
docstring for the full schema rationale.
"""

from __future__ import annotations

import logging
import os
import uuid

logger = logging.getLogger(__name__)


def _connect():
    """Lazy psycopg connect. None on missing DSN / connect error.

    Matches the source_performance + bandit_validation pattern: fail
    OPEN by returning None, callers gracefully short-circuit.
    """
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        logger.debug("[trace] DATABASE_URL not set; trace writes disabled")
        return None
    try:
        from psycopg.rows import dict_row

        from genlab_core.storage.tenant_context import pg_connect

        return pg_connect(dsn, row_factory=dict_row, niche_id="all", connect_timeout=5)
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("[trace] connection failed: %s", exc)
        return None


# 2026-07-14 session finding: post_decision_trace stayed at 0 rows
# for months because ALL writers passed the SHA256 candidate_id
# (64 hex chars, not a UUID). _coerce_uuid rejected everything →
# every write silently no-op'd. Table designed as canonical
# analysis surface for AUTO #2 readiness + drift detection was
# structurally dead.
#
# Fix: deterministic UUID5 conversion. When the value isn't a
# valid UUID directly, derive a stable UUID5 from the SHA256 hash
# using a genlab-scoped namespace. Same input always produces the
# same UUID (idempotent for the ON CONFLICT clause). No schema
# change needed; downstream analysis joins by the UUID.
_GENLAB_NAMESPACE_UUID = uuid.UUID("6f8b3e3d-4b7f-4c9c-8e3f-c1b3e5d1a4b2")


def _coerce_uuid(value: str | None) -> str | None:
    """Normalise a blueprint_id to canonical UUID string form.

    Accepts:
      * Actual UUIDs (blueprint.id column) — returned unchanged
      * SHA256 candidate_id (64 hex chars) — converted via uuid5
        with the genlab namespace so all callers land on the same
        canonical form for the ON CONFLICT clause
      * Anything else — returned as uuid5 as long as it's a str
        (SharePoint record ids etc.)

    Returns None only for empty/None input. Prior strict UUID-only
    check masked the whole table's write path for months.
    """
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        # Direct UUID case (blueprint.id column already-canonical)
        return str(uuid.UUID(text))
    except (ValueError, AttributeError):
        pass
    # Not a UUID — derive a stable UUID5 from the input string.
    # Same input always produces the same output → ON CONFLICT
    # (blueprint_id) DO UPDATE stays idempotent across the 3
    # lifecycle writers.
    return str(uuid.uuid5(_GENLAB_NAMESPACE_UUID, text))


def record_bandit_pick(
    *,
    blueprint_id: str,
    niche_id: str,
    bandit_arm_id: str,
    bandit_predicted_score: float | None = None,
    bandit_confidence: float | None = None,
) -> bool:
    """Wire-point #1 — fired from push_to_backlog at blueprint creation.

    Inserts or updates the per-blueprint trace row with the bandit
    decision fields. ON CONFLICT preserves any engagement / operator
    fields previously written (defensive — push runs before the
    other 2 events, but a re-push can happen on retries).

    Returns True on write, False fail-OPEN otherwise.
    """
    bp_uuid = _coerce_uuid(blueprint_id)
    if not bp_uuid or not niche_id or not bandit_arm_id:
        return False
    conn_cm = _connect()
    if conn_cm is None:
        return False
    try:
        with conn_cm as conn:
            conn.execute(
                """
                INSERT INTO post_decision_trace (
                    blueprint_id, niche_id, bandit_arm_id,
                    bandit_predicted_score, bandit_confidence,
                    created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (blueprint_id) DO UPDATE
                SET bandit_arm_id = EXCLUDED.bandit_arm_id,
                    bandit_predicted_score = EXCLUDED.bandit_predicted_score,
                    bandit_confidence = EXCLUDED.bandit_confidence,
                    updated_at = NOW()
                """,
                (
                    bp_uuid,
                    niche_id,
                    bandit_arm_id,
                    bandit_predicted_score,
                    bandit_confidence,
                ),
            )
        return True
    except Exception as exc:  # noqa: BLE001 — fail-open
        # 2026-07-14: elevated DEBUG → WARNING. Table was 0 rows for
        # months due to the _coerce_uuid bug (fixed same session);
        # if we regress into silent write-failures we want to see it.
        logger.warning("[trace] record_bandit_pick failed for %s: %s", blueprint_id, exc)
        return False


def record_operator_decision(
    *,
    blueprint_id: str,
    niche_id: str,
    gate_verdict: bool | None,
    gate_confidence: float | None,
    operator_action: str | None,
) -> bool:
    """Wire-point #2 — fired from calibration_helper after every
    operator review action.

    Updates only the gate + operator fields. The bandit fields from
    wire-point #1 are preserved (per ON CONFLICT clause). When the
    push step never wrote (e.g. blueprint created via a legacy path
    that skipped record_bandit_pick), this INSERT creates the row
    with NULL bandit fields — still useful: the operator-decision
    fraction is intact for AUTO #2 readiness analysis.

    Returns True on write, False fail-OPEN otherwise.
    """
    bp_uuid = _coerce_uuid(blueprint_id)
    if not bp_uuid or not niche_id:
        return False
    conn_cm = _connect()
    if conn_cm is None:
        return False
    try:
        with conn_cm as conn:
            conn.execute(
                """
                INSERT INTO post_decision_trace (
                    blueprint_id, niche_id, gate_verdict, gate_confidence,
                    operator_action, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (blueprint_id) DO UPDATE
                SET gate_verdict = EXCLUDED.gate_verdict,
                    gate_confidence = EXCLUDED.gate_confidence,
                    operator_action = EXCLUDED.operator_action,
                    updated_at = NOW()
                """,
                (
                    bp_uuid,
                    niche_id,
                    gate_verdict,
                    gate_confidence,
                    operator_action,
                ),
            )
        return True
    except Exception as exc:  # noqa: BLE001 — fail-open
        # 2026-07-14: elevated DEBUG → WARNING (see record_bandit_pick).
        logger.warning("[trace] record_operator_decision failed for %s: %s", blueprint_id, exc)
        return False


def record_engagement_window(
    *,
    blueprint_id: str,
    niche_id: str,
    published_at: str | None = None,
    engagement_reach_24h: int | None = None,
    engagement_reach_168h: int | None = None,
    engagement_likes: int | None = None,
    computed_reward: float | None = None,
) -> bool:
    """Wire-point #3 — fired from metric_collector at 24h and 168h
    windows.

    Updates only the engagement + reward fields. The 24h window
    typically only has engagement_reach_24h to set; the 168h window
    adds engagement_reach_168h + computed_reward. Each call merges
    its non-None fields without clobbering the other window's data
    (we use COALESCE on the UPDATE to keep prior non-NULL values).

    Returns True on write, False fail-OPEN otherwise.
    """
    bp_uuid = _coerce_uuid(blueprint_id)
    if not bp_uuid or not niche_id:
        return False
    conn_cm = _connect()
    if conn_cm is None:
        return False
    try:
        with conn_cm as conn:
            # COALESCE(EXCLUDED.x, post_decision_trace.x) keeps the
            # already-stored value when the caller doesn't pass a new
            # one — lets 24h and 168h merge cleanly.
            conn.execute(
                """
                INSERT INTO post_decision_trace (
                    blueprint_id, niche_id, published_at,
                    engagement_reach_24h, engagement_reach_168h,
                    engagement_likes, computed_reward,
                    created_at, updated_at
                ) VALUES (
                    %s, %s, %s::timestamptz, %s, %s, %s, %s, NOW(), NOW()
                )
                ON CONFLICT (blueprint_id) DO UPDATE
                SET published_at = COALESCE(
                        EXCLUDED.published_at, post_decision_trace.published_at
                    ),
                    engagement_reach_24h = COALESCE(
                        EXCLUDED.engagement_reach_24h,
                        post_decision_trace.engagement_reach_24h
                    ),
                    engagement_reach_168h = COALESCE(
                        EXCLUDED.engagement_reach_168h,
                        post_decision_trace.engagement_reach_168h
                    ),
                    engagement_likes = COALESCE(
                        EXCLUDED.engagement_likes,
                        post_decision_trace.engagement_likes
                    ),
                    computed_reward = COALESCE(
                        EXCLUDED.computed_reward,
                        post_decision_trace.computed_reward
                    ),
                    updated_at = NOW()
                """,
                (
                    bp_uuid,
                    niche_id,
                    published_at,
                    engagement_reach_24h,
                    engagement_reach_168h,
                    engagement_likes,
                    computed_reward,
                ),
            )
        return True
    except Exception as exc:  # noqa: BLE001 — fail-open
        # 2026-07-14: elevated DEBUG → WARNING (see record_bandit_pick).
        logger.warning("[trace] record_engagement_window failed for %s: %s", blueprint_id, exc)
        return False
