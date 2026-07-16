"""Drift signal persistence (L7, 2026-07-08 audit).

Writes `DriftSignal` records to Postgres so the operator can see
trend lines across multiple detector runs. Sibling module to
`ensemble_persist` (L5, PR #728) — same fail-open discipline.

## Flag gating

Independent env flag `GENLAB_DRIFT_PERSIST_ENABLED` (strict "1"
match, mirroring the sibling `_ENSEMBLE_PERSIST_ENABLED` guard).
Independent so the operator can:

  * Run the detector CLI without persistence (existing behaviour
    — stdout print only)
  * Persist detector output for post-hoc analysis (this PR's
    default when the flag is set)

## Fail-open discipline

Same contract as ensemble_persist: NEVER raises. Any failure logs
at WARNING and returns 0 (rows written). The caller (`detect_all_
drift`) still returns its in-memory signal list even if the write
fails.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from genlab_core.learning.drift_detector import DriftSignal

logger = logging.getLogger(__name__)

_ENABLE_ENV_VAR = "GENLAB_DRIFT_PERSIST_ENABLED"


def _persist_enabled() -> bool:
    """Return True iff drift persistence is opt-in.

    2026-07-14: migrated to canonical env_true helper matching the
    ensemble_persist fix earlier this session. Prior strict-'1' was
    the exact class-of-bug the memory
    `[[class-of-bug-metric-proxies-mask-audience-facing-failures]]`
    codifies — operator setting =true silently no-op'd the whole
    drift-persistence layer.
    """
    from genlab_core.settings import env_true

    return env_true(_ENABLE_ENV_VAR)


def _parse_computed_at(raw: str) -> datetime | None:
    """Convert DriftSignal's ISO-string `computed_at` to a datetime.

    Returns None for unparseable inputs — the caller then drops the
    row (a malformed timestamp is unrecoverable).
    """
    if not raw:
        return None
    try:
        # Handle both bare-ISO ("2026-07-08T16:00:00") and Z-suffixed
        # forms. `fromisoformat` accepts both since 3.11.
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def record_signals(signals: list[DriftSignal]) -> int:
    """Persist a batch of drift signals. Returns count written.

    Args:
        signals: The list from `detect_all_drift`. Empty list is
            explicitly OK — returns 0 without touching Postgres.

    Returns:
        Number of rows successfully written. `0` when disabled, when
        the input is empty, or when ANY error occurred. The caller
        can treat this as advisory but should never predicate
        correctness on it.

    Never raises.
    """
    if not _persist_enabled():
        return 0
    if not signals:
        logger.debug("drift_persist.skip", extra={"reason": "no_signals"})
        return 0

    # Lazy import — the CLI path uses this module, and pulling
    # psycopg into `drift_detector`'s import graph would slow the
    # CLI's cold start.
    try:
        from genlab_core.storage import get_pool  # type: ignore[attr-defined]
    except Exception as exc:  # pragma: no cover — import error path
        logger.warning("drift_persist.pool_import_failed", extra={"error": str(exc)})
        return 0

    rows = []
    for sig in signals:
        computed_at = _parse_computed_at(sig.computed_at)
        if computed_at is None:
            logger.debug(
                "drift_persist.skip_row",
                extra={
                    "arm_id": sig.arm_id,
                    "reason": "unparseable_computed_at",
                    "raw": sig.computed_at,
                },
            )
            continue
        rows.append(
            (
                sig.arm_id,
                sig.niche_id,
                float(sig.recent_rate),
                float(sig.baseline_rate),
                float(sig.z_score),
                int(sig.recent_obs),
                int(sig.baseline_obs),
                bool(sig.is_regression),
                computed_at,
            )
        )

    if not rows:
        # All signals had unparseable timestamps. Nothing to write.
        # Log at info because this is unusual but not fatal.
        logger.info(
            "drift_persist.no_valid_rows",
            extra={"input_count": len(signals)},
        )
        return 0

    try:
        pool = get_pool()
    except Exception as exc:
        logger.warning("drift_persist.pool_unavailable", extra={"error": str(exc)})
        return 0

    sql = """
        INSERT INTO drift_signals (
            arm_id, niche_id,
            recent_rate, baseline_rate, z_score,
            recent_obs, baseline_obs,
            is_regression, computed_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """

    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, rows)
            conn.commit()
    except Exception as exc:
        logger.warning(
            "drift_persist.write_failed",
            extra={"error": str(exc), "n_rows": len(rows)},
        )
        return 0

    logger.info(
        "drift_persist.ok",
        extra={"n_rows": len(rows), "current_utc": datetime.now(UTC).isoformat()},
    )
    return len(rows)


__all__ = ["record_signals", "_persist_enabled", "_ENABLE_ENV_VAR"]
