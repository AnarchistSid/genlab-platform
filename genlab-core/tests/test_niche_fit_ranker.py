"""Pin: LLM-judge niche-fit re-ranking for trending video candidates.

The 2026-06-22 audit identified velocity-only sorting as the highest-
leverage upstream smartness gap — view_velocity catches viral signal
but misses niche-fit. A trending gaming streamer's news reaction beats
a slightly-slower gameplay clip on velocity, but the gameplay clip
converts better for a gameplay channel.

This module adds an opt-in re-rank pass that, when
``GENLAB_VIDEO_NICHE_FIT_ENABLED=1``, asks Haiku to identify the
top-K most niche-fit candidates from the top-20 velocity-sorted
candidates. Fail-OPEN at every error path so the existing velocity-
only path is the safe fallback.

These pins guard:
- Opt-in: default off → no behavior change
- LLM returns ranked indices → reorder top-K, preserve tail
- Anthropic circuit open / parse errors / out-of-range indices →
  return original list (never freeze the pipeline on LLM issues)
- Out-of-bounds indices are filtered (LLM hallucination guard)
- Top-K cap respected (don't waste tokens on huge lists)
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch


@dataclass
class _FakeVideo:
    title: str
    description_snippet: str = ""
    channel_name: str = "test_channel"


def _make_videos(n: int) -> list[_FakeVideo]:
    return [_FakeVideo(title=f"Video {i}", description_snippet=f"desc {i}") for i in range(n)]


class TestOptIn:
    def test_default_off_returns_input_unchanged(self, monkeypatch):
        """Pin: env unset → no LLM call, no reordering. Shadow-rollout
        pattern matches Lever C/K/REPLY_CRITIC."""
        monkeypatch.delenv("GENLAB_VIDEO_NICHE_FIT_ENABLED", raising=False)
        from genlab_core.media.niche_fit_ranker import rerank_for_niche_fit

        videos = _make_videos(15)
        # Should NOT call anthropic — verify by patching to raise
        with patch("anthropic.Anthropic", side_effect=RuntimeError("should not be called")):
            result = rerank_for_niche_fit(videos, "gaming")
        assert result is videos  # exact same list, no reorder

    def test_env_zero_is_off(self, monkeypatch):
        """Pin: '0' is off (truthy-string trap guard)."""
        monkeypatch.setenv("GENLAB_VIDEO_NICHE_FIT_ENABLED", "0")
        from genlab_core.media.niche_fit_ranker import rerank_for_niche_fit

        videos = _make_videos(5)
        with patch("anthropic.Anthropic", side_effect=RuntimeError("should not be called")):
            result = rerank_for_niche_fit(videos, "gaming")
        assert result is videos


class TestReranking:
    def test_llm_reorder_picks_top_k_first(self, monkeypatch):
        """Pin: LLM picks indices → those videos come first in returned
        list, in the LLM's order. Unchosen candidates from top-N stay
        in velocity order behind them."""
        monkeypatch.setenv("GENLAB_VIDEO_NICHE_FIT_ENABLED", "1")
        from genlab_core.media import niche_fit_ranker

        videos = _make_videos(15)
        # LLM picks indices 3, 0, 7 as the top-3
        fake_resp = MagicMock()
        fake_resp.content = [MagicMock(text="3, 0, 7, 4, 5, 9, 1, 12, 11, 14")]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_resp

        with patch("anthropic.Anthropic", return_value=fake_client):
            with patch("genlab_core.intelligence.cost_accumulator.record_anthropic_usage"):
                result = niche_fit_ranker.rerank_for_niche_fit(videos, "gaming")
        # First 3 of result should be videos[3], videos[0], videos[7]
        assert result[0].title == "Video 3"
        assert result[1].title == "Video 0"
        assert result[2].title == "Video 7"
        # Total length preserved
        assert len(result) == len(videos)
        # All input videos still present
        assert {v.title for v in result} == {v.title for v in videos}

    def test_circuit_open_returns_input(self, monkeypatch):
        """Pin: ANTHROPIC_CB.call raises CircuitOpenError → return
        original list. Fail-OPEN preserves velocity ranking when API
        is down. Patches at the source import (CB is imported lazily
        inside rerank_for_niche_fit, so the canonical module path is
        what needs patching)."""
        monkeypatch.setenv("GENLAB_VIDEO_NICHE_FIT_ENABLED", "1")
        from genlab_core.http.circuit_breaker import CircuitOpenError
        from genlab_core.media import niche_fit_ranker

        videos = _make_videos(10)
        with patch("anthropic.Anthropic", return_value=MagicMock()):
            with patch(
                "genlab_core.http.circuit_breaker.ANTHROPIC_CB.call",
                side_effect=CircuitOpenError("test"),
            ):
                result = niche_fit_ranker.rerank_for_niche_fit(videos, "gaming")
        assert result is videos

    def test_unparseable_response_returns_input(self, monkeypatch):
        """Pin: LLM response with no numbers → return original list."""
        monkeypatch.setenv("GENLAB_VIDEO_NICHE_FIT_ENABLED", "1")
        from genlab_core.media import niche_fit_ranker

        videos = _make_videos(10)
        fake_resp = MagicMock()
        fake_resp.content = [MagicMock(text="I'm not sure how to rank these")]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_resp

        with patch("anthropic.Anthropic", return_value=fake_client):
            with patch("genlab_core.intelligence.cost_accumulator.record_anthropic_usage"):
                result = niche_fit_ranker.rerank_for_niche_fit(videos, "gaming")
        assert result is videos

    def test_out_of_range_indices_filtered(self, monkeypatch):
        """Pin: LLM returns indices ≥ len(candidates) → those are
        ignored (clamped by parser). Catches hallucinated 99,100,42
        indices from a model that didn't read the candidate list."""
        monkeypatch.setenv("GENLAB_VIDEO_NICHE_FIT_ENABLED", "1")
        from genlab_core.media import niche_fit_ranker

        videos = _make_videos(5)
        # LLM hallucinates indices way out of range
        fake_resp = MagicMock()
        fake_resp.content = [MagicMock(text="42, 99, 1, 4, 100, 3")]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_resp

        with patch("anthropic.Anthropic", return_value=fake_client):
            with patch("genlab_core.intelligence.cost_accumulator.record_anthropic_usage"):
                result = niche_fit_ranker.rerank_for_niche_fit(videos, "gaming")
        # Only valid indices (1, 4, 3) should be picked, in that order
        assert result[0].title == "Video 1"
        assert result[1].title == "Video 4"
        assert result[2].title == "Video 3"

    def test_duplicate_indices_dedup(self, monkeypatch):
        """Pin: LLM returns same index twice → deduped to one
        appearance. Otherwise a 'rank' could place the same video
        in two positions."""
        monkeypatch.setenv("GENLAB_VIDEO_NICHE_FIT_ENABLED", "1")
        from genlab_core.media import niche_fit_ranker

        videos = _make_videos(5)
        fake_resp = MagicMock()
        fake_resp.content = [MagicMock(text="2, 2, 2, 0, 4")]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_resp

        with patch("anthropic.Anthropic", return_value=fake_client):
            with patch("genlab_core.intelligence.cost_accumulator.record_anthropic_usage"):
                result = niche_fit_ranker.rerank_for_niche_fit(videos, "gaming")
        # Video 2 should appear ONCE at the front
        assert result[0].title == "Video 2"
        assert result[1].title == "Video 0"
        assert result[2].title == "Video 4"
        # And no duplicates anywhere in result
        titles = [v.title for v in result]
        assert len(titles) == len(set(titles))

    def test_large_list_caps_at_20(self, monkeypatch):
        """Pin: input with 50 videos → only first 20 sent to LLM, but
        all 50 returned in order. Caps token cost without losing
        candidates."""
        monkeypatch.setenv("GENLAB_VIDEO_NICHE_FIT_ENABLED", "1")
        from genlab_core.media import niche_fit_ranker

        videos = _make_videos(50)
        fake_resp = MagicMock()
        fake_resp.content = [MagicMock(text="0, 1, 2, 3, 4, 5, 6, 7, 8, 9")]  # LLM picks first 10
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_resp

        with patch("anthropic.Anthropic", return_value=fake_client):
            with patch("genlab_core.intelligence.cost_accumulator.record_anthropic_usage"):
                result = niche_fit_ranker.rerank_for_niche_fit(videos, "gaming")
        # All 50 should still be present
        assert len(result) == 50
        # And the user-prompt should have only contained 20 candidates
        call_messages = fake_client.messages.create.call_args.kwargs["messages"]
        user_content = call_messages[0]["content"]
        # 20 candidates → indices [0]..[19] in the prompt
        assert "[19]" in user_content
        assert "[20]" not in user_content


