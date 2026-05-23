"""R-75: auto/review/discard routing must use the toxicity of the GENERATED
REPLY (outbound), not the inbound comment — otherwise a clean comment could
auto-post a toxic reply. Also covers ToxicityGate.check_outbound (fail-closed)
and the de-banned gaming persona.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import yaml
from genlab_core.engagement.toxicity_gate import ToxicityGate


def _make_event(**overrides):
    event = {
        "comment_id": "c123",
        "comment_text": "This is a great clip!",
        "platform": "youtube",
        "niche_id": "gaming",
        "post_id": "p456",
        "post_context": "",
    }
    event.update(overrides)
    return event


# ── ToxicityGate.check_outbound ──────────────────────────────────────


def test_check_outbound_scores_clean_reply() -> None:
    gate = ToxicityGate()
    gate._model = MagicMock()
    gate._model.predict.return_value = {"toxicity": 0.02, "insult": 0.01}
    result = gate.check_outbound("Thanks, glad you enjoyed it!")
    assert result.is_toxic is False
    assert result.max_score == 0.02


def test_check_outbound_flags_toxic_reply() -> None:
    gate = ToxicityGate()
    gate._model = MagicMock()
    gate._model.predict.return_value = {"toxicity": 0.9, "insult": 0.8}
    result = gate.check_outbound("something nasty")
    assert result.is_toxic is True
    assert result.max_score == 0.9


def test_check_outbound_fails_closed_on_model_error() -> None:
    gate = ToxicityGate()
    gate._model = MagicMock()
    gate._model.predict.side_effect = RuntimeError("model exploded")
    result = gate.check_outbound("anything")
    # Fail-CLOSED: a model error must NOT let a reply through.
    assert result.is_toxic is True
    assert result.max_score == 1.0


def test_is_clean_outbound_delegates() -> None:
    gate = ToxicityGate()
    gate._model = MagicMock()
    gate._model.predict.return_value = {"toxicity": 0.05}
    assert gate.is_clean_outbound("clean reply") is True
    gate._model.predict.return_value = {"toxicity": 0.5}
    assert gate.is_clean_outbound("toxic reply") is False


# ── routing uses OUTBOUND toxicity ───────────────────────────────────


@patch("genlab_core.engagement.comment_processor.human_delay", return_value=0)
@patch("genlab_core.engagement.comment_processor.PersonaEngine")
@patch("genlab_core.engagement.comment_processor.is_spam", return_value=False)
@patch("genlab_core.engagement.comment_processor._has_replied", return_value=False)
@patch("genlab_core.engagement.comment_processor._rate_limiter")
def test_toxic_outbound_reply_is_discarded_even_for_clean_comment(
    mock_rl, mock_replied, mock_spam, mock_engine, mock_delay, monkeypatch
) -> None:
    from genlab_core.engagement import comment_processor as cp

    mock_rl.acquire.return_value = True
    mock_engine.return_value.generate_reply.return_value = "a generated reply"

    # Clean inbound comment, but the generated reply scores TOXIC outbound.
    fake_gate = MagicMock()
    fake_gate.check_inbound.return_value = SimpleNamespace(
        is_toxic=False, max_score=0.01, max_dimension="toxicity", all_scores={}
    )
    fake_gate.check_outbound.return_value = SimpleNamespace(
        is_toxic=True, max_score=0.95, max_dimension="toxicity", all_scores={}
    )

    monkeypatch.setattr(cp, "_toxicity_gate", fake_gate)
    monkeypatch.setattr(cp, "_get_backlog_client", lambda: None)
    monkeypatch.setattr(
        cp,
        "_load_persona",
        lambda nid: SimpleNamespace(reply_constraints=SimpleNamespace(max_length_chars=200)),
    )

    with (
        patch.object(cp, "_post_reply") as mock_post,
        patch.object(cp, "_queue_for_review") as mock_review,
        patch.object(cp, "_mark_replied"),
    ):
        cp.process_reply_event(_make_event(comment_id="rt1"))

    # Outbound tox 0.95 >= 0.3 -> discard. Had routing used the inbound 0.01,
    # this would have gone to review/auto instead. So: neither posted nor queued.
    fake_gate.check_outbound.assert_called_once()
    mock_post.assert_not_called()
    mock_review.assert_not_called()


# ── persona cleanup (R-75) ───────────────────────────────────────────


def test_gaming_persona_has_no_banned_superlatives() -> None:
    persona_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "genlab_core"
        / "engagement"
        / "personas"
        / "gaming.yaml"
    )
    data = yaml.safe_load(persona_path.read_text())
    examples = " ".join(data.get("style_examples", [])).lower()
    for banned in ("insane", "literally", "godlike"):
        assert banned not in examples, f"banned AI-tell word '{banned}' in gaming persona examples"
