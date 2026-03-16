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
