"""Pin the music_mood LLM-steer observability wire.

Wire behavior (this commit, observability-only):

  * When ``blueprint_context`` is passed AND the flag is on AND music_mood
    was picked AND context has hook/title/summary AND LLM returns a
    suggestion, emit ``[transform_selector] LLM_STEER music_mood ...``
    log line comparing bandit pick vs LLM pick.
  * Zero effect on selection outcome. TransformationChoices.choices
    is byte-identical with or without the wire firing.

Fail-open at every layer:

  * blueprint_context is None -> no LLM call, no log
  * music_mood was not picked -> no LLM call
  * suggest_mood returns None (any reason) -> no log
  * suggest_mood raises (should not per its contract) -> caught, no log

Future consumer wire (deferred, separate commit): decides whether to
promote to override / veto / prior-boost based on ~1-2wk of the log
data. Follow-up test file will pin that behavior.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from genlab_core.media.intelligent_transform import (
    IntelligentTransformConfig,
)
from genlab_core.media.music_mood_llm_fit import MoodSuggestion
from genlab_core.media.transformation_selector import (
    select_transformation_dimensions,
)


def _enabled_config_with_music(moods: list[str]) -> IntelligentTransformConfig:
    return IntelligentTransformConfig.from_visuals_dict(
        {
            "intelligent_transform": {
                "enabled": True,
                "dimensions": {
                    "music_mood": {"enabled": True, "moods": moods},
                },
            }
        }
    )


def _proxy_returning_arms(
    arms: dict[str, tuple[float, float]],
    niche_id: str = "gaming",
) -> MagicMock:
    proxy = MagicMock()
    proxy.all.return_value = [
        {
            "id": f"row_{i}",
            "fields": {
                "arm_id": arm_id,
                "alpha": alpha,
                "beta": beta,
                "niche_id": niche_id,
            },
        }
        for i, (arm_id, (alpha, beta)) in enumerate(arms.items())
    ]
    return proxy


class TestLLMSteerObservabilityWire:
    """The LLM steer is pure observation. Selection is byte-identical."""

    def _flag_on(self, monkeypatch):
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")

    def test_wire_calls_llm_when_context_provided(self, monkeypatch, caplog):
        self._flag_on(monkeypatch)
        cfg = _enabled_config_with_music(["hype", "chill", "dramatic"])
        proxy = _proxy_returning_arms(
            {
                "transform__music_mood__hype": (5.0, 1.0),
                "transform__music_mood__chill": (1.0, 1.0),
                "transform__music_mood__dramatic": (1.0, 1.0),
            }
        )
        with patch(
            "genlab_core.media.music_mood_llm_fit.suggest_mood",
            return_value=MoodSuggestion(
                top_mood="dramatic",
                reasoning="scene tension warrants dramatic scoring",
                confidence=0.85,
            ),
        ) as mock_suggest, caplog.at_level(logging.INFO):
            result = select_transformation_dimensions(
                "gaming",
                cfg,
                proxy=proxy,
                blueprint_context={
                    "hook": "The final boss appears",
                    "title": "Elden Ring - Malenia final phase",
                    "summary": "10-hour attempt culminating in phase-two victory",
                },
            )

        assert "music_mood" in result.choices
        # LLM was called with the available moods (sorted) + all context
        mock_suggest.assert_called_once()
        kwargs = mock_suggest.call_args.kwargs
        assert kwargs["niche_id"] == "gaming"
        assert kwargs["hook"] == "The final boss appears"
        assert kwargs["title"] == "Elden Ring - Malenia final phase"
        assert set(kwargs["available_moods"]) == {"hype", "chill", "dramatic"}
        # Log emitted for operator visibility
        assert any("LLM_STEER music_mood" in r.message for r in caplog.records)

    def test_wire_no_context_no_llm_call(self, monkeypatch):
        self._flag_on(monkeypatch)
        cfg = _enabled_config_with_music(["hype", "chill"])
        proxy = _proxy_returning_arms(
            {
                "transform__music_mood__hype": (5.0, 1.0),
                "transform__music_mood__chill": (1.0, 1.0),
            }
        )
        with patch(
            "genlab_core.media.music_mood_llm_fit.suggest_mood"
        ) as mock_suggest:
            result = select_transformation_dimensions(
                "gaming",
                cfg,
                proxy=proxy,
                blueprint_context=None,
            )
        assert "music_mood" in result.choices
        mock_suggest.assert_not_called()

    def test_wire_music_mood_not_picked_no_llm_call(self, monkeypatch):
        """If the config disables music_mood, LLM should not be called
        even when the flag + context are present."""
        self._flag_on(monkeypatch)
        cfg = IntelligentTransformConfig.from_visuals_dict(
            {
                "intelligent_transform": {
                    "enabled": True,
                    "dimensions": {
                        "caption_style": {
                            "enabled": True,
                            "styles": ["bold", "minimal"],
                        }
                    },
                }
            }
        )
        proxy = _proxy_returning_arms(
            {
                "transform__caption_style__bold": (5.0, 1.0),
                "transform__caption_style__minimal": (1.0, 1.0),
            }
        )
        with patch(
            "genlab_core.media.music_mood_llm_fit.suggest_mood"
        ) as mock_suggest:
            result = select_transformation_dimensions(
                "gaming",
                cfg,
                proxy=proxy,
                blueprint_context={
                    "hook": "something happened",
                    "title": "something",
                    "summary": "something else",
                },
            )
        assert "music_mood" not in result.choices
        mock_suggest.assert_not_called()

    def test_wire_llm_returns_none_no_log(self, monkeypatch, caplog):
        """LLM disabled / no API key / any failure => suggest_mood
        returns None => no log line emitted, but selection still succeeds."""
        self._flag_on(monkeypatch)
        cfg = _enabled_config_with_music(["hype", "chill"])
        proxy = _proxy_returning_arms(
            {
                "transform__music_mood__hype": (5.0, 1.0),
                "transform__music_mood__chill": (1.0, 1.0),
            }
        )
        with patch(
            "genlab_core.media.music_mood_llm_fit.suggest_mood",
            return_value=None,
        ), caplog.at_level(logging.INFO):
            result = select_transformation_dimensions(
                "gaming",
                cfg,
                proxy=proxy,
                blueprint_context={
                    "hook": "hook", "title": "title", "summary": "summary",
                },
            )
        assert "music_mood" in result.choices
        assert not any("LLM_STEER music_mood" in r.message for r in caplog.records)

    def test_wire_llm_raises_swallowed(self, monkeypatch):
        """suggest_mood promises no-raise, but a broken import path
        could still raise. The wire must swallow the exception so it
        never breaks the selector."""
        self._flag_on(monkeypatch)
        cfg = _enabled_config_with_music(["hype", "chill"])
        proxy = _proxy_returning_arms(
            {
                "transform__music_mood__hype": (5.0, 1.0),
                "transform__music_mood__chill": (1.0, 1.0),
            }
        )
        with patch(
            "genlab_core.media.music_mood_llm_fit.suggest_mood",
            side_effect=RuntimeError("boom"),
        ):
            # Should not raise
            result = select_transformation_dimensions(
                "gaming",
                cfg,
                proxy=proxy,
                blueprint_context={
                    "hook": "h", "title": "t", "summary": "s",
                },
            )
        assert "music_mood" in result.choices

    def test_wire_agree_flag_computed_correctly(self, monkeypatch, caplog):
        """The log line's agree=<bool> field reflects whether the LLM's
        suggested mood matches the bandit's actual pick — the metric
        the operator will grep on to decide override policy."""
        self._flag_on(monkeypatch)
        cfg = _enabled_config_with_music(["hype", "dramatic"])
        # Bandit will strongly prefer hype (5, 1) over dramatic (1, 5)
        proxy = _proxy_returning_arms(
            {
                "transform__music_mood__hype": (50.0, 1.0),
                "transform__music_mood__dramatic": (1.0, 50.0),
            }
        )
        import random as _random
        # Deterministic rng — Thompson picks the arm with higher expected value
        rng = _random.Random(42)
        with patch(
            "genlab_core.media.music_mood_llm_fit.suggest_mood",
            return_value=MoodSuggestion(
                top_mood="dramatic",  # LLM disagrees with bandit
                reasoning="cinematic scene",
                confidence=0.9,
            ),
        ), caplog.at_level(logging.INFO):
            result = select_transformation_dimensions(
                "gaming",
                cfg,
                proxy=proxy,
                rng=rng,
                blueprint_context={
                    "hook": "h", "title": "t", "summary": "s",
                },
            )
        assert result.choices["music_mood"].dimension_value == "hype"
        steer_logs = [r.message for r in caplog.records if "LLM_STEER music_mood" in r.message]
        assert len(steer_logs) == 1
        assert "bandit=hype" in steer_logs[0]
        assert "llm=dramatic" in steer_logs[0]
        assert "agree=False" in steer_logs[0]

    def test_wire_no_alteration_of_selection_outcome(self, monkeypatch):
        """The wire is observability-only. Two invocations with the same
        RNG seed — one with a strong LLM suggestion, one without — must
        produce the identical choice."""
        self._flag_on(monkeypatch)
        cfg = _enabled_config_with_music(["hype", "dramatic"])
        proxy = _proxy_returning_arms(
            {
                "transform__music_mood__hype": (2.0, 1.0),
                "transform__music_mood__dramatic": (2.0, 1.0),
            }
        )
        import random as _random

        with patch(
            "genlab_core.media.music_mood_llm_fit.suggest_mood",
            return_value=MoodSuggestion(
                top_mood="dramatic", reasoning="x", confidence=0.99
            ),
        ):
            result_with_llm = select_transformation_dimensions(
                "gaming", cfg, proxy=proxy, rng=_random.Random(7),
                blueprint_context={"hook": "h", "title": "t", "summary": "s"},
            )

        with patch(
            "genlab_core.media.music_mood_llm_fit.suggest_mood",
            return_value=None,
        ):
            result_without_llm = select_transformation_dimensions(
                "gaming", cfg, proxy=proxy, rng=_random.Random(7),
                blueprint_context={"hook": "h", "title": "t", "summary": "s"},
            )

        assert (
            result_with_llm.choices["music_mood"].dimension_value
            == result_without_llm.choices["music_mood"].dimension_value
        )
        assert (
            result_with_llm.choices["music_mood"].arm_id
            == result_without_llm.choices["music_mood"].arm_id
        )

    def test_wire_none_context_values_coerced_to_empty_string(
        self, monkeypatch
    ):
        """blueprint_context values are optional — hook/title/summary
        may each be None. The wire must coerce to str before passing to
        the LLM (which types them as str, non-optional)."""
        self._flag_on(monkeypatch)
        cfg = _enabled_config_with_music(["hype", "chill"])
        proxy = _proxy_returning_arms(
            {"transform__music_mood__hype": (5.0, 1.0)}
        )
        captured_kwargs = {}

        def fake_suggest(**kwargs):
            captured_kwargs.update(kwargs)
            return None

        with patch(
            "genlab_core.media.music_mood_llm_fit.suggest_mood",
            side_effect=fake_suggest,
        ):
            select_transformation_dimensions(
                "gaming", cfg, proxy=proxy,
                blueprint_context={
                    "hook": None,
                    "title": "Real Title",
                    "summary": None,
                },
            )
        assert captured_kwargs.get("hook") == ""
        assert captured_kwargs.get("title") == "Real Title"
        assert captured_kwargs.get("summary") == ""
