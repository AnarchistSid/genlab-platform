"""Pin: click-rationale taxonomy classifier (Engine 1.4).

Locks the contract for ``genlab_core.learning.rationale_classifier``:

* Opt-in via ``GENLAB_RATIONALE_CLASSIFIER_ENABLED`` — default OFF
  preserves today's behaviour (NULL feedback_category).
* Returns ``(category, confidence)`` only when Haiku confidence is at
  or above the threshold AND category is in the canonical 6-set.
* All failure paths collapse to ``("uncategorized", 0.0)`` so the
  calibration writer NEVER blocks on classification errors.
* Niche name flows through to the prompt — required for the
  bad_fit / niche-convention judgements.

See docs/AGENT-LEARNING-ENGINES.md §1.4 for the engine rationale.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _blueprint() -> dict:
    """Reference blueprint shape — matches what _execute_review_action
    flattens before calling the classifier."""
    return {
        "id": "00000000-0000-0000-0000-000000000001",
        "hook_text": "AI lab just dropped a wild new model",
        "story_summary": (
            "OpenAI announced a new model today that reportedly outperforms "
            "GPT-5 on coding benchmarks. The release page shipped without "
            "benchmark numbers and the model card is empty."
        ),
        "niche_id": "ai_creators",
    }


def _mock_haiku(text: str) -> MagicMock:
    """Build a fake anthropic client whose .messages.create() returns
    a response with ``content[0].text == text``."""
    fake_resp = MagicMock()
    fake_resp.content = [MagicMock(text=text)]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_resp
    return fake_client


class TestEnvFlagOff:
    """The env flag is the master switch. When unset, no Haiku call
    happens AND the classifier returns the sentinel — exactly the
    behaviour the calibration writer needs to see to leave the
    feedback_category column NULL."""

    def test_no_op_when_flag_unset(self, monkeypatch):
        from genlab_core.learning import rationale_classifier

        monkeypatch.delenv("GENLAB_RATIONALE_CLASSIFIER_ENABLED", raising=False)
        # Even with an API key present, the env flag gates everything.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

        with patch("anthropic.Anthropic") as mocked_client:
            category, confidence = rationale_classifier.classify_rejection(
                _blueprint(), "ai_creators"
            )

        assert category == rationale_classifier.UNCATEGORIZED
        assert confidence == 0.0
        # Most important assertion: Haiku is NEVER invoked when the
        # env flag is off. Cost stays at $0 for the default config.
        mocked_client.assert_not_called()

    def test_no_op_when_flag_is_zero(self, monkeypatch):
        from genlab_core.learning import rationale_classifier

        monkeypatch.setenv("GENLAB_RATIONALE_CLASSIFIER_ENABLED", "0")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

        with patch("anthropic.Anthropic") as mocked_client:
            category, confidence = rationale_classifier.classify_rejection(
                _blueprint(), "ai_creators"
            )

        assert category == rationale_classifier.UNCATEGORIZED
        assert confidence == 0.0
        mocked_client.assert_not_called()


class TestClassificationBehaviour:
    """All these tests run with the flag enabled + a fake API key."""

    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        monkeypatch.setenv("GENLAB_RATIONALE_CLASSIFIER_ENABLED", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

    def test_returns_category_when_haiku_returns_valid_json(self):
        from genlab_core.learning import rationale_classifier

        fake_client = _mock_haiku(
            '{"category": "weak_hook", "confidence": 0.88, '
            '"rationale": "Hook uses vague language"}'
        )
        with patch("anthropic.Anthropic", return_value=fake_client):
            category, confidence = rationale_classifier.classify_rejection(
                _blueprint(), "ai_creators"
            )

        assert category == "weak_hook"
        assert confidence == pytest.approx(0.88)

    def test_returns_uncategorized_when_confidence_below_threshold(self):
        """Confidence 0.5 < default 0.7 → classifier withholds the
        verdict so the calibration column stays NULL. Operator can
        still pick a category manually."""
        from genlab_core.learning import rationale_classifier

        fake_client = _mock_haiku(
            '{"category": "too_generic", "confidence": 0.5, '
            '"rationale": "Maybe template-y"}'
        )
        with patch("anthropic.Anthropic", return_value=fake_client):
            category, confidence = rationale_classifier.classify_rejection(
                _blueprint(), "ai_creators"
            )

        assert category == rationale_classifier.UNCATEGORIZED
        assert confidence == 0.0

    def test_fail_open_when_haiku_api_errors(self):
        """anthropic.Anthropic() raises — should NOT bubble up. Without
        this guard, calibration writes would fail on every Haiku outage
        and the auto_approval_calibration table would stop accumulating."""
        from genlab_core.learning import rationale_classifier

        with patch("anthropic.Anthropic", side_effect=RuntimeError("simulated outage")):
            category, confidence = rationale_classifier.classify_rejection(
                _blueprint(), "ai_creators"
            )

        assert category == rationale_classifier.UNCATEGORIZED
        assert confidence == 0.0

    def test_fail_open_when_haiku_returns_invalid_json(self):
        """Haiku free-text response → parser bails → sentinel. The
        calibration table never sees junk data."""
        from genlab_core.learning import rationale_classifier

        fake_client = _mock_haiku("I don't know how to categorize this.")
        with patch("anthropic.Anthropic", return_value=fake_client):
            category, confidence = rationale_classifier.classify_rejection(
                _blueprint(), "ai_creators"
            )

        assert category == rationale_classifier.UNCATEGORIZED
        assert confidence == 0.0

    def test_returns_one_of_canonical_six_categories(self):
        """Whitelist enforcement: if Haiku invents a category that isn't
        in the canonical 6-set, it gets downgraded to the sentinel.
        Downstream aggregations (per-category breakdown endpoint) can
        rely on the enum being closed."""
        from genlab_core.learning import rationale_classifier

        fake_client = _mock_haiku(
            '{"category": "user_demographic_mismatch", "confidence": 0.95, '
            '"rationale": "made-up category"}'
        )
        with patch("anthropic.Anthropic", return_value=fake_client):
            category, _ = rationale_classifier.classify_rejection(
                _blueprint(), "ai_creators"
            )

        assert category == rationale_classifier.UNCATEGORIZED

        # Now the positive path — every canonical category survives
        # the whitelist gate.
        canonical = {
            "weak_hook",
            "too_generic",
            "unsupported_claim",
            "bad_fit",
            "too_long",
            "low_value",
        }
        assert canonical == set(rationale_classifier.CANONICAL_CATEGORIES)
        for valid in canonical:
            fake = _mock_haiku(
                f'{{"category": "{valid}", "confidence": 0.9, "rationale": "x"}}'
            )
            with patch("anthropic.Anthropic", return_value=fake):
                cat, _ = rationale_classifier.classify_rejection(
                    _blueprint(), "ai_creators"
                )
            assert cat == valid
            assert cat in rationale_classifier.CANONICAL_CATEGORIES

    def test_includes_niche_name_in_prompt(self):
        """The niche flows through to Haiku — required for the bad_fit
        judgement to know what "off-niche" means for THIS channel.
        We inspect the user message that was sent to messages.create()."""
        from genlab_core.learning import rationale_classifier

        fake_client = _mock_haiku(
            '{"category": "bad_fit", "confidence": 0.9, "rationale": "x"}'
        )
        with patch("anthropic.Anthropic", return_value=fake_client):
            rationale_classifier.classify_rejection(_blueprint(), "anime")

        # Inspect the call. The user message is JSON-shaped per
        # _build_user_message; the niche string must appear verbatim.
        fake_client.messages.create.assert_called_once()
        kwargs = fake_client.messages.create.call_args.kwargs
        user_messages = kwargs.get("messages") or []
        assert user_messages, "messages.create called without a messages kwarg"
        user_content = user_messages[0]["content"]
        assert "anime" in user_content, (
            f"niche_id 'anime' must appear in prompt, got: {user_content!r}"
        )

    def test_includes_hook_and_summary_in_prompt(self):
        """Adjacent contract: hook + summary fragments are passed to
        Haiku. Without them the classifier has nothing to reason over."""
        from genlab_core.learning import rationale_classifier

        fake_client = _mock_haiku(
            '{"category": "weak_hook", "confidence": 0.9, "rationale": "x"}'
        )
        with patch("anthropic.Anthropic", return_value=fake_client):
            rationale_classifier.classify_rejection(_blueprint(), "ai_creators")

        kwargs = fake_client.messages.create.call_args.kwargs
        user_content = kwargs["messages"][0]["content"]
        assert "AI lab just dropped a wild new model" in user_content
        assert "OpenAI announced" in user_content

    def test_no_api_key_returns_uncategorized(self, monkeypatch):
        """CI / local dev path: ANTHROPIC_API_KEY missing → sentinel,
        no SDK import attempted."""
        from genlab_core.learning import rationale_classifier

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        category, confidence = rationale_classifier.classify_rejection(
            _blueprint(), "ai_creators"
        )
        assert category == rationale_classifier.UNCATEGORIZED
        assert confidence == 0.0

    def test_confidence_threshold_env_override(self, monkeypatch):
        """Operator can relax / tighten the threshold via env. With
        threshold=0.3, a 0.5-confidence verdict is accepted."""
        from genlab_core.learning import rationale_classifier

        monkeypatch.setenv("GENLAB_RATIONALE_CONFIDENCE_THRESHOLD", "0.3")
        fake_client = _mock_haiku(
            '{"category": "too_generic", "confidence": 0.5, "rationale": "x"}'
        )
        with patch("anthropic.Anthropic", return_value=fake_client):
            category, confidence = rationale_classifier.classify_rejection(
                _blueprint(), "ai_creators"
            )
        assert category == "too_generic"
        assert confidence == pytest.approx(0.5)

    def test_handles_markdown_fenced_json(self):
        """Haiku occasionally wraps JSON in ```json fences. Parser
        should strip them — same pattern as post_rca."""
        from genlab_core.learning import rationale_classifier

        fake_client = _mock_haiku(
            '```json\n{"category": "low_value", "confidence": 0.8, "rationale": "y"}\n```'
        )
        with patch("anthropic.Anthropic", return_value=fake_client):
            category, _ = rationale_classifier.classify_rejection(
                _blueprint(), "ai_creators"
            )
        assert category == "low_value"

    def test_blueprint_with_nested_fields_shape(self):
        """SharePoint backend returns ``{"id": ..., "fields": {...}}``
        — classifier should pull hook/summary from ``fields`` too."""
        from genlab_core.learning import rationale_classifier

        nested = {
            "id": "00000000-0000-0000-0000-000000000002",
            "fields": {
                "hook_text": "Sports highlight from last night",
                "story_summary": "A buzzer beater that won the game",
            },
        }
        fake_client = _mock_haiku(
            '{"category": "weak_hook", "confidence": 0.9, "rationale": "x"}'
        )
        with patch("anthropic.Anthropic", return_value=fake_client):
            rationale_classifier.classify_rejection(nested, "sports")
        kwargs = fake_client.messages.create.call_args.kwargs
        user_content = kwargs["messages"][0]["content"]
        assert "Sports highlight from last night" in user_content
        assert "buzzer beater" in user_content
