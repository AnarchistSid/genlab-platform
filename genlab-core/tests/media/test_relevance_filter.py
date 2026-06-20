"""Tests for the content relevance filter."""

from genlab_core.media.relevance_filter import RelevanceFilter


class TestRelevanceFilter:
    def _make_filter(self, **overrides):
        config = {
            "positive_keywords": ["anime", "manga", "crunchyroll", "shonen", "episode"],
            "negative_keywords": ["mma", "ufc", "boxing", "dana white", "nate diaz"],
            "relevance_threshold": 0.3,
        }
        config.update(overrides)
        return RelevanceFilter("anime", config)

    def test_rejects_mma_content(self):
        rf = self._make_filter()
        score = rf.score("Dana White's beef with Nick Diaz went DEEP", "UFC fight")
        assert score == 0.0

    def test_accepts_anime_content(self):
        rf = self._make_filter()
        score = rf.score("Subaru's about to break AGAIN", "Re:Zero anime episode")
        assert score >= 0.3

    def test_negative_keyword_hard_reject(self):
        rf = self._make_filter()
        score = rf.score("Epic anime-style UFC knockout", "MMA highlights")
        assert score == 0.0  # "ufc" in text triggers hard reject

    def test_filter_removes_irrelevant(self):
        rf = self._make_filter()
        candidates = [
            {"title": "One Piece episode 1200 reaction", "description": "anime"},
            {"title": "Nate Diaz on Living With His Brother", "description": "MMA"},
            {"title": "Re:Zero Season 3 trailer", "description": "crunchyroll anime"},
        ]
        kept = rf.filter(candidates)
        assert len(kept) == 2
        kept_titles = [c["title"] for c in kept]
        assert "One Piece episode 1200 reaction" in kept_titles
        assert "Re:Zero Season 3 trailer" in kept_titles
        assert "Nate Diaz on Living With His Brother" not in kept_titles

    def test_empty_config_passes_all(self):
        rf = RelevanceFilter("gaming", {})
        candidates = [{"title": "anything", "description": "whatever"}]
        kept = rf.filter(candidates)
        assert len(kept) == 1

    def test_relevance_score_attached(self):
        rf = self._make_filter()
        candidates = [{"title": "anime fight scene", "description": "shonen manga"}]
        kept = rf.filter(candidates)
        assert "relevance_score" in kept[0]
        assert kept[0]["relevance_score"] > 0

    def test_score_capped_at_one(self):
        """Score never exceeds 1.0 even with many keyword matches."""
        rf = self._make_filter()
        score = rf.score(
            "anime manga crunchyroll shonen episode",
            "anime manga crunchyroll shonen episode",
        )
        assert score <= 1.0

    def test_no_positive_keywords_passes_all(self):
        """Without positive keywords, everything passes (no negatives to reject)."""
        rf = RelevanceFilter("test", {"negative_keywords": ["spam"]})
        assert rf.score("some clean video", "") == 1.0

    def test_negative_keyword_case_insensitive(self):
        rf = self._make_filter()
        score = rf.score("UFC HIGHLIGHTS", "")
        assert score == 0.0

    def test_positive_keyword_case_insensitive(self):
        rf = self._make_filter()
        score = rf.score("ANIME EPISODE", "")
        assert score > 0.0

    def test_filter_preserves_other_fields(self):
        """Filter should not strip fields from candidate dicts."""
        rf = self._make_filter()
        candidates = [{"title": "anime episode 5", "description": "manga", "video_id": "abc123"}]
        kept = rf.filter(candidates)
        assert kept[0]["video_id"] == "abc123"

    def test_empty_candidates_returns_empty(self):
        rf = self._make_filter()
        kept = rf.filter([])
        assert kept == []


