"""Pin the trending-audio Meta Reels interface + stub behavior.

This ships the INTERFACE for the Meta Reels trending audio fetcher.
The actual scraping is deliberately unimplemented (multi-day session
scope + TOS review needed). Tests pin the fail-open contract so when
the real scraper lands, callers can trust the interface.

## Contract

  * `get_trending_moods_for_niche(niche_id)` returns `list[TrendingAudioMood]`
  * Flag off (default) -> [] (never raises)
  * Flag on + no real scraper -> [] (stub INFO log)
  * Any exception in cache read -> [] + WARN log

  * `moods_as_prompt_context(trending)` returns string
  * Empty list -> "" (caller treats as no signal, skips prompt injection)
  * Non-empty -> sorted by rank, formatted for prompt injection
"""

from __future__ import annotations

import logging

from genlab_core.media.trending_audio_meta import (
    TrendingAudioMood,
    get_trending_moods_for_niche,
    moods_as_prompt_context,
)


class TestStubBehavior:
    def test_flag_off_returns_empty(self, monkeypatch):
        monkeypatch.delenv("GENLAB_TRENDING_AUDIO_META_ENABLED", raising=False)
        assert get_trending_moods_for_niche("sports") == []

    def test_flag_on_no_cache_returns_empty(self, monkeypatch, tmp_path):
        """When flag is on but the scraper has never written a cache
        (fresh install / scraper not deployed yet), returns []. The
        old STUB log is gone since 2026-08-12 — the cache reader is
        the real code path now, and cache-miss returns [] silently."""
        monkeypatch.setenv("GENLAB_TRENDING_AUDIO_META_ENABLED", "1")
        # Redirect cache root to an empty tmpdir so no stale prod cache
        # can accidentally satisfy the read
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        assert get_trending_moods_for_niche("sports") == []

    def test_never_raises_on_flag_on(self, monkeypatch):
        """Even if internals blow up, the contract is fail-open."""
        monkeypatch.setenv("GENLAB_TRENDING_AUDIO_META_ENABLED", "1")
        # Force _read_cache to raise
        import genlab_core.media.trending_audio_meta as mod

        def _boom(_niche):
            raise RuntimeError("simulated cache corruption")

        monkeypatch.setattr(mod, "_read_cache", _boom)
        result = get_trending_moods_for_niche("sports")
        assert result == []


class TestPromptContextFormatting:
    def test_empty_returns_empty_string(self):
        """Empty list -> empty string. Caller checks this and skips
        prompt-injection when there's no signal."""
        assert moods_as_prompt_context([]) == ""

    def test_single_mood_formatted(self):
        result = moods_as_prompt_context([
            TrendingAudioMood(mood="hype", trend_rank=1, meta_audio_id="a1"),
        ])
        assert "TRENDING ON META REELS:" in result
        assert "hype" in result
        assert "rank 1" in result

    def test_sorted_by_rank(self):
        """Higher-viral (lower rank number) appears first — LLM reads
        top-to-bottom, so leading with the hottest mood biases the
        pick correctly."""
        result = moods_as_prompt_context([
            TrendingAudioMood(mood="chill", trend_rank=5, meta_audio_id="a5"),
            TrendingAudioMood(mood="hype", trend_rank=1, meta_audio_id="a1"),
            TrendingAudioMood(mood="dramatic", trend_rank=3, meta_audio_id="a3"),
        ])
        # hype (rank 1) appears before dramatic (rank 3) appears before chill (rank 5)
        assert result.index("hype") < result.index("dramatic") < result.index("chill")

    def test_meta_audio_id_NOT_leaked_to_prompt(self):
        """meta_audio_id is for future auditing — MUST NOT appear in
        the LLM prompt. LLM only needs the mood name + rank."""
        result = moods_as_prompt_context([
            TrendingAudioMood(mood="hype", trend_rank=1, meta_audio_id="secret_meta_id_xyz"),
        ])
        assert "secret_meta_id_xyz" not in result


class TestConsumerWiring:
    """The selector wire filters trending moods to those in the
    niche's available set — no point suggesting a mood the
    orchestrator can't consume."""

    def test_music_mood_llm_fit_accepts_trending_context(self, monkeypatch):
        """suggest_mood() has a trending_context kwarg. Empty string
        (default) = baseline. Non-empty = injected into prompt."""
        import inspect

        from genlab_core.media.music_mood_llm_fit import suggest_mood

        sig = inspect.signature(suggest_mood)
        assert "trending_context" in sig.parameters
        assert sig.parameters["trending_context"].default == ""

    def test_transformation_selector_calls_trending_fetcher(self):
        """Structural pin: the selector imports and calls the trending
        module. Guards against the wire being deleted accidentally
        (which would silently drop the trending signal without warning)."""
        import pathlib

        selector_path = (
            pathlib.Path(__file__).parents[2]
            / "src"
            / "genlab_core"
            / "media"
            / "transformation_selector.py"
        )
        src = selector_path.read_text()
        assert "get_trending_moods_for_niche" in src
        assert "moods_as_prompt_context" in src
        assert "trending_context=trending_context" in src
