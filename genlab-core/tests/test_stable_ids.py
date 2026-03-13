"""Tests for genlab_core.cache.stable_ids — canonical stable ID generation."""

import pytest

from genlab_core.cache.stable_ids import (
    generate_asset_id,
    generate_candidate_id,
    generate_claim_id,
    generate_cluster_id,
    generate_global_asset_id,
    generate_post_id,
    generate_story_id,
    generate_ugc_asset_id,
    normalize_url,
)


# --- normalize_url -----------------------------------------------------------

class TestNormalizeUrl:
    def test_strips_tracking_params(self):
        url = "https://example.com/article?utm_source=twitter&utm_medium=social&id=123"
        result = normalize_url(url)
        assert "utm_source" not in result
        assert "utm_medium" not in result
        assert "id=123" in result

    def test_lowercases_scheme_and_netloc(self):
        url = "https://Example.COM/Article"
        result = normalize_url(url)
        assert result == "https://example.com/Article"

    def test_preserves_path_case(self):
        url = "https://Example.COM/CaseSensitive/Path"
        result = normalize_url(url)
        assert result == "https://example.com/CaseSensitive/Path"

    def test_preserves_query_case(self):
        url = "https://example.com/search?q=CamelCase&id=ABC"
        result = normalize_url(url)
        assert "CamelCase" in result

    def test_no_query_params(self):
        url = "https://example.com/article"
        result = normalize_url(url)
        assert result == "https://example.com/article"

    def test_strips_fbclid(self):
        url = "https://example.com/post?fbclid=abc123"
        result = normalize_url(url)
        assert "fbclid" not in result

    def test_strips_trailing_slash(self):
        url = "https://example.com/article/"
        result = normalize_url(url)
        assert result == "https://example.com/article"

    def test_preserves_root_path(self):
        url = "https://example.com/"
        result = normalize_url(url)
        assert result == "https://example.com/"

    def test_rejects_empty_string(self):
        with pytest.raises(ValueError):
            normalize_url("")

    def test_rejects_no_scheme(self):
        with pytest.raises(ValueError):
            normalize_url("example.com/article")


# --- generate_story_id -------------------------------------------------------

class TestGenerateStoryId:
    def test_deterministic(self):
        id1 = generate_story_id("https://example.com/article", "2026-02-15T10:00:00Z")
        id2 = generate_story_id("https://example.com/article", "2026-02-15T10:00:00Z")
        assert id1 == id2

    def test_64_hex_chars(self):
        result = generate_story_id("https://example.com/article", "2026-02-15T10:00:00Z")
        assert len(result) == 64
        assert all(c in '0123456789abcdef' for c in result)

    def test_ignores_time_component(self):
        id1 = generate_story_id("https://example.com/article", "2026-02-15T10:00:00Z")
        id2 = generate_story_id("https://example.com/article", "2026-02-15T23:59:59Z")
        assert id1 == id2

    def test_different_urls_different_ids(self):
        id1 = generate_story_id("https://example.com/article-1", "2026-02-15T10:00:00Z")
        id2 = generate_story_id("https://example.com/article-2", "2026-02-15T10:00:00Z")
        assert id1 != id2

    def test_tracking_params_ignored(self):
        id1 = generate_story_id("https://example.com/article", "2026-02-15T10:00:00Z")
        id2 = generate_story_id("https://example.com/article?utm_source=twitter", "2026-02-15T10:00:00Z")
        assert id1 == id2


# --- generate_candidate_id ---------------------------------------------------

class TestGenerateCandidateId:
    def test_deterministic(self):
        id1 = generate_candidate_id("story123", "TPL_BRK1", "breaking news")
        id2 = generate_candidate_id("story123", "TPL_BRK1", "breaking news")
        assert id1 == id2

    def test_64_hex_chars(self):
        result = generate_candidate_id("story123", "TPL_BRK1", "breaking news")
        assert len(result) == 64

    def test_case_insensitive_angle(self):
        id1 = generate_candidate_id("story123", "TPL_BRK1", "Breaking News")
        id2 = generate_candidate_id("story123", "TPL_BRK1", "breaking news")
        assert id1 == id2


# --- generate_claim_id -------------------------------------------------------

