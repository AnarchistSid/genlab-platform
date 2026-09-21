"""Sum a plan's generation cost and refuse it before the first call.

Part 32 §1. A plan names the calls it intends to make; this prices them from
the registry's MEASURED costs and rejects the whole plan if the total clears
the niche's cap. The point is that nothing is spent finding out.

This is only possible because the costs are billed figures. The catalog could
never have backed a cap: ``topaz/astra`` quotes "$0.0714/credit" (not a unit),
and ``pixverse/extend`` quotes a range spanning 4.6x. You cannot sum a range.

Measured 2026-09-21, an anime STILL reel:

    1 x tts    $0.00154
    7 x still  $0.00700
    1 x card   $0.04500
    1 x music  $0.01250
    ---------------------
               $0.06604   against a 0.25 cap

Seven cents. The STILL path was always the cheapest place to prove the
renderer, and now that is a number rather than an intuition.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from genlab_core.capabilities.registry import Capability, Unselectable, select


class PlanOverBudget(RuntimeError):
    """The plan's summed generation cost clears the niche cap."""


@dataclass(frozen=True)
class PlannedCall:
    """One intended capability call. ``count`` is how many of that kind."""

    kind: str
    count: int = 1

    def __post_init__(self) -> None:
        if self.count < 0:
            raise ValueError(f"count must be >= 0, got {self.count}")


@dataclass(frozen=True)
class PlanCost:
    total_usd: float
    per_kind: dict[str, float]
    chosen: dict[str, str]
    unpriced: tuple[str, ...]

    def __str__(self) -> str:
        rows = ", ".join(f"{k}={v:.5f}" for k, v in sorted(self.per_kind.items()))
        return f"${self.total_usd:.5f} ({rows})"


def price_plan(calls: Iterable[PlannedCall], *, context: str = "fire") -> PlanCost:
    """What this plan will cost, from measured figures only.

    A kind that cannot be selected in this context is returned in
    ``unpriced`` rather than silently costed at zero — a plan containing a
    capability we cannot actually use is not a cheap plan, it is a broken one.
    """
    merged: Counter[str] = Counter()
    for c in calls:
        merged[c.kind] += c.count

    per_kind: dict[str, float] = {}
    chosen: dict[str, str] = {}
    unpriced: list[str] = []
    for kind, n in merged.items():
        if n == 0:
            continue
        try:
            cap: Capability = select(kind, context=context)
        except Unselectable:
            unpriced.append(kind)
            continue
        per_kind[kind] = round((cap.cost_per_unit_usd or 0.0) * n, 6)
        chosen[kind] = cap.ref
    return PlanCost(
        total_usd=round(sum(per_kind.values()), 6),
        per_kind=per_kind,
        chosen=chosen,
        unpriced=tuple(sorted(unpriced)),
    )


def check_plan_budget(
    calls: Iterable[PlannedCall],
    *,
    cap_usd: float | None,
    context: str = "fire",
) -> PlanCost:
    """Price the plan and raise before anything is spent.

    ``cap_usd`` of ``None`` means the niche declares no cap; the plan is
    priced and allowed. That is deliberate — a missing cap must not silently
    become zero, which would reject every plan and read as "generation is
    broken" rather than "this niche has no budget configured".
    """
    cost = price_plan(calls, context=context)
    if cost.unpriced:
        raise Unselectable(
            f"plan names kinds with no selectable entry in context {context!r}: "
            f"{list(cost.unpriced)}"
        )
    if cap_usd is not None and cost.total_usd > cap_usd:
        raise PlanOverBudget(
            f"plan_over_budget:{cost.total_usd:.5f} > {cap_usd:.5f} — {cost}"
        )
    return cost
