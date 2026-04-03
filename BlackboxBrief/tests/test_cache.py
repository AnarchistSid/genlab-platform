"""Tests for file-based cache."""

import json
import time

import pytest
from genlab_core.cache.disk_cache import Cache


@pytest.fixture
def cache(tmp_path):
    """Create a cache in a temp directory."""
    return Cache(str(tmp_path / "cache"))


class TestCache:
    def test_set_and_get(self, cache):
        cache.set("test_key", {"hello": "world"})
        result = cache.get("test_key")
        assert result == {"hello": "world"}

    def test_cache_miss(self, cache):
        result = cache.get("nonexistent_key")
        assert result is None

    def test_ttl_expired(self, cache):
        """Expired items return None."""
        cache.set("test_key", "data")
        # Manually backdate the timestamp
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

    def test_clear(self, cache):
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.clear()
        assert cache.get("key1") is None
        assert cache.get("key2") is None

    def test_stats(self, cache):
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        stats = cache.stats()
        assert stats["entries"] == 2
        assert stats["total_size_bytes"] > 0

    def test_overwrite(self, cache):
        cache.set("key", "old_value")
        cache.set("key", "new_value")
        assert cache.get("key") == "new_value"


def test_cache_rejects_unsafe_keys(tmp_path):
    """Cache must reject keys with path traversal characters."""
    cache = Cache(cache_dir=str(tmp_path / "cache"))

    # Path traversal
    cache.set("../../etc/passwd", {"evil": True})
    assert cache.get("../../etc/passwd") is None

    # Slashes
    cache.set("key/with/slashes", {"evil": True})
    assert cache.get("key/with/slashes") is None

    # Valid key should still work
    cache.set("valid_key-123", {"good": True})
    assert cache.get("valid_key-123") == {"good": True}


def test_cache_auto_purge_on_max_entries(tmp_path):
    """Cache must evict oldest entries when exceeding max_entries."""
    cache = Cache(cache_dir=str(tmp_path / "cache"), max_entries=5)

    for i in range(10):
        cache.set(f"key_{i}", {"data": i})
        time.sleep(0.01)  # Ensure distinct timestamps

    # Only 5 most recent entries should survive
    remaining = list((tmp_path / "cache").glob("*.json"))
    assert len(remaining) <= 5
