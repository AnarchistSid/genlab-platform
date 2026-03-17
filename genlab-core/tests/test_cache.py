"""Tests for genlab_core.cache.disk_cache.Cache."""

import json
import time

import pytest
from genlab_core.cache.disk_cache import Cache


@pytest.fixture
def cache(tmp_path):
    """Create a cache in a temp directory."""
    return Cache(str(tmp_path / "cache"))


class TestCacheBasic:
    def test_set_and_get(self, cache):
        cache.set("test_key", {"hello": "world"})
        result = cache.get("test_key")
        assert result == {"hello": "world"}

    def test_cache_miss(self, cache):
        result = cache.get("nonexistent_key")
        assert result is None

    def test_overwrite(self, cache):
        cache.set("key", "old_value")
        cache.set("key", "new_value")
        assert cache.get("key") == "new_value"

    def test_clear(self, cache):
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.clear()
        assert cache.get("key1") is None
        assert cache.get("key2") is None


class TestCacheTTL:
    def test_ttl_expired(self, cache):
        """Expired items return None."""
        cache.set("test_key", "data")
        cache_file = cache.cache_dir / "test_key.json"
        data = json.loads(cache_file.read_text())
        data['timestamp'] = "2020-01-01T00:00:00"
        cache_file.write_text(json.dumps(data))
        result = cache.get("test_key", ttl_hours=1)
        assert result is None

    def test_ttl_valid(self, cache):
        cache.set("test_key", "data")
        result = cache.get("test_key", ttl_hours=24)
        assert result == "data"


class TestCacheKeyValidation:
    def test_rejects_path_traversal(self, tmp_path):
        cache = Cache(cache_dir=str(tmp_path / "cache"))
        cache.set("../../etc/passwd", {"evil": True})
        assert cache.get("../../etc/passwd") is None

    def test_rejects_slashes(self, tmp_path):
        cache = Cache(cache_dir=str(tmp_path / "cache"))
        cache.set("key/with/slashes", {"evil": True})
        assert cache.get("key/with/slashes") is None

    def test_accepts_valid_key(self, tmp_path):
        cache = Cache(cache_dir=str(tmp_path / "cache"))
        cache.set("valid_key-123", {"good": True})
        assert cache.get("valid_key-123") == {"good": True}


class TestCacheEviction:
    def test_auto_purge_on_max_entries(self, tmp_path):
        cache = Cache(cache_dir=str(tmp_path / "cache"), max_entries=5)
        for i in range(10):
            cache.set(f"key_{i}", {"data": i})
            time.sleep(0.01)
        remaining = list((tmp_path / "cache").glob("*.json"))
        assert len(remaining) <= 5

    def test_purge_expired(self, tmp_path):
        cache = Cache(cache_dir=str(tmp_path / "cache"))
        cache.set("old_key", "old_data")
        cache.set("new_key", "new_data")
        # Backdate the old key
        old_file = cache.cache_dir / "old_key.json"
        data = json.loads(old_file.read_text())
        data['timestamp'] = "2020-01-01T00:00:00"
        old_file.write_text(json.dumps(data))
        # Set mtime to the past for purge_expired (uses mtime)
        import os
        os.utime(old_file, (0, 0))
        deleted = cache.purge_expired(ttl_hours=1)
        assert deleted >= 1
        assert cache.get("new_key") == "new_data"


class TestCacheStats:
    def test_stats(self, cache):
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        stats = cache.stats()
        assert stats["entries"] == 2
        assert stats["total_size_bytes"] > 0


class TestCacheCorruption:
    def test_corrupt_json_returns_none(self, cache):
        """Corrupt cache files are deleted and treated as misses."""
        cache.set("corrupt_key", "data")
        cache_file = cache.cache_dir / "corrupt_key.json"
        cache_file.write_text("not valid json{{{")
        result = cache.get("corrupt_key")
        assert result is None
        # File should be deleted
        assert not cache_file.exists()
