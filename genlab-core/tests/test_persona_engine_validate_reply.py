"""Pin: PersonaEngine.validate_reply (LLM-as-judge for reply quality).

The 2026-06-22 autonomy audit found auto-replies are the last-mile of
unattended operation but were never self-validated. ``generate_reply``
only checks toxicity; semantic mismatches (off-topic, wrong-voice,
generic) flow straight to the platform. One bad auto-reply can damage
channel reputation.

This judge adds an opt-in LLM-as-judge pass that scores 0-100. When
enabled via ``GENLAB_REPLY_CRITIC_ENABLED=1``, the comment_processor
uses the judge's score to downgrade confidence so borderline replies
route to the review queue instead of auto-posting.

These pins guard:
- Parse contract: "SCORE: NN | REASON: ..." format → (score, reason)
- Fail-OPEN on circuit-open (don't freeze engagement on transient API)
- Fail to NEUTRAL on parse error (route to review, not discard)
- Clamps to [0,1] regardless of LLM output ranges
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_engine_with_response(text: str):
    """Build a PersonaEngine with a mocked Anthropic client."""
    from genlab_core.engagement.persona_engine import PersonaEngine
    from genlab_core.engagement.persona_schema import NichePersona

    # Minimal persona — fields default per persona_schema.py
    persona = NichePersona(name="Gaming Persona")

    engine = PersonaEngine(persona=persona, toxicity_gate=MagicMock())

    fake_client = MagicMock()
    fake_resp = MagicMock()
    fake_resp.content = [MagicMock(text=text)]
    fake_client.messages.create.return_value = fake_resp
    engine._client = fake_client
    return engine


class TestValidateReply:
    def test_parses_high_score(self):
        """Pin: SCORE 95 → score=0.95 + reason text captured."""
        with patch("genlab_core.intelligence.cost_accumulator.record_anthropic_usage"):
            engine = _make_engine_with_response(
                "SCORE: 95 | REASON: directly addresses the play call-out"
            )
            score, reason = engine.validate_reply(
                comment="GG", reply="Nice play!", platform="instagram"
            )
        assert abs(score - 0.95) < 0.001
        assert "directly addresses" in reason

    def test_parses_low_score(self):
        """Pin: SCORE 30 → score=0.30 (will route to discard via
        classify_reply_action when used as confidence)."""
        with patch("genlab_core.intelligence.cost_accumulator.record_anthropic_usage"):
            engine = _make_engine_with_response("SCORE: 30 | REASON: generic and off-topic")
            score, _ = engine.validate_reply(
                comment="what's your setup?", reply="cool!", platform="instagram"
            )
        assert abs(score - 0.30) < 0.001

    def test_clamps_above_100(self):
        """Pin: LLM hallucinating SCORE 150 → clamped to 1.0. Guards
        against garbage output destabilizing routing."""
        with patch("genlab_core.intelligence.cost_accumulator.record_anthropic_usage"):
            engine = _make_engine_with_response("SCORE: 150 | REASON: anything")
            score, _ = engine.validate_reply(comment="x", reply="y", platform="instagram")
        assert score == 1.0

    def test_clamps_below_zero(self):
        """Pin: LLM SCORE -10 → clamped to 0.0."""
        with patch("genlab_core.intelligence.cost_accumulator.record_anthropic_usage"):
            engine = _make_engine_with_response("SCORE: -10 | REASON: weird")
            score, _ = engine.validate_reply(comment="x", reply="y", platform="instagram")
        assert score == 0.0

    def test_unparseable_returns_neutral(self):
        """Pin: response without SCORE: NN → neutral 0.5 (routes to
        review queue, not discard). Fail-to-neutral preserves operator
        oversight without freezing the pipeline."""
        with patch("genlab_core.intelligence.cost_accumulator.record_anthropic_usage"):
            engine = _make_engine_with_response("I'm not sure what to say")
            score, reason = engine.validate_reply(comment="x", reply="y", platform="instagram")
        assert score == 0.5
        assert reason == "judge_parse_error"

    def test_circuit_open_fails_open(self):
        """Pin: when ANTHROPIC_CB is open (transient API outage), judge
        returns (1.0, "judge_unavailable") — fail-OPEN so engagement
        keeps moving. Fail-closed (0.0) would freeze all auto-replies
        for the duration of the outage."""
        from genlab_core.http.circuit_breaker import CircuitOpenError

        with patch("genlab_core.intelligence.cost_accumulator.record_anthropic_usage"):
            engine = _make_engine_with_response("SCORE: 80 | REASON: ok")
            with patch(
                "genlab_core.engagement.persona_engine.ANTHROPIC_CB.call",
                side_effect=CircuitOpenError("test"),
            ):
                score, reason = engine.validate_reply(comment="x", reply="y", platform="instagram")
        assert score == 1.0
        assert reason == "judge_unavailable"

    def test_exception_returns_neutral(self):
        """Pin: any unexpected exception → neutral 0.5 (routes to
        review). Engagement pipeline MUST NEVER crash on critic
        failures."""
        with patch("genlab_core.intelligence.cost_accumulator.record_anthropic_usage"):
            engine = _make_engine_with_response("SCORE: 80 | REASON: ok")
            with patch(
                "genlab_core.engagement.persona_engine.ANTHROPIC_CB.call",
                side_effect=RuntimeError("unexpected"),
            ):
                score, reason = engine.validate_reply(comment="x", reply="y", platform="instagram")
        assert score == 0.5
        assert reason == "judge_exception"


class TestCommentProcessorIntegration:
    """Pin: comment_processor wires the judge correctly — opt-in via
    env, downgrade confidence on low judge score."""

    def test_judge_disabled_by_default_not_called(self, monkeypatch):
        """Pin: GENLAB_REPLY_CRITIC_ENABLED unset → judge NEVER called.
        Default-off is critical for safe shadow rollout."""
        monkeypatch.delenv("GENLAB_REPLY_CRITIC_ENABLED", raising=False)
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "genlab_core"
            / "engagement"
            / "comment_processor.py"
        )
        content = src.read_text()
        # Source-level pin: the env-gate must be present via the
        # canonical env_true() helper (rolled out in commit aa11be6d
        # replacing the direct os.environ.get(...) == "1" pattern).
        # The gate remains opt-in — env_true() returns True only for
        # {"1", "true", "yes", "on"} (case-insensitive).
        assert 'env_true("GENLAB_REPLY_CRITIC_ENABLED")' in content, (
            "comment_processor.py is missing the GENLAB_REPLY_CRITIC_ENABLED "
            "env gate around the judge call (env_true helper form). "
            "Critic must be opt-in."
        )

    def test_judge_failure_does_not_crash_pipeline(self):
        """Pin: source-level — the judge call is wrapped in try/except
        with `noqa: BLE001 — never block on critic`. Engagement
        pipeline MUST NEVER crash on critic exceptions."""
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "genlab_core"
            / "engagement"
            / "comment_processor.py"
        )
        content = src.read_text()
        assert "never block on critic" in content, (
            "comment_processor.py is missing the never-block-on-critic guard."
        )

    def test_judge_downgrades_confidence_not_replaces(self):
        """Pin: confidence = min(confidence, judge_score) — judge can
        only LOWER confidence, never RAISE it. Heuristic confidence is
        the upper bound; judge is the optional further filter.

        This matters because the heuristic catches platform-specific
        signals (length, question detection) the LLM doesn't see;
        replacing the value would lose those signals."""
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "genlab_core"
            / "engagement"
            / "comment_processor.py"
        )
        content = src.read_text()
        assert "min(confidence, judge_score)" in content, (
            "comment_processor.py must use min(confidence, judge_score) — "
            "judge can only LOWER confidence, never raise."
        )