class TestGenerateClaimId:
    def test_format(self):
        result = generate_claim_id("GPT-5 has improved reasoning capabilities")
        assert result.startswith("CLM_")
        assert len(result) == 12  # CLM_ + 8 chars

    def test_deterministic(self):
        id1 = generate_claim_id("GPT-5 has improved reasoning capabilities")
        id2 = generate_claim_id("GPT-5 has improved reasoning capabilities")
        assert id1 == id2

    def test_uppercase_hex(self):
        result = generate_claim_id("test claim text")
        hex_part = result[4:]
        assert hex_part == hex_part.upper()


# --- generate_asset_id -------------------------------------------------------

class TestGenerateAssetId:
    def test_format(self):
        result = generate_asset_id("story123", "https://cdn.example.com/img.jpg", "image")
        assert result.startswith("AST_")
        assert len(result) == 20  # AST_ + 16 chars

    def test_deterministic(self):
        id1 = generate_asset_id("story123", "https://cdn.example.com/img.jpg", "image")
        id2 = generate_asset_id("story123", "https://cdn.example.com/img.jpg", "image")
        assert id1 == id2


# --- generate_post_id --------------------------------------------------------

class TestGeneratePostId:
    def test_deterministic(self):
        id1 = generate_post_id("candidate123", "2026-02-15")
        id2 = generate_post_id("candidate123", "2026-02-15")
        assert id1 == id2

    def test_64_hex_chars(self):
        result = generate_post_id("candidate123", "2026-02-15")
        assert len(result) == 64


# --- generate_cluster_id -----------------------------------------------------

class TestGenerateClusterId:
    def test_deterministic(self):
        id1 = generate_cluster_id(["a", "b", "c"])
        id2 = generate_cluster_id(["a", "b", "c"])
        assert id1 == id2

    def test_order_independent(self):
        id1 = generate_cluster_id(["b", "a", "c"])
        id2 = generate_cluster_id(["c", "a", "b"])
        assert id1 == id2

    def test_format_prefix_and_length(self):
        result = generate_cluster_id(["x", "y"])
        assert result.startswith("CLU_")
        assert len(result) == 20

    def test_different_inputs_different_ids(self):
        id1 = generate_cluster_id(["a", "b"])
        id2 = generate_cluster_id(["a", "c"])
        assert id1 != id2


# --- generate_global_asset_id ------------------------------------------------

class TestGenerateGlobalAssetId:
    def test_deterministic(self):
        id1 = generate_global_asset_id("https://cdn.example.com/img.jpg", "image")
        id2 = generate_global_asset_id("https://cdn.example.com/img.jpg", "image")
        assert id1 == id2

    def test_format_prefix_and_length(self):
        result = generate_global_asset_id("https://cdn.example.com/img.jpg", "image")
        assert result.startswith("GAST_")
        assert len(result) == 21

    def test_strips_tracking_params(self):
        id1 = generate_global_asset_id("https://cdn.example.com/img.jpg", "image")
        id2 = generate_global_asset_id("https://cdn.example.com/img.jpg?utm_source=x", "image")
        assert id1 == id2

    def test_different_types(self):
        id1 = generate_global_asset_id("https://cdn.example.com/media", "image")
        id2 = generate_global_asset_id("https://cdn.example.com/media", "video")
        assert id1 != id2


# --- generate_ugc_asset_id ---------------------------------------------------

class TestGenerateUgcAssetId:
    def test_deterministic(self):
        id1 = generate_ugc_asset_id("https://youtube.com/watch?v=abc", "youtube")
        id2 = generate_ugc_asset_id("https://youtube.com/watch?v=abc", "youtube")
        assert id1 == id2

    def test_format_prefix_and_length(self):
        result = generate_ugc_asset_id("https://youtube.com/watch?v=abc", "youtube")
        assert result.startswith("UGC_")
        assert len(result) == 20

    def test_strips_tracking_params(self):
        id1 = generate_ugc_asset_id("https://youtube.com/watch?v=abc", "youtube")
        id2 = generate_ugc_asset_id("https://youtube.com/watch?v=abc&utm_source=x", "youtube")
        assert id1 == id2
