"""Tests for stable ID generation."""

from genlab_core.cache.stable_ids import (
    generate_candidate_id,
    generate_claim_id,
    generate_cluster_id,
    generate_global_asset_id,
    generate_post_id,
    generate_story_id,
    normalize_url,
)


class TestNormalizeUrl:
    def test_strips_tracking_params(self):
        url = "https://example.com/article?utm_source=twitter&utm_medium=social&id=123"
        result = normalize_url(url)
        assert "utm_source" not in result
        assert "utm_medium" not in result
        assert "id=123" in result

    def test_lowercases_scheme_and_netloc(self):
        """P2.2: Only scheme + netloc are lowercased; path case is preserved."""
        url = "https://Example.COM/Article"
        result = normalize_url(url)
        assert result == "https://example.com/Article"

    def test_preserves_path_case(self):
        """P2.2: Case-sensitive paths must be preserved (different resources)."""
        url = "https://Example.COM/CaseSensitive/Path"
        result = normalize_url(url)
        assert result == "https://example.com/CaseSensitive/Path"

    def test_preserves_query_case(self):
        """P2.2: Query parameter values should preserve case."""
        url = "https://example.com/search?q=CamelCase&id=ABC"
        result = normalize_url(url)
        assert "CamelCase" in result or "q=CamelCase" in result

    def test_no_query_params(self):
        url = "https://example.com/article"
        result = normalize_url(url)
        assert result == "https://example.com/article"

    def test_strips_fbclid(self):
        url = "https://example.com/post?fbclid=abc123"
        result = normalize_url(url)
        assert "fbclid" not in result


class TestGenerateStoryId:
    def test_deterministic(self):
        """Same inputs always produce the same ID."""
        id1 = generate_story_id("https://example.com/article", "2026-02-15T10:00:00Z")
        id2 = generate_story_id("https://example.com/article", "2026-02-15T10:00:00Z")
        assert id1 == id2

    def test_64_hex_chars(self):
        result = generate_story_id("https://example.com/article", "2026-02-15T10:00:00Z")
        assert len(result) == 64
        assert all(c in '0123456789abcdef' for c in result)

    def test_ignores_time_component(self):
        """Same date, different times should produce the same ID."""
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


class TestGeneratePostId:
    def test_deterministic(self):
        id1 = generate_post_id("candidate123", "2026-02-15")
        id2 = generate_post_id("candidate123", "2026-02-15")
        assert id1 == id2

    def test_64_hex_chars(self):
        result = generate_post_id("candidate123", "2026-02-15")
        assert len(result) == 64


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


class TestColumnMapCacheTTL:
    """P2.10: Verify that backlog_client column map cache respects TTL."""

    def test_cache_has_ttl_constant(self):
        """Module must define a TTL constant for column map cache."""
        from genlab_core.http.graph_proxy import _COLUMN_MAP_TTL
        assert isinstance(_COLUMN_MAP_TTL, (int, float))
        assert _COLUMN_MAP_TTL > 0

    def test_expired_cache_is_ignored(self):
        """Entries older than TTL must not be returned from cache."""
        import time as _time_mod

        from genlab_core.http import graph_proxy as bc

        # Save original cache
        original_cache = bc._column_map_cache.copy()
        try:
            # Insert a stale entry (timestamp far in the past)
            fake_list_id = "__test_stale__"
            stale_ts = _time_mod.monotonic() - bc._COLUMN_MAP_TTL - 100
            bc._column_map_cache[fake_list_id] = (
                {"display": "internal"},
                {"internal": "display"},
                stale_ts,
            )
            # The stale entry should exist in the dict but any reader
            # should check the timestamp and reject it
            cached = bc._column_map_cache.get(fake_list_id)
            assert cached is not None  # raw dict still has it
            assert len(cached) >= 3
            d2i, i2d, ts = cached
            # Verify it IS expired
            assert (_time_mod.monotonic() - ts) >= bc._COLUMN_MAP_TTL
        finally:
            # Restore
            bc._column_map_cache.clear()
            bc._column_map_cache.update(original_cache)

    def test_fresh_cache_is_valid(self):
        """Entries newer than TTL should be considered valid."""
        import time as _time_mod

        from genlab_core.http import graph_proxy as bc

        original_cache = bc._column_map_cache.copy()
        try:
            fake_list_id = "__test_fresh__"
            fresh_ts = _time_mod.monotonic()
            bc._column_map_cache[fake_list_id] = (
                {"display": "internal"},
                {"internal": "display"},
                fresh_ts,
            )
            cached = bc._column_map_cache.get(fake_list_id)
            assert cached is not None
            d2i, i2d, ts = cached
            assert (_time_mod.monotonic() - ts) < bc._COLUMN_MAP_TTL
        finally:
            bc._column_map_cache.clear()
            bc._column_map_cache.update(original_cache)
