"""Selection by kind, and the three things that must never be selectable.

Six apps were in use out of a hundred-plus because every call site named its
own ref and carried its own hardcoded cost. The registry ends that, but only
if the guarantees hold: cheapest MEASURED wins, unmeasured and eval_only
cannot ship, and an unknown kind is loud rather than empty.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest
from genlab_core.capabilities import registry as R

_SRC = pathlib.Path(R.__file__)
_COSTS = _SRC.with_name("measured_costs.json")


class TestSelectionByKind:
    def test_every_kind_in_the_registry_resolves_or_says_why(self):
        for kind in sorted(R.KINDS):
            entries = R.for_kind(kind)
            if not entries:
                continue
            try:
                chosen = R.select(kind)
            except R.Unselectable:
                assert not any(c.selectable_for_production for c in entries)
                continue
            assert chosen.selectable_for_production

    def test_cheapest_measured_entry_wins(self):
        """tts has two entries; the measured, production-rights one must win."""
        entries = R.for_kind("tts")
        assert len(entries) >= 2, "this pin is vacuous with one entry"
        chosen = R.select("tts")
        cheaper_usable = [
            c
            for c in entries
            if c.selectable_for_production
            and (c.cost_per_unit_usd or 0) < (chosen.cost_per_unit_usd or 0)
        ]
        assert not cheaper_usable, f"a cheaper usable entry was skipped: {cheaper_usable}"

    def test_unknown_kind_fails_loudly(self):
        with pytest.raises(R.UnknownKind, match="unknown capability kind"):
            R.select("sparkle_overlay")
        with pytest.raises(R.UnknownKind):
            R.for_kind("")

    def test_unknown_kind_does_not_return_empty(self):
        """The failure this replaces: a typo resolving to nothing looks exactly
        like "no capability configured", which is unreadable at 02:00."""
        try:
            R.for_kind("stil")  # typo for "still"
        except R.UnknownKind:
            return
        pytest.fail("a misspelled kind returned instead of raising")


class TestUnselectable:
    def test_eval_only_is_never_selected_for_production(self):
        evals = [c for c in R._REGISTRY if c.eval_only]
        assert evals, "this pin is vacuous with no eval_only entries"
        for c in evals:
            assert not c.selectable_for_production
            assert R.select(c.kind) is not c

    def test_unmeasured_is_unselectable_however_cheap_the_catalog_looks(self):
        unmeasured = [c for c in R._REGISTRY if not c.measured]
        for c in unmeasured:
            assert not c.selectable_for_production, (
                f"{c.ref} has no billed measurement; the catalog price is not a "
                "substitute — topaz quotes per CREDIT and spanned $0.07-$14.29"
            )

    def test_unread_rights_cannot_ship(self):
        for c in R._REGISTRY:
            if c.rights != "owned_channels":
                assert not c.selectable_for_production


class TestCostProvenance:
    def test_no_cost_literal_lives_in_the_registry_module(self):
        """Costs come from measurement, so they are data, not code.

        A float literal in this module would be a catalogued price wearing a
        measured one's clothes. The previous two registries each carried one
        (`cost_per_image_usd`, `cost_per_5s_usd`) that had never been billed.
        """
        tree = ast.parse(_SRC.read_text())
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.keyword):
                continue
            if "cost" not in (node.arg or ""):
                continue
            if isinstance(node.value, ast.Constant) and isinstance(
                node.value.value, int | float
            ):
                offenders.append(f"{node.arg}={node.value.value}")
        assert not offenders, (
            "cost literals in registry.py: "
            + ", ".join(offenders)
            + " — measured costs belong in measured_costs.json with their task id"
        )

    def test_every_measured_cost_carries_its_receipt(self):
        costs = json.loads(_COSTS.read_text())
        assert costs, "no measurements at all"
        for ref, d in costs.items():
            assert d.get("task_id"), f"{ref} has a cost with no belt task id"
            assert d.get("measured_at"), f"{ref} has a cost with no date"
            assert d.get("unit"), f"{ref} has a cost with no unit — a number per WHAT?"
            assert isinstance(d.get("cost_per_unit_usd"), int | float)

    def test_measured_refs_all_exist_in_the_registry(self):
        known = {c.ref for c in R._REGISTRY}
        stray = set(json.loads(_COSTS.read_text())) - known
        assert not stray, f"measured costs for refs not in the registry: {sorted(stray)}"