class TestSummaryFallback:
    """Pin the description-or-summary fallback in RelevanceFilter.filter().

    Pre-fix (2026-06-20): ``filter()`` read ``v.get("description", "")``
    only, which silently returned ``""`` for BlackboxBrief stories where
    the legacy-bridge code at ``bb_strategies/content_research.py:103``
    populates ``summary`` instead of ``description``. The bridge predates
    the genlab-core unification — BB's RSS pipeline names the secondary
    text ``summary`` (matching feedparser's convention), while
    ``RelevanceFilter`` named it ``description`` (matching YouTube API).

    Result: BB stories were scored on the title alone. Negative-keyword
    matches in the article body were invisible to the filter, letting
    off-niche content (sports / paint timelapses / anime episodes) pass
    when their titles happened to be generic.

    Fix: prefer ``description``, fall back to ``summary`` so that neither
    producer is forced to rename its data shape.
    """

    def _make_filter(self):
        config = {
            "positive_keywords": ["ai", "claude", "gpt"],
            "negative_keywords": ["gym", "paint"],
            "relevance_threshold": 0.3,
        }
        return RelevanceFilter("ai_creators", config)

    def test_summary_field_consulted_when_description_missing(self):
        """A BB-shaped story (``summary`` set, no ``description``) gets its
        secondary text read by the filter — so negative keywords in the
        body trigger the hard-reject path."""
        rf = self._make_filter()
        bb_story = {
            "title": "Bored Sunday morning ideas",  # generic, no AI marker
            "summary": "Hit the gym, did some bench press, then took a walk.",
            # Note: no "description" key at all — matches BB bridge shape
        }
        kept = rf.filter([bb_story])
        # "gym" in summary triggers negative-keyword hard reject -> score=0.0
        # -> below 0.3 threshold -> story is rejected
        assert kept == [], (
            "Pre-fix this would have passed: title scored alone, score=0.0 "
            "(no AI keyword) but description=\"\" never saw 'gym' so the "
            "behavior was a false-clean. With fallback, summary IS read, "
            "'gym' triggers hard reject, story is correctly rejected."
        )

    def test_summary_positive_keywords_also_count(self):
        """Symmetric to negative: positive keywords in the summary also
        contribute to scoring, not just the title."""
        rf = self._make_filter()
        bb_story = {
            "title": "Cool new tool",  # generic title, no AI marker
            "summary": "Anthropic released Claude 4.7 with new agentic features.",
        }
        kept = rf.filter([bb_story])
        # "claude" in summary brings score up past 0.3 -> kept
        assert len(kept) == 1
        assert kept[0]["relevance_score"] >= 0.3

    def test_description_takes_precedence_when_both_present(self):
        """When both ``description`` and ``summary`` exist, ``description``
        wins. The fallback never overrides an explicit description."""
        rf = self._make_filter()
        candidate = {
            "title": "Tool review",
            "description": "Anthropic Claude review",  # AI keyword here
            "summary": "Gym workout for beginners",  # negative keyword here
        }
        kept = rf.filter([candidate])
        # description wins -> "claude" matched, "gym" never consulted
        assert len(kept) == 1, (
            "description has precedence — if summary were also scanned the "
            "'gym' negative would have rejected, but pre-existing YouTube "
            "candidates (which set ``description``, not ``summary``) must "
            "keep working exactly as before."
        )

    def test_youtube_shaped_candidate_unchanged_behavior(self):
        """A YouTube-shaped candidate (``description`` set, no ``summary``)
        continues to score exactly as it did before the fix — regression
        guard for the non-BB producers."""
        rf = self._make_filter()
        yt_candidate = {
            "title": "Claude 4.7 demo",
            "description": "AI agentic coding demo with Claude.",
        }
        kept = rf.filter([yt_candidate])
        assert len(kept) == 1
        assert kept[0]["relevance_score"] >= 0.3

    def test_empty_summary_and_description_falls_back_to_empty_string(self):
        """When neither ``description`` nor ``summary`` are set, the
        secondary text is empty string — no AttributeError, no None
        leaking into ``score()``."""
        rf = self._make_filter()
        bare_candidate = {"title": "Claude 4.7 just dropped"}
        kept = rf.filter([bare_candidate])
        # Title has "claude" -> scores above threshold -> kept
        assert len(kept) == 1

    def test_summary_is_none_handled_without_typeerror(self):
        """``v.get("summary", "")`` returns ``None`` when summary IS the
        key but the value is None. The ``or`` chain handles that — no
        ``TypeError: argument of type 'NoneType' is not iterable``."""
        rf = self._make_filter()
        weird_candidate = {
            "title": "Claude 4.7 review",
            "description": None,
            "summary": None,
        }
        # Must not raise
        kept = rf.filter([weird_candidate])
        # Title has "claude" -> scores above threshold -> kept
        assert len(kept) == 1