class TestEmptyAndEdgeCases:
    def test_empty_videos_list(self, monkeypatch):
        """Pin: empty input → empty output, no LLM call."""
        monkeypatch.setenv("GENLAB_VIDEO_NICHE_FIT_ENABLED", "1")
        from genlab_core.media import niche_fit_ranker

        with patch("anthropic.Anthropic", side_effect=RuntimeError("should not be called")):
            result = niche_fit_ranker.rerank_for_niche_fit([], "gaming")
        assert result == []

    def test_unknown_niche_uses_generic_brief(self, monkeypatch):
        """Pin: niche not in _NICHE_FIT_BRIEF → falls back to generic
        instruction (still ranks; doesn't error)."""
        monkeypatch.setenv("GENLAB_VIDEO_NICHE_FIT_ENABLED", "1")
        from genlab_core.media import niche_fit_ranker

        videos = _make_videos(3)
        fake_resp = MagicMock()
        fake_resp.content = [MagicMock(text="0, 1, 2")]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_resp

        with patch("anthropic.Anthropic", return_value=fake_client):
            with patch("genlab_core.intelligence.cost_accumulator.record_anthropic_usage"):
                result = niche_fit_ranker.rerank_for_niche_fit(videos, "new_niche")
        # Should not crash + should rank
        assert len(result) == 3
        # System prompt should have included a generic instruction
        sys_prompt = fake_client.messages.create.call_args.kwargs["system"]
        assert "new_niche" in sys_prompt
