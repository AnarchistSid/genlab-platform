"""Pin R-19: ``virality_score`` + ``virality_features`` flow into the
blueprint ``fields`` dict.

Bug history: ``ViralityScoring.execute()`` (see
``pipeline/stages/virality_scoring.py:138``) writes
``story["virality_score"]`` + ``story["virality_features"]`` on every
run, but ``PushToBacklog`` previously omitted both from its
blueprint ``fields`` dict (``push_to_backlog.py:~1078–1140``). The
score was computed → persisted to the story dict (which is
ephemeral run state) → discarded when the blueprint was created. It
never reached ``priority_score`` and the dashboard's "why was this
ranked?" tab showed nothing.

The fix is a one-line copy at the right place. These tests pin the
contract by reading the source file directly — fast (no Postgres /
backlog client needed) and brittle to the regression we care about.
A future edit that drops either field will fail CI immediately.
"""

from __future__ import annotations

import re
from pathlib import Path

PUSH_TO_BACKLOG_SOURCE = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "genlab_core"
    / "pipeline"
    / "stages"
    / "push_to_backlog.py"
)


def test_fields_dict_carries_virality_score() -> None:
    """The blueprint ``fields`` dict must include a ``virality_score``
    key that defaults to ``0.0`` so the column lights up even when
    ``ViralityScoring`` couldn't compute one."""
    text = PUSH_TO_BACKLOG_SOURCE.read_text()
    # Match the exact field-binding pattern used elsewhere in the file:
    # `"virality_score": story.get("virality_score", 0.0),`
    pattern = re.compile(r'"virality_score":\s*story\.get\(\s*"virality_score"\s*,\s*0\.0\s*\)')
    assert pattern.search(text), (
        "R-19 regression: push_to_backlog.py's blueprint fields dict "
        "no longer carries `virality_score`. The score is computed in "
        "virality_scoring.py:138 but won't reach the blueprint without "
        "this line."
    )


def test_fields_dict_carries_virality_features() -> None:
    """The signal-breakdown dict that the dashboard explainer reads.
    Stored as JSON-encoded text so it works on either backend."""
    text = PUSH_TO_BACKLOG_SOURCE.read_text()
    # The features dict is serialised with json.dumps so the
    # SharePoint text column + Postgres jsonb column both accept it.
    pattern = re.compile(
        r'"virality_features":\s*json\.dumps\(\s*story\.get\(\s*"virality_features"\s*,\s*\{\s*\}\s*\)\s*\)'
    )
    assert pattern.search(text), (
        "R-19 regression: push_to_backlog.py's blueprint fields dict "
        "no longer carries `virality_features`. The dashboard's "
        "ranking-explainer tab depends on this signal breakdown."
    )


def test_virality_keys_are_in_the_same_fields_block_as_priority_score() -> None:
    """A defensive structural test — if a refactor splits the field set
    into pieces, virality_score should stay in the same block as
    priority_score (the same blueprint write). Otherwise persistence
    would happen as a second UPDATE after the row is already created,
    which is brittle."""
    text = PUSH_TO_BACKLOG_SOURCE.read_text()
    # Find the line index of priority_score and virality_score.
    lines = text.splitlines()
    pri_idx = next(
        (i for i, line in enumerate(lines) if '"priority_score":' in line),
        None,
    )
    vir_idx = next(
        (i for i, line in enumerate(lines) if '"virality_score":' in line),
        None,
    )
    assert pri_idx is not None, "priority_score line not found"
    assert vir_idx is not None, "virality_score line not found"
    # They should be within ~50 lines of each other (same fields dict).
    assert abs(pri_idx - vir_idx) < 50, (
        f"priority_score (line {pri_idx + 1}) and virality_score "
        f"(line {vir_idx + 1}) are far apart — they should live in "
        f"the same blueprint fields dict so a single create call "
        f"persists both."
    )
