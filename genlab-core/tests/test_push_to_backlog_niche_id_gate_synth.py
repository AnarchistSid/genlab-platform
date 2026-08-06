"""Pin test for QB-FIX-02 V3 — push_to_backlog's pre-write
auto_approval_gate call must propagate niche_id.

Prior bug (Yankee blueprint 2026-08-06): the synthetic blueprint
dict built at push_to_backlog.py:3064 for computing
auto_approval_confidence omitted niche_id. The DB write below
correctly stamped niche_id, but the pre-write gate saw
`niche_id or "unknown"` and its LLM judge reasoned about a phantom
tenant. Log line for the bug:

  [gate] LLM judge fired for niche=unknown rule_decision=False
  rule_conf=0.40 llm_decision=False reason=zero virality score
  and unknown niche make this too risky despite passing other checks

Source pin. A full integration test would need Flask + Postgres +
LLM API mocks. The wire is 1 line; the source assertion catches
every plausible regression at zero runtime cost.
"""

from __future__ import annotations

from pathlib import Path


def test_synth_blueprint_for_gate_evaluate_includes_niche_id():
    """The _synth dict built at push_to_backlog for _aag_evaluate
    must include a niche_id key sourced from either fields or the
    stage-scope niche_id variable.

    Detection heuristic: after the `_synth = {` marker preceding
    `_aag_evaluate(_synth)`, verify a `"niche_id"` key is present
    in the dict literal before the closing brace.
    """
    import genlab_core.pipeline.stages.push_to_backlog as mod

    src = Path(mod.__file__).read_text()

    # Find the _synth = { ... } literal that immediately precedes
    # `_aag_evaluate(_synth)`. There may be other _synth dicts in
    # the file; this test targets the one for the gate call.
    marker = "_decision = _aag_evaluate(_synth)"
    assert marker in src, (
        "Anchor line missing — refactor may have moved the gate call. "
        "Update this pin test if the auto-approval-confidence pre-write "
        "site relocates."
    )
    prefix = src[: src.index(marker)]
    # Walk backward from marker to the nearest _synth = { open brace
    synth_open = prefix.rfind("_synth = {")
    assert synth_open >= 0, "Could not locate _synth dict opening for the gate call"
    synth_body = src[synth_open : src.index(marker)]

    assert '"niche_id"' in synth_body, (
        "push_to_backlog._synth for auto_approval_gate must include niche_id. "
        "Without it the gate's LLM judge sees niche=unknown and applies "
        "ai_creators default thresholds to movies/sports/anime/gaming rows. "
        "QB-FIX-02 V3."
    )
