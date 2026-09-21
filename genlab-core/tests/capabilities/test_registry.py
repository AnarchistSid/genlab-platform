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
                # Either nothing may ship at all, or everything that may ship
                # is batch_only and so invisible in the default "fire" context.
                shippable = [c for c in entries if c.selectable_for_production]
                assert not shippable or all(c.batch_only for c in shippable), (
                    f"{kind} raised in fire context but has a non-batch "
                    f"shippable entry: {[c.ref for c in shippable]}"
                )
                continue
            assert chosen.selectable_for_production
            assert not chosen.batch_only, "fire context returned a batch_only entry"

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


class TestLatencyIsASelectionConstraint:
    """Four minutes per second is a batch capability; the fire can't see it.

    Measured 2026-09-21: pruna/p-video-edit billed 250.9 s for ONE second of
    video, so a 30 s reel is over two hours. topaz/astra billed 202.4 s for
    one second. Cost alone would have ranked both as perfectly ordinary.
    """

    def test_fire_context_cannot_return_a_batch_only_entry(self):
        batch = [c for c in R._REGISTRY if c.batch_only]
        assert batch, "this pin is vacuous with no batch_only entries"
        for c in batch:
            with pytest.raises(R.Unselectable, match="fire"):
                R.select(c.kind, context="fire")

    def test_batch_context_can(self):
        for c in (x for x in R._REGISTRY if x.batch_only and x.measured):
            assert R.select(c.kind, context="batch").batch_only

    def test_fire_is_the_default_so_a_thoughtless_caller_is_safe(self):
        for c in (x for x in R._REGISTRY if x.batch_only):
            with pytest.raises(R.Unselectable):
                R.select(c.kind)  # no context= given

    def test_an_unknown_context_is_rejected(self):
        with pytest.raises(ValueError, match="context must be one of"):
            R.select("still", context="whenever")


class TestPlanBudgetGate:
    """A plan's cost is gated before its first call."""

    ANIME_REEL = None  # built in each test; kept literal for legibility

    def _anime_reel(self):
        from genlab_core.capabilities.plan_cost import PlannedCall

        return [
            PlannedCall("tts", 1),
            PlannedCall("still", 7),
            PlannedCall("card", 1),
            PlannedCall("music", 1),
        ]

    def test_the_anime_still_reel_prices_under_its_cap(self):
        from genlab_core.capabilities.plan_cost import check_plan_budget

        cost = check_plan_budget(self._anime_reel(), cap_usd=0.25)
        assert cost.total_usd == pytest.approx(0.06604, abs=1e-5), cost
        assert not cost.unpriced

    def test_one_card_too_many_is_rejected_before_any_call(self):
        from genlab_core.capabilities.plan_cost import (
            PlannedCall,
            PlanOverBudget,
            check_plan_budget,
        )

        over = [*self._anime_reel(), PlannedCall("card", 5)]
        with pytest.raises(PlanOverBudget, match=r"plan_over_budget:0\.29104 > 0\.25000"):
            check_plan_budget(over, cap_usd=0.25)

    def test_a_missing_cap_does_not_silently_become_zero(self):
        """None means "no budget configured", not "budget of nothing"."""
        from genlab_core.capabilities.plan_cost import check_plan_budget

        assert check_plan_budget(self._anime_reel(), cap_usd=None).total_usd > 0

    def test_a_plan_naming_an_unusable_kind_is_not_priced_as_free(self):
        """restyle is batch_only; in a fire it has no price, not a zero price."""
        from genlab_core.capabilities.plan_cost import PlannedCall, check_plan_budget, price_plan

        plan = [PlannedCall("still", 1), PlannedCall("restyle", 1)]
        assert price_plan(plan, context="fire").unpriced == ("restyle",)
        with pytest.raises(R.Unselectable, match="restyle"):
            check_plan_budget(plan, cap_usd=1.0, context="fire")
        assert not price_plan(plan, context="batch").unpriced

    @pytest.mark.parametrize(
        ("niche_yaml", "niche"),
        [
            ("FrameDrift/config/niche.yaml", "anime"),
            ("SpliceReel/config/niche.yaml", "movies"),
            ("BlackboxBrief/config/niche.yaml", "ai_creators"),
            ("CriticalRush/niches/gaming/config/niche.yaml", "gaming"),
            ("ClutchWire/config/niche.yaml", "sports"),
        ],
    )
    def test_every_niche_declares_a_cap(self, niche_yaml, niche):
        import yaml

        root = pathlib.Path(__file__).resolve().parents[3]
        cfg = yaml.safe_load((root / niche_yaml).read_text())
        cap = cfg.get("capabilities", {}).get("max_generation_cost_usd")
        assert isinstance(cap, int | float) and cap > 0, (
            f"{niche} has no max_generation_cost_usd; the plan gate would pass "
            "everything for it"
        )


