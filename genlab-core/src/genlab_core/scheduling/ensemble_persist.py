"""Ensemble vote persistence (L5, 2026-07-08 audit).

Writes per-component votes + aggregate to Postgres after every
``ensemble_decide`` call. Enables post-hoc analysis of component
disagreement + operator-vs-ensemble calibration that the ephemeral
in-memory ``EnsembleDecision`` currently loses.

## Flag gating

Independent env flag ``GENLAB_ENSEMBLE_PERSIST_ENABLED`` (strict "1"
match, mirroring the sibling gate flags surfaced by the L11 flag-
state endpoint). Independent of ``GENLAB_ENSEMBLE_DECISION_ENABLED``
so the operator can:

  * Enable ensemble but NOT persist (existing behaviour) — data
    is displayed on the badge but not stored
  * Enable ensemble AND persist (this PR's default when both are set)
  * Persist while ensemble is otherwise off (nonsensical — the
    caller wouldn't invoke ensemble_decide at all)

## Fail-open discipline

Persistence is BEST-EFFORT. If Postgres is down, the connection pool
is exhausted, or the schema hasn't been migrated yet, this module
logs at WARNING and returns silently. It MUST NEVER raise into the
caller — the ensemble decision itself still has to reach the reviewer
even if the audit-trail write fails.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from genlab_core.scheduling.ensemble_decide import EnsembleDecision

logger = logging.getLogger(__name__)

_ENABLE_ENV_VAR = "GENLAB_ENSEMBLE_PERSIST_ENABLED"


def _persist_enabled() -> bool:
    """Return True iff persistence is opt-in via a truthy env value.

    2026-07-14: unified with the codebase-canonical ``env_true`` helper
    (accepts ``1``, ``true``, ``yes``, ``on``, ``y``, ``t`` — case-
    insensitive). Prior implementation required the STRICT value ``"1"``
    (rejects ``"true"``), while sibling
    :func:`genlab_core.scheduling.ensemble_decide._integration_enabled`
    required the STRICT value ``"true"`` (rejects ``"1"``) — two
    incompatible activation semantics in the same module. An operator
    setting ``GENLAB_ENSEMBLE_DECISION_ENABLED=true`` +
    ``GENLAB_ENSEMBLE_PERSIST_ENABLED=true`` would activate the first
    but silently no-op the second, producing 0 rows in
    ``ensemble_votes`` while the badge on Focus Review still showed
    ensemble decisions — a proxy-signal-masking class-of-bug.
    """
    from genlab_core.settings import env_true

    return env_true(_ENABLE_ENV_VAR)


def record_decision(blueprint_id: str, niche_id: str, decision: EnsembleDecision) -> bool:
    """Persist all component votes + aggregate for one decision.

    Args:
        blueprint_id: The blueprint the decision was made about.
            Free-text; matches the ``blueprints.id`` shape used
            elsewhere. Empty string is accepted — the row still
            writes, but downstream operator queries won't find it.
        niche_id: The niche the blueprint belongs to. Used for the
            per-niche summary endpoint.
        decision: The :class:`EnsembleDecision` returned from
            ``ensemble_decide``. Every vote in ``decision.votes``
            gets its own row; ``score``/``disagreement``/
            ``recommendation``/``n_voters`` are denormalized across
            all rows for the decision.

    Returns:
        ``True`` on successful write of at least one row.
        ``False`` if disabled, if the decision has no votes, or if
        anything raised — the caller can treat this as advisory but
        should never predicate correctness on it.

    Never raises. Any exception is logged at WARNING and swallowed.
    """
    if not _persist_enabled():
        return False

    # An empty vote list can happen when the ensemble is env-disabled
    # OR when 0 components voted. Nothing meaningful to persist.
    votes = list(decision.votes)
    if not votes:
        logger.debug(
            "ensemble_persist.skip",
            extra={"reason": "no_votes", "blueprint_id": blueprint_id},
        )
        return False

    # 2026-07-14: switched from ``genlab_core.storage.get_pool`` (which
    # doesn't exist — the import silently failed and every persist
    # returned False as "ensemble_persist.pool_import_failed") to the
    # canonical ``pg_connect`` helper from ``storage.tenant_context``.
    # Same helper ips_replay uses for its persist path. Yields a raw
    # psycopg connection with the tenant GUC already set so RLS on
    # ensemble_votes (if added later) will filter correctly.
    #
    # Lazy import kept — avoids pulling psycopg into module init just
    # to check the flag.
    try:
        from genlab_core.storage.tenant_context import pg_connect
    except Exception as exc:  # pragma: no cover — import error path
        logger.warning(
            "ensemble_persist.pool_import_failed",
            extra={"error": str(exc)},
        )
        return False

    rows = [
        (
            blueprint_id,
            niche_id,
            v.component,
            v.score,
            v.weight,
            (v.reason or None),
            decision.score,
            decision.disagreement,
            decision.recommendation,
            decision.n_voters,
        )
        for v in votes
    ]

    sql = """
        INSERT INTO ensemble_votes (
            blueprint_id, niche_id, component,
            component_score, component_weight, component_reason,
            ensemble_score, ensemble_disagreement,
            ensemble_recommendation, n_voters
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """

    try:
        # pg_connect returns a raw psycopg connection; use it as a
        # context manager so commit/rollback + close happen automatically.
        # niche_id kwarg satisfies the RLS tenant GUC set inside pg_connect.
        with pg_connect(niche_id=niche_id) as conn:
            with conn.cursor() as cur:
                # executemany is exactly the shape we want here: one
                # decision → 4-5 rows, no error-isolation branching
                # like `shared_ingestion`'s per-row savepoint pattern.
                # If ANY row fails, all fail — that's acceptable
                # because the decision is atomic; a partial persist
                # of 3-of-5 votes would corrupt the downstream
                # disagreement calc.
                cur.executemany(sql, rows)
            conn.commit()
    except Exception as exc:
        # Broad except is intentional — any failure (network, schema,
        # permission, malformed row) surfaces the same way to the
        # caller: "we did our best, but the badge on Focus Review is
        # still correct."
        logger.warning(
            "ensemble_persist.write_failed",
            extra={
                "error": str(exc),
                "blueprint_id": blueprint_id,
                "niche_id": niche_id,
                "n_rows": len(rows),
            },
        )
        return False

    logger.debug(
        "ensemble_persist.ok",
        extra={
            "blueprint_id": blueprint_id,
            "niche_id": niche_id,
            "n_rows": len(rows),
            "recommendation": decision.recommendation,
        },
    )
    return True


__all__ = ["record_decision", "_persist_enabled", "_ENABLE_ENV_VAR"]
