"""Pin: ``load_yaml_cached`` reads each file at most once per mtime.

Perf fix (2026-06-21): dashboard config + alerts endpoints re-read +
parsed the same niche-registry, alerting, and affiliate-catalog YAMLs
on every request. The mtime cache turns each steady-state call into a
single ``stat()``; only an actual file edit triggers a re-parse.

These pins lock in:

  1. Repeated calls for an unchanged file → exactly ONE disk read.
  2. mtime change → re-parse fires (operator edits propagate immediately,
     no TTL staleness window).
  3. Nonexistent path → returns ``None`` (matches prior behavior; existing
     callers handle ``None``).
  4. Empty / malformed YAML → still cached as the parsed value (None for
     empty, None for parse errors) so we don't repeatedly stat+open a
     known-bad file.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _clear_cache():
    from server.core import yaml_cache

    yaml_cache.clear_yaml_cache()
    yield
    yaml_cache.clear_yaml_cache()


class TestMtimeKeyedCache:
    def test_repeated_calls_unchanged_file_one_disk_read(self, tmp_path):
        from server.core.yaml_cache import load_yaml_cached

        p = tmp_path / "config.yaml"
        p.write_text("foo: 1\nbar: [a, b, c]\n")

        # Spy on ``open`` to count file reads. Patch ``builtins.open``
        # only — yaml.safe_load itself doesn't open files.
        with patch("server.core.yaml_cache.open", side_effect=open) as spy_open:
            for _ in range(15):
                data = load_yaml_cached(p)
                assert data == {"foo": 1, "bar": ["a", "b", "c"]}

            # The pin: 15 logical reads → 1 actual disk read. If the
            # mtime comparison breaks or someone removes the cache,
            # this test fails immediately.
            assert spy_open.call_count == 1, (
                f"Expected 1 disk read for 15 calls with unchanged file, got {spy_open.call_count}."
            )

    def test_mtime_change_triggers_reparse(self, tmp_path):
        """Operator edits the YAML — next request sees the new value
        without any cache-invalidation API call."""
        from server.core.yaml_cache import load_yaml_cached

        p = tmp_path / "config.yaml"
        p.write_text("value: original\n")
        assert load_yaml_cached(p) == {"value": "original"}

        # Bump mtime forward (filesystem mtime resolution can be 1s on
        # some platforms, so we sleep + write atomically). 1.05s is the
        # safe minimum cross-platform.
        time.sleep(1.05)
        p.write_text("value: updated\n")

        assert load_yaml_cached(p) == {"value": "updated"}, (
            "mtime cache did not invalidate on file edit — operator edits would never propagate."
        )

    def test_nonexistent_path_returns_none_without_raise(self, tmp_path):
        from server.core.yaml_cache import load_yaml_cached

        missing = tmp_path / "does-not-exist.yaml"
        # Caller handles ``None`` already (config_routes._load_yaml /
        # alerts placeholder check). Don't raise.
        assert load_yaml_cached(missing) is None

    def test_empty_yaml_parses_to_none_and_caches(self, tmp_path):
        """Empty file → ``yaml.safe_load`` returns None. The cache
        stores that so an empty config doesn't re-parse on every call."""
        from server.core.yaml_cache import load_yaml_cached

        p = tmp_path / "empty.yaml"
        p.write_text("")

        with patch("server.core.yaml_cache.open", side_effect=open) as spy_open:
            for _ in range(5):
                assert load_yaml_cached(p) is None
            assert spy_open.call_count == 1, (
                f"Empty YAML was re-parsed {spy_open.call_count}× — "
                f"the None result should still be cached."
            )

    def test_malformed_yaml_returns_none_does_not_poison_cache(self, tmp_path):
        """A parse error returns None but does NOT cache — the next
        request gets a fresh attempt (matches prior uncached behavior
        where each request would re-fail loudly)."""
        from server.core.yaml_cache import load_yaml_cached

        p = tmp_path / "broken.yaml"
        p.write_text("not: valid: yaml: :::\n  - mismatched indentation\n  also: bad")

        # First call returns None (parse error logged).
        with patch("server.core.yaml_cache.open", side_effect=open) as spy_open:
            assert load_yaml_cached(p) is None
            assert load_yaml_cached(p) is None
            # Pin: a parse error is NOT cached — subsequent calls re-attempt
            # the parse. Each call hits open() so the operator's fix to the
            # YAML is observed on the very next request.
            assert spy_open.call_count == 2, (
                f"Malformed YAML should not poison the cache; got "
                f"{spy_open.call_count} opens for 2 calls (expected 2)."
            )

    def test_distinct_paths_have_distinct_cache_entries(self, tmp_path):
        """No collisions between unrelated configs."""
        from server.core.yaml_cache import load_yaml_cached

        a = tmp_path / "a.yaml"
        b = tmp_path / "b.yaml"
        a.write_text("which: a\n")
        b.write_text("which: b\n")

        assert load_yaml_cached(a) == {"which": "a"}
        assert load_yaml_cached(b) == {"which": "b"}
        # Re-read — both come from cache, no cross-contamination.
        assert load_yaml_cached(a) == {"which": "a"}
        assert load_yaml_cached(b) == {"which": "b"}