class TestAbsorption:
    """Two registries became one, not three.

    `media/hook_thumbnail_models.py` and `media/pruna_video_client_models.py`
    each held a parallel table with a hardcoded `cost_per_*` that had never
    been billed. Both are deleted; their entries, builders, rotation and arm
    ids live in `capabilities/`.
    """

    _REPO = pathlib.Path(__file__).resolve().parents[3]
    _DELETED = ("hook_thumbnail_models", "pruna_video_client_models")

    def _py_files(self):
        skip = {".venv", "node_modules", "__pycache__", ".git", "build", "dist"}
        for p in self._REPO.rglob("*.py"):
            if not (skip & set(p.parts)):
                yield p

    def test_the_deleted_modules_are_gone(self):
        for name in self._DELETED:
            assert not (
                self._REPO / f"genlab-core/src/genlab_core/media/{name}.py"
            ).exists(), f"{name}.py still exists"

    def test_nothing_imports_them(self):
        offenders = []
        for p in self._py_files():
            try:
                text = p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for name in self._DELETED:
                if f"import {name}" in text or f"from genlab_core.media.{name}" in text:
                    offenders.append(f"{p.relative_to(self._REPO)} -> {name}")
        assert not offenders, "imports of deleted modules:\n  " + "\n  ".join(offenders)

    def test_no_cost_literal_outside_capabilities(self):
        """A `cost_per_*` literal anywhere else is a price nobody was billed.

        AST, not grep: the comments explaining this absorption name the old
        attributes verbatim, and a substring search cannot tell code from
        prose. (The same trap caught a pin in Part 29.)
        """
        offenders = []
        for p in self._py_files():
            rel = p.relative_to(self._REPO)
            if "capabilities" in rel.parts or rel.parts[0] != "genlab-core":
                continue
            if rel.parts[1] != "src":
                continue
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                target = None
                if isinstance(node, ast.AnnAssign):
                    target = getattr(node.target, "id", None) or getattr(
                        node.target, "attr", None
                    )
                elif isinstance(node, ast.keyword):
                    target = node.arg
                if not target or not target.startswith("cost_per"):
                    continue
                val = node.value
                if isinstance(val, ast.Constant) and isinstance(val.value, int | float):
                    offenders.append(f"{rel}:{node.lineno} {target}={val.value}")
        assert not offenders, (
            "cost_per_* literals outside capabilities/:\n  "
            + "\n  ".join(offenders)
            + "\nMeasured costs belong in measured_costs.json with a belt task id."
        )

    def test_the_absorbed_kinds_are_present_with_their_full_sets(self):
        assert len(R.in_registration_order("thumbnail")) == 6
        assert len(R.in_registration_order("video_gen")) == 5

    def test_arm_id_prefixes_match_what_the_reward_router_joins_on(self):
        """Changing a prefix orphans every arm that already has history."""
        thumb = R.in_registration_order("thumbnail")[0]
        video = R.in_registration_order("video_gen")[0]
        assert R.arm_id_for(thumb) == "hook_thumbnail_model__flux"
        assert R.arm_id_for(video) == "video_backfill_model__pruna-p-video"

    def test_rotation_uses_registration_order_not_cost_order(self):
        """`for_kind` sorts by cost; the rotation must not.

        If the rotation ever read the cost-sorted view, every blueprint would
        re-map to a different model and 48 h reward would join to the wrong
        arm — silently, because both return a valid Capability.
        """
        reg = R.in_registration_order("thumbnail")
        cost = R.for_kind("thumbnail")
        assert reg[0].model_id == "flux"
        assert [c.ref for c in reg] != [c.ref for c in cost] or len(reg) == 1, (
            "this pin is vacuous if the two orders happen to coincide"
        )
