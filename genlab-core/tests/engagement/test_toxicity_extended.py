"""Extended toxicity gate tests -- threshold behavior, model loading, and edge cases."""
from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from genlab_core.engagement.toxicity_gate import ToxicityGate, ToxicityResult


class TestToxicityThresholds:
    def test_inbound_threshold_is_0_7(self):
        assert ToxicityGate.INBOUND_THRESHOLD == 0.7

    def test_outbound_threshold_is_0_3(self):
        assert ToxicityGate.OUTBOUND_THRESHOLD == 0.3

    def test_outbound_stricter_than_inbound(self):
        """Outbound must be stricter (lower threshold) than inbound."""
        assert ToxicityGate.OUTBOUND_THRESHOLD < ToxicityGate.INBOUND_THRESHOLD


class TestInboundCheck:
    def test_clean_text_passes_inbound(self):
        gate = ToxicityGate()
        gate._model = MagicMock()
        gate._model.predict.return_value = {
            "toxicity": 0.01, "severe_toxicity": 0.001,
            "obscene": 0.005, "identity_attack": 0.002,
            "insult": 0.01, "threat": 0.001,
        }
        result = gate.check_inbound("I love this video!")
        assert not result.is_toxic
        assert result.max_score < 0.1

    def test_toxic_text_fails_inbound(self):
        gate = ToxicityGate()
        gate._model = MagicMock()
        gate._model.predict.return_value = {
            "toxicity": 0.95, "severe_toxicity": 0.8,
            "obscene": 0.9, "identity_attack": 0.1,
            "insult": 0.85, "threat": 0.05,
        }
        result = gate.check_inbound("test toxic content")
        assert result.is_toxic

    def test_borderline_below_threshold_passes(self):
        """Toxicity at exactly 0.7 should NOT be toxic (> 0.7 required)."""
        gate = ToxicityGate()
        gate._model = MagicMock()
        gate._model.predict.return_value = {"toxicity": 0.7, "insult": 0.3}
        result = gate.check_inbound("borderline text")
        assert not result.is_toxic

    def test_borderline_above_threshold_fails(self):
        """Toxicity at 0.71 should be toxic."""
        gate = ToxicityGate()
        gate._model = MagicMock()
        gate._model.predict.return_value = {"toxicity": 0.71, "insult": 0.3}
        result = gate.check_inbound("slightly over")
        assert result.is_toxic

    def test_inbound_uses_toxicity_key_not_max(self):
        """Inbound check uses 'toxicity' key specifically, not max across all dimensions."""
        gate = ToxicityGate()
        gate._model = MagicMock()
        # insult is high but toxicity is low -- should pass inbound
        gate._model.predict.return_value = {"toxicity": 0.3, "insult": 0.9}
        result = gate.check_inbound("snarky but not toxic")
        assert not result.is_toxic

    def test_result_max_dimension_is_highest_scoring(self):
        """ToxicityResult.max_dimension should be the highest scoring dimension."""
        gate = ToxicityGate()
        gate._model = MagicMock()
        gate._model.predict.return_value = {
            "toxicity": 0.2,
            "insult": 0.6,
            "obscene": 0.1,
        }
        result = gate.check_inbound("text")
        assert result.max_dimension == "insult"
        assert result.max_score == 0.6

    def test_all_scores_are_preserved(self):
        """check_inbound should preserve all raw scores in the result."""
        gate = ToxicityGate()
        gate._model = MagicMock()
        scores = {"toxicity": 0.1, "insult": 0.2, "threat": 0.05}
        gate._model.predict.return_value = scores
        result = gate.check_inbound("text")
        assert result.all_scores == scores


class TestOutboundCheck:
    def test_clean_reply_passes_outbound(self):
        gate = ToxicityGate()
        gate._model = MagicMock()
        gate._model.predict.return_value = {
            "toxicity": 0.01, "insult": 0.02, "obscene": 0.01,
        }
        assert gate.is_clean_outbound("Thanks for watching!")

    def test_mildly_toxic_reply_fails_outbound(self):
        """Even moderate toxicity should fail outbound (threshold 0.3)."""
        gate = ToxicityGate()
        gate._model = MagicMock()
        gate._model.predict.return_value = {
            "toxicity": 0.1, "insult": 0.35, "obscene": 0.05,
        }
        assert not gate.is_clean_outbound("snarky reply")

    def test_outbound_checks_all_dimensions(self):
        """If ANY dimension exceeds 0.3, outbound should fail."""
        gate = ToxicityGate()
        gate._model = MagicMock()
        gate._model.predict.return_value = {
            "toxicity": 0.1, "insult": 0.1, "threat": 0.31,
        }
        assert not gate.is_clean_outbound("reply with subtle threat")

    def test_outbound_at_exactly_threshold_passes(self):
        """All dimensions at exactly 0.3 should pass (<= 0.3)."""
        gate = ToxicityGate()
        gate._model = MagicMock()
        gate._model.predict.return_value = {
            "toxicity": 0.3, "insult": 0.3, "obscene": 0.3,
        }
        assert gate.is_clean_outbound("borderline reply")


class TestModelLoading:
    def test_model_starts_as_none(self):
        gate = ToxicityGate()
        assert gate._model is None

    def test_detoxify_unavailable_fails_open_inbound(self):
        """When model predict raises, inbound fails open (not toxic)."""
        gate = ToxicityGate()
        gate._model = MagicMock()
        gate._model.predict.side_effect = ImportError("detoxify not installed")
        result = gate.check_inbound("anything")
        assert not result.is_toxic
        assert result.max_dimension == "error"

    def test_detoxify_unavailable_fails_closed_outbound(self):
        """When model predict raises, outbound fails closed (not clean)."""
        gate = ToxicityGate()
        gate._model = MagicMock()
        gate._model.predict.side_effect = RuntimeError("model error")
        assert not gate.is_clean_outbound("anything")


class TestConvenienceWrapper:
    def test_is_toxic_inbound_delegates_to_check_inbound(self):
        """is_toxic_inbound returns the bool from check_inbound."""
        gate = ToxicityGate()
        gate._model = MagicMock()
        gate._model.predict.return_value = {"toxicity": 0.85, "insult": 0.1}
        assert gate.is_toxic_inbound("bad text") is True

    def test_is_toxic_inbound_false_for_clean(self):
        gate = ToxicityGate()
        gate._model = MagicMock()
        gate._model.predict.return_value = {"toxicity": 0.1, "insult": 0.05}
        assert gate.is_toxic_inbound("nice text") is False


class TestToxicityResultDataclass:
    def test_result_has_all_fields(self):
        r = ToxicityResult(
            is_toxic=True, max_dimension="toxicity",
            max_score=0.95, all_scores={"toxicity": 0.95},
        )
        assert r.is_toxic
        assert r.max_dimension == "toxicity"
        assert r.max_score == 0.95
        assert r.all_scores == {"toxicity": 0.95}

    def test_result_is_not_frozen(self):
        """ToxicityResult is a plain dataclass (not frozen)."""
        r = ToxicityResult(
            is_toxic=False, max_dimension="insult",
            max_score=0.1, all_scores={},
        )
        r.is_toxic = True
        assert r.is_toxic is True

    def test_error_result_has_empty_scores(self):
        """Error results should have empty scores dict."""
        r = ToxicityResult(
            is_toxic=False, max_dimension="error",
            max_score=0.0, all_scores={},
        )
        assert r.all_scores == {}
        assert r.max_dimension == "error"
