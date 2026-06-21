"""Pin: ``save_arm`` uses the composite UNIQUE index, not a full-table scan.

Perf fix (2026-06-21): the upsert lookup was previously
``proxy.all() + next(item for item in existing if ...)`` — a full-table
scan + linear Python search per call. Inside the bandit reward loop at
``metric_collector.py:813`` this fired N+1 times per reward window.

The fix uses ``proxy.all(formula=...)`` to push the lookup down to a
single index probe against the composite ``UNIQUE(niche_id, arm_id)``
constraint added in migration ``i9d0e1f2g3h4``.

These pins lock in:

  1. When ``niche_id`` is provided, ``proxy.all`` is called with the
     composite formula + ``max_records=1`` (single index probe — no
     full-table scan, no Python-side filter loop).
  2. ``niche_id`` is written to the row's ``niche_id`` column on
     create — so the composite constraint can do its job.
  3. When ``niche_id`` is omitted (legacy callers), the lookup falls
     back to arm_id-only — backward-compat preserved.
  4. The legacy Title-column scan is ONLY triggered when no row matches
     the formula AND no niche_id was passed — keeps the slow path
     reachable for niches still on the single-column layout, but
     doesn't pay for it when the indexed path succeeds.
  5. Cross-niche isolation: an arm_id present in two niches gets
     resolved to the right row when niche_id is provided.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from genlab_core.learning.arm_loader import save_arm


class TestIndexLookupViaFormula:
    def test_save_with_niche_id_uses_composite_formula(self):
        """The pin: ``proxy.all`` is called with the composite formula
        + max_records=1, NOT with an empty/missing formula (which would
        be the regression-back-to-full-scan signature)."""
        proxy = MagicMock()
        proxy.all.return_value = []  # no existing row → create path

        save_arm(
            proxy,
            arm_id="gameplay_clip__youtube",
            alpha=2.0,
            beta=1.5,
            niche_id="gaming",
        )

        # proxy.all called exactly once (the indexed lookup), with the
        # composite formula and max_records=1.
        assert proxy.all.call_count == 1, (
            f"Expected 1 indexed call to proxy.all; got {proxy.all.call_count}. "
            f"Did someone re-introduce the full-table scan?"
        )
        call = proxy.all.call_args
        formula = call.kwargs.get("formula", "")
        assert "{arm_id}='gameplay_clip__youtube'" in formula
        assert "{niche_id}='gaming'" in formula
        assert formula.startswith("AND("), (
            f"Expected composite AND(...) formula for niche-scoped lookup; got: {formula}"
        )
        assert call.kwargs.get("max_records") == 1, (
            f"Expected max_records=1 (single-row probe); got {call.kwargs.get('max_records')}."
        )

    def test_save_with_niche_id_writes_niche_to_row_on_create(self):
        """The composite UNIQUE constraint requires niche_id on the row
        to be effective. Pin that we write it on create."""
        proxy = MagicMock()
        proxy.all.return_value = []  # no match → create

        save_arm(
            proxy,
            arm_id="highlight_play__instagram",
            alpha=1.0,
            beta=1.0,
            niche_id="sports",
        )

        proxy.create.assert_called_once()
        created_fields = proxy.create.call_args.args[0]
        assert created_fields.get("niche_id") == "sports", (
            f"Expected niche_id='sports' in create payload; got "
            f"{created_fields.get('niche_id')!r}. Composite UNIQUE "
            f"constraint won't fire without it."
        )
        assert created_fields.get("arm_id") == "highlight_play__instagram"

    def test_save_with_niche_id_update_path(self):
        """Existing row found by the composite-formula probe → update,
        not create. n_plays preserved when caller doesn't pass it."""
        proxy = MagicMock()
        proxy.all.return_value = [
            {
                "id": "rec-123",
                "fields": {
                    "arm_id": "cast_reveal__youtube",
                    "niche_id": "movies",
                    "alpha": 5.0,
                    "beta": 3.0,
                    "n_plays": 42,
                },
            }
        ]

        save_arm(
            proxy,
            arm_id="cast_reveal__youtube",
            alpha=6.0,
            beta=3.0,
            niche_id="movies",
        )

        proxy.create.assert_not_called()
        proxy.update.assert_called_once()
        record_id, fields = proxy.update.call_args.args
        assert record_id == "rec-123"
        assert fields["alpha"] == 6.0
        # n_plays NOT in update fields → existing value preserved (the
        # 2026-04-XX bug fix this code already had).
        assert "n_plays" not in fields


