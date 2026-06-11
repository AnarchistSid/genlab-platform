"""R-70 part 2: pin the design-phase doc.

R-70 part 1 (PR #140) deleted 826 LOC of gaming legacy strategies
and explicitly deferred the base-class extraction half (visual_render
+ scoring across SR/CW/FD). Empirical measurement at session-#2 time
showed the divergence was 55% (visual_render) and 70% (scoring) —
not the audit-asserted "clean ~750L copy-paste" — so the right
closure was an honest design-phase doc, not a rushed extraction.

``docs/r70-part2-design-phase.md`` is that doc. This pin guards
against:

  * Someone deleting the doc without doing the extraction work.
  * Someone editing the divergence numbers out of the doc without
    re-measuring (the numbers are how the next contributor decides
    if the extraction approach still holds).
"""

from __future__ import annotations

from pathlib import Path

_GENLAB_ROOT = Path(__file__).resolve().parents[3]
_DESIGN_DOC = _GENLAB_ROOT / "docs" / "r70-part2-design-phase.md"


def test_r70_part2_design_doc_exists() -> None:
    """The doc captures the multi-PR extraction sequencing and the
    measured-divergence baseline. Deleting it without doing R-70
    part 2 loses that institutional knowledge."""
    assert _DESIGN_DOC.is_file(), (
        f"R-70 part 2 regression: design-phase doc is missing at "
        f"{_DESIGN_DOC.relative_to(_GENLAB_ROOT)}. Either restore the "
        "doc, or do the extraction it sequenced and remove this pin "
        "as part of the R-70 part 2 closure PR."
    )


def test_r70_part2_design_doc_carries_measured_divergence_numbers() -> None:
    """The doc must declare the actual measured divergence
    percentages, because the extraction strategy depends on them.

    If a future contributor re-measures and finds the divergence has
    moved (e.g., a channel was rewritten and is now 30% divergent),
    the design needs revisiting — but only if the numbers are still
    visible in the doc to be compared against. A version that
    quietly drops the measurements is one that future contributors
    can't validate against the current state."""
    body = _DESIGN_DOC.read_text()
    # The two measured numbers MUST be present verbatim.
    assert "55%" in body or "~55%" in body, (
        "R-70 part 2 regression: design doc no longer cites the 55% "
        "visual_render divergence. Re-measure with "
        "``diff -u SpliceReel/sr_strategies/visual_render.py "
        "ClutchWire/cw_strategies/visual_render.py | grep -E '^[+-]' "
        "| grep -v '^[+-][+-]' | wc -l`` and update the doc + this pin."
    )
    assert "70%" in body or "~70%" in body, (
        "R-70 part 2 regression: design doc no longer cites the 70% "
        "scoring divergence. Same re-measure procedure as above, "
        "scoring.py instead of visual_render.py."
    )


def test_r70_part2_design_doc_sequences_pr_steps() -> None:
    """The doc must enumerate a PR sequence (PR 1, PR 2, …) so the
    next contributor knows how to chunk the work. An undated 'we
    should refactor someday' doc is what got us into the situation
    where R-70 part 1 was claimed-done-but-wasn't."""
    body = _DESIGN_DOC.read_text()
    # At least 3 explicit PR steps must be named.
    pr_step_count = sum(1 for n in range(1, 10) if f"PR {n}" in body or "PR 1" in body)
    assert pr_step_count >= 3, (
        "R-70 part 2 regression: design doc no longer enumerates "
        "PR-by-PR sequencing. The doc's value is in chunking the "
        "extraction so each PR is reviewable in isolation — without "
        "that, it's just a 'someday' note."
    )
