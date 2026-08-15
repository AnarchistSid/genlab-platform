"""Pin threads_hashtags augment (2026-08-15 Threads-stagnation fix)."""
from __future__ import annotations

import pytest

from genlab_core.publishing.threads_hashtags import (
    _MAX_TAGS,
    _NICHE_FALLBACK,
    append_niche_hashtags,
    is_enabled_for,
)


class TestFlagSemantics:
    @pytest.mark.parametrize("val", ["", "0", "false", "no", "off"])
    def test_off_tokens(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_THREADS_HASHTAGS_NICHES", val)
        assert is_enabled_for("anime") is False

    def test_unset_is_off(self, monkeypatch):
        monkeypatch.delenv("GENLAB_THREADS_HASHTAGS_NICHES", raising=False)
        assert is_enabled_for("anime") is False

    def test_wildcard(self, monkeypatch):
        monkeypatch.setenv("GENLAB_THREADS_HASHTAGS_NICHES", "all")
        for n in ("ai_creators", "gaming", "sports", "movies", "anime"):
            assert is_enabled_for(n) is True

    def test_canary(self, monkeypatch):
        monkeypatch.setenv("GENLAB_THREADS_HASHTAGS_NICHES", "gaming,anime")
        assert is_enabled_for("gaming") is True
        assert is_enabled_for("anime") is True
        assert is_enabled_for("movies") is False


class TestAppend:
    def test_disabled_returns_caption_unchanged(self, monkeypatch):
        monkeypatch.delenv("GENLAB_THREADS_HASHTAGS_NICHES", raising=False)
        cap = "Original body"
        out = append_niche_hashtags(cap, "anime", ["#Anime"])
        assert out == cap

    def test_empty_caption_stays_empty(self, monkeypatch):
        monkeypatch.setenv("GENLAB_THREADS_HASHTAGS_NICHES", "all")
        assert append_niche_hashtags("", "anime", []) == ""

    def test_appends_from_source_when_available(self, monkeypatch):
        monkeypatch.setenv("GENLAB_THREADS_HASHTAGS_NICHES", "all")
        cap = "Peak anime moment right here."
        out = append_niche_hashtags(cap, "anime", ["#Anime", "#Manga", "#Otaku"])
        assert cap in out
        # First 2 from source (respects _MAX_TAGS)
        assert "#Anime" in out
        assert "#Manga" in out
        # Third one skipped
        assert "#Otaku" not in out

    def test_fallback_niche_anchor_when_no_source(self, monkeypatch):
        monkeypatch.setenv("GENLAB_THREADS_HASHTAGS_NICHES", "all")
        cap = "Body without tags"
        out = append_niche_hashtags(cap, "gaming", [])
        assert cap in out
        assert _NICHE_FALLBACK["gaming"] in out

    def test_idempotent_when_hashtag_already_present(self, monkeypatch):
        """LLM already put a hashtag inline → don't append more."""
        monkeypatch.setenv("GENLAB_THREADS_HASHTAGS_NICHES", "all")
        cap = "This is content with a tag #SomethingSpecific at the end."
        out = append_niche_hashtags(cap, "anime", ["#Anime"])
        assert out == cap

    def test_position_number_hash_does_not_trigger_idempotency(self, monkeypatch):
        """Bug caught 2026-08-15 live probe: '#1' position number
        in caption body incorrectly triggered the idempotency guard,
        skipping the augment. Real hashtags start with a letter —
        '#1' is content, not metadata."""
        monkeypatch.setenv("GENLAB_THREADS_HASHTAGS_NICHES", "all")
        cap = "Rust just hit #1 trending on Twitch. Watch this play."
        out = append_niche_hashtags(cap, "gaming", [])
        # Fallback tag SHOULD be appended since no REAL hashtag exists
        assert "#Gaming" in out, (
            f"'#1' in body must not skip the augment; expected "
            f"#Gaming appended: got {out!r}"
        )

    def test_max_2_tags(self, monkeypatch):
        monkeypatch.setenv("GENLAB_THREADS_HASHTAGS_NICHES", "all")
        cap = "Body"
        out = append_niche_hashtags(
            cap, "gaming", ["#A", "#B", "#C", "#D", "#E"],
        )
        # Count hashtags in output
        n = sum(1 for w in out.split() if w.startswith("#"))
        assert n <= _MAX_TAGS

    def test_normalizes_missing_hash_prefix(self, monkeypatch):
        """If source hashtag lacks #, add it."""
        monkeypatch.setenv("GENLAB_THREADS_HASHTAGS_NICHES", "all")
        out = append_niche_hashtags("Body", "gaming", ["Gaming", "clips"])
        assert "#Gaming" in out
        assert "#clips" in out

    def test_unknown_niche_no_fallback(self, monkeypatch):
        """Unknown niche + empty source = no fallback (caption unchanged)."""
        monkeypatch.setenv("GENLAB_THREADS_HASHTAGS_NICHES", "all")
        cap = "Body"
        out = append_niche_hashtags(cap, "nonexistent", [])
        assert out == cap

    def test_placement_inline_at_end(self, monkeypatch):
        monkeypatch.setenv("GENLAB_THREADS_HASHTAGS_NICHES", "all")
        cap = "Body content here."
        out = append_niche_hashtags(cap, "anime", ["#Anime"])
        # Threads convention: blank line separator + tag block at end
        assert out.endswith("#Anime")
        assert "\n\n#Anime" in out


class TestNichePools:
    def test_every_niche_has_fallback(self):
        for niche in ("gaming", "sports", "movies", "anime", "ai_creators"):
            assert niche in _NICHE_FALLBACK
            assert _NICHE_FALLBACK[niche].startswith("#")

    def test_max_tags_matches_threads_norm(self):
        """Threads community norm is 1-3 tags MAX. _MAX_TAGS=2 is
        conservative. Never let this creep above 3 without operator
        review."""
        assert _MAX_TAGS <= 3
