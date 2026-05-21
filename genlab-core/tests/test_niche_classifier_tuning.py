"""Tests for classifier tuning at 10x volume."""
from unittest.mock import patch

import pytest
import yaml


@pytest.fixture
def classifier(tmp_path):
    """Create a classifier with minimal test profiles."""
    for niche_id, kw_cfg in [
        ("ai_creators", {"positive_keywords": ["ai", "chatgpt", "llm", "neural", "model"], "negative_keywords": ["cooking"], "relevance_threshold": 0.30}),
        ("gaming", {"positive_keywords": ["game", "gaming", "esports", "fortnite", "playstation"], "negative_keywords": ["cooking"], "relevance_threshold": 0.20}),
        ("sports", {"positive_keywords": ["sports", "nba", "football", "cricket", "ufc"], "negative_keywords": ["cooking"], "relevance_threshold": 0.20}),
        ("movies", {"positive_keywords": ["movie", "trailer", "film", "oscar", "netflix"], "negative_keywords": ["cooking"], "relevance_threshold": 0.25}),
        ("anime", {"positive_keywords": ["anime", "manga", "crunchyroll", "one piece", "demon slayer"], "negative_keywords": ["cooking"], "relevance_threshold": 0.35}),
    ]:
        cfg_file = tmp_path / f"{niche_id}.yaml"
        cfg_file.write_text(yaml.dump({"content_filter": kw_cfg}))

    paths = {nid: tmp_path / f"{nid}.yaml" for nid in ["ai_creators", "gaming", "sports", "movies", "anime"]}
    with patch("genlab_core.intelligence.niche_classifier.NICHE_SOURCE_PATHS", paths):
        from genlab_core.intelligence.niche_classifier import NicheClassifier
        c = NicheClassifier(genlab_root=tmp_path)
    return c


def test_single_keyword_not_enough(classifier):
    scores = classifier.classify("Something about AI today")
    assert scores["ai_creators"] < 0.20


def test_two_keywords_scores_reasonably(classifier):
    scores = classifier.classify("AI model breaks records")
    assert scores["ai_creators"] > 0.10


def test_affinity_requires_keyword_match(classifier):
    scores = classifier.classify("Cooking tips for beginners", source_affinity=["ai_creators"])
    assert scores["ai_creators"] == 0.0


def test_youtube_category_contributes_without_keywords(classifier):
    scores = classifier.classify("New update available now", youtube_category="20")
    assert scores["gaming"] == 0.15


def test_negative_keyword_hard_reject(classifier):
    scores = classifier.classify("AI cooking robot makes pasta", source_affinity=["ai_creators"])
    assert scores["ai_creators"] == 0.0
