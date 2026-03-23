"""Simple file-based cache with TTL.

JSON files on disk, keyed by safe alphanumeric identifiers.
No external dependencies (stdlib only).

For heavy binary media caching (video clips, thumbnails), a diskcache-backed
AgentCache is planned but not yet needed by any consumer.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SAFE_KEY = re.compile(r'^[a-zA-Z0-9_.\-]{1,256}$')
_DEFAULT_MAX_ENTRIES = 10000


class Cache:
    """File-based JSON cache with TTL and auto-purge.

    Each cached value is stored as a separate JSON file in the cache directory.
    Keys are validated against a safe character set to prevent path traversal.

    Args:
        cache_dir: Directory for cache files. Created if it doesn't exist.
        max_entries: Maximum number of cached entries. Oldest are evicted
            when exceeded.

    Example:
        cache = Cache("/tmp/cache")
        cache.set("news_feed_abc", {"items": [...]})
        data = cache.get("news_feed_abc", ttl_hours=6)
    """

    def __init__(self, cache_dir: str = '.tmp/cache', max_entries: int = _DEFAULT_MAX_ENTRIES):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_entries = max_entries
        self._auto_purge()

    def _validate_key(self, key: str) -> bool:
        if not _SAFE_KEY.match(key):
            logger.warning("Cache: rejected unsafe key: %s", key[:50])
            return False
        return True

    def get(self, key: str, ttl_hours: int = 24) -> Any | None:
        """Get cached value if not expired.

        Returns None on cache miss, expiry, or corruption.
        Corrupted cache files are deleted automatically.
        """
        if not self._validate_key(key):
            return None
        cache_file = self.cache_dir / f"{key}.json"

        try:
            with open(cache_file) as f:
                cached = json.load(f)

            cached_at = datetime.fromisoformat(cached['timestamp'])
            now = datetime.now(UTC)
            if cached_at.tzinfo is None:
                cached_at = cached_at.replace(tzinfo=UTC)
            if now - cached_at > timedelta(hours=ttl_hours):
                try:
                    cache_file.unlink()
                except OSError:
                    pass
                return None

            return cached['data']
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning("Corrupt cache file %s (deleting): %s", cache_file.name, exc)
            try:
                cache_file.unlink()
            except OSError:
                pass
            return None
        except OSError as exc:
            logger.warning("Cache read error for %s: %s", cache_file.name, exc)
            return None

    def set(self, key: str, value: Any) -> None:
        """Cache value with timestamp."""
        if not self._validate_key(key):
            return
        cache_file = self.cache_dir / f"{key}.json"

        try:
            with open(cache_file, 'w') as f:
                json.dump({
                    'timestamp': datetime.now(UTC).isoformat(),
                    'data': value
                }, f)
        except OSError as exc:
            logger.warning("Cache write error for %s: %s", cache_file.name, exc)
        self._auto_purge()

    def clear(self) -> None:
        """Clear all cache entries."""
        for f in self.cache_dir.glob('*.json'):
            try:
                f.unlink()
            except OSError:
                pass

    def purge_expired(self, ttl_hours: int = 24) -> int:
        """Delete all cache entries older than ttl_hours. Returns count deleted.

        Uses file mtime as a fast proxy instead of loading every JSON file.
        """
        cutoff_ts = time.time() - (ttl_hours * 3600)
        deleted = 0
        for f in self.cache_dir.glob('*.json'):
            try:
                if f.stat().st_mtime < cutoff_ts:
                    f.unlink()
                    deleted += 1
            except OSError:
                pass
        return deleted

    def _auto_purge(self) -> None:
        """Evict oldest entries if over max_entries."""
        entries = sorted(self.cache_dir.glob("*.json"), key=lambda f: f.stat().st_mtime)
        excess = len(entries) - self.max_entries
        if excess > 0:
            for f in entries[:excess]:
                try:
                    f.unlink()
                except OSError:
                    pass
            logger.debug("Cache: auto-purged %d entries (max=%d)", excess, self.max_entries)

    def stats(self) -> dict:
        """Return cache stats."""
        files = list(self.cache_dir.glob('*.json'))
        total_size = sum(f.stat().st_size for f in files)
        return {
            "entries": len(files),
            "total_size_bytes": total_size,
            "cache_dir": str(self.cache_dir)
        }
