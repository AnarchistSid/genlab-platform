"""AB_Tests persistence, extracted from BacklogClient.

Three methods live in this module:

* :meth:`ABTestStore.create_ab_test` — record a new A/B test row.
* :meth:`ABTestStore.get_ab_tests` — list rows (optional status +
  niche_id filters).
* :meth:`ABTestStore.update_ab_test` — find a row by ``test_id`` and
  update its fields.

Tier 2 / audit S-2, slice 2.3 — third focused extraction from the
BacklogClient god class. BacklogClient retains all 3 method names +
signatures as thin one-line delegators so the public API stays
byte-stable.

Constructor shape combines the two patterns established by slices
2.1 (AnalyticsStore — ``backend`` lookup) and 2.2 (EngagementStore —
proxy reference for the truthy "configured" gate). Pass both:

* ``ab_tests``: the proxy reference (may be None); only used for the
  ``if not self._proxy`` early-return that mirrors the historical
  "AB_Tests table not configured" warning.
* ``backend``: bound ``BacklogClient._backend`` method (live lookup
  so future backend swaps don't strand this store).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from genlab_core.http.analytics_store import _esc

logger = logging.getLogger(__name__)


class ABTestStore:
    """CRUD over the AB_Tests table."""

    def __init__(
        self,
        ab_tests: Any,
        backend: Callable[[str], Any],
    ) -> None:
        # Proxy reference for the "is configured" truthy-check. May be
        # ``None`` when the underlying SP list isn't configured. Every
        # method below early-returns on that branch.
        self._proxy = ab_tests
        # Live backend lookup. Bound to ``BacklogClient._backend`` so
        # future backend swaps (e.g. SP → Postgres) flow through here
        # without needing to rebuild the store.
        self._backend = backend

    # ── Create ─────────────────────────────────────────────────────

    def create_ab_test(self, test: dict) -> str | None:
        """Record a new A/B test row.

        PR #530 (2026-06-24, SR-C tenant binding, 4th store): pass
        ``niche_id`` from the test dict to backend.create so the row's
        ``SET LOCAL app.niche_id`` step fires. The ab_tests table on
        prod has a ``niche_id`` column (verified 2026-06-24 schema
        probe — 117 rows, tenant-scoped). Pre-PR test rows landed
        without tenant binding.

        The test dict already carries ``niche_id`` (callers building
        an A/B record for a specific channel always set it), so no
        new data is needed — just the kwarg forward.
        """
        if not self._proxy:
            logger.warning("AB_Tests table not configured")
            return None
        try:
            record = self._backend("AB_Tests").create(
                "AB_Tests",
                test,
                typecast=True,
                niche_id=test.get("niche_id"),
            )
            return record["id"]
        except Exception as exc:
            logger.warning("Failed to create AB test: %s", exc)
            return None

    # ── Read ───────────────────────────────────────────────────────

    def get_ab_tests(
        self,
        status: str | None = None,
        *,
        niche_id: str | None = None,
    ) -> list[dict]:
        if not self._proxy:
            return []
        formula = f"{{status}}='{_esc(status)}'" if status else None
        return self._backend("AB_Tests").find(
            "AB_Tests",
            formula=formula,
            niche_id=niche_id,
            max_records=50,
        )

    # ── Update ─────────────────────────────────────────────────────

    def update_ab_test(self, test_id: str, fields: dict) -> None:
        if not self._proxy:
            return
        records = self._backend("AB_Tests").find(
            "AB_Tests",
            formula=f"{{test_id}}='{_esc(test_id)}'",
            max_records=1,
        )
        if records:
            self._backend("AB_Tests").update(
                "AB_Tests",
                records[0]["id"],
                fields,
                typecast=True,
            )