class TestBackwardCompatNoNicheId:
    """When callers don't pass ``niche_id``, the legacy behavior is preserved."""

    def test_no_niche_falls_back_to_arm_id_only_formula(self):
        """Legacy callers (or any not-yet-migrated caller) still work:
        the formula uses arm_id alone, matching prior single-column
        UNIQUE behavior on niches that haven't migrated yet."""
        proxy = MagicMock()
        proxy.all.return_value = [
            {"id": "r1", "fields": {"arm_id": "x__y", "alpha": 2.0, "beta": 1.0}}
        ]

        save_arm(proxy, arm_id="x__y", alpha=3.0, beta=1.0)
        # NO niche_id passed.

        # First proxy.all call uses arm_id-only formula.
        first_call = proxy.all.call_args_list[0]
        formula = first_call.kwargs.get("formula", "")
        assert formula == "{arm_id}='x__y'", (
            f"Expected arm_id-only fallback formula; got {formula!r}"
        )

    def test_no_niche_legacy_title_fallback_when_formula_empty(self):
        """If the arm_id-only formula returns no rows AND no niche_id was
        passed, we fall back to a full-scan + Title-column match. This
        keeps legacy SharePoint-backend rows reachable."""
        proxy = MagicMock()
        # First call (formula): empty (formula filter doesn't match Title col)
        # Second call (legacy full scan): returns the row by Title
        proxy.all.side_effect = [
            [],
            [{"id": "legacy-1", "fields": {"Title": "legacy_arm__instagram"}}],
        ]

        save_arm(proxy, arm_id="legacy_arm__instagram", alpha=2.0, beta=1.0)

        # Two proxy.all calls: 1 formula probe + 1 legacy fallback scan.
        assert proxy.all.call_count == 2, (
            f"Expected fallback to legacy Title scan; got {proxy.all.call_count} proxy.all calls."
        )
        # The fallback found the row → update path.
        proxy.update.assert_called_once()
        proxy.create.assert_not_called()

    def test_with_niche_id_does_not_trigger_legacy_fallback_on_miss(self):
        """When niche_id IS passed and the indexed probe returns empty,
        we go straight to create — NO expensive legacy full-scan. The
        niche_id contract is "you know your namespace; if it's not
        there it doesn't exist"."""
        proxy = MagicMock()
        proxy.all.return_value = []  # no existing row in the niche

        save_arm(
            proxy,
            arm_id="brand_new__youtube",
            alpha=1.0,
            beta=1.0,
            niche_id="anime",
        )

        # The pin: exactly ONE proxy.all call (no legacy fallback). If
        # someone accidentally adds the legacy scan to the niche-scoped
        # path, this fires.
        assert proxy.all.call_count == 1, (
            f"Niche-scoped miss should NOT trigger legacy full-scan; "
            f"got {proxy.all.call_count} proxy.all calls."
        )
        proxy.create.assert_called_once()


class TestCrossNicheIsolation:
    """The composite formula resolves correctly when an arm_id exists in
    multiple niches (which the original c3d4e5f6a7b8 single-column
    UNIQUE prevented, but i9d0e1f2g3h4 deliberately allows)."""

    def test_niche_scoped_lookup_selects_right_row(self):
        """Two rows in the simulated table: gaming and sports both have
        ``highlight_play``. The niche-scoped formula must resolve to
        sports' row when niche_id='sports' is passed."""
        proxy = MagicMock()

        # Simulate the SQL backend filtering by formula: only return the
        # row matching niche_id='sports'.
        sports_row = {
            "id": "sports-row",
            "fields": {
                "arm_id": "highlight_play",
                "niche_id": "sports",
                "alpha": 4.0,
                "beta": 2.0,
            },
        }
        proxy.all.return_value = [sports_row]

        save_arm(
            proxy,
            arm_id="highlight_play",
            alpha=5.0,
            beta=2.0,
            niche_id="sports",
        )

        # Update path on the right row id.
        proxy.update.assert_called_once()
        record_id, _ = proxy.update.call_args.args
        assert record_id == "sports-row", (
            f"Expected update to target sports' row; got {record_id!r}. Cross-niche contamination?"
        )

        # And the formula carried niche_id='sports' (so the SQL backend
        # had the right info to filter).
        formula = proxy.all.call_args.kwargs.get("formula", "")
        assert "{niche_id}='sports'" in formula
