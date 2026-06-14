"""Pin niche_credentials.resolve_threads_credentials' cache-then-env
priority — added 2026-06-14 for the Threads LLUT auto-refresh flow.

When ``/opt/genlab/.threads_tokens.json`` exists with a non-expired
entry for a niche, resolve_threads_credentials must prefer it over the
env-var fallback. Without this, the auto-refresh script could
successfully refresh tokens but the poller would still read stale env
values and keep failing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta


def _write_cache(tmp_path, data: dict):
    """Helper: write a fake cache file + return its path."""
    cache_path = tmp_path / ".threads_tokens.json"
    cache_path.write_text(json.dumps(data))
    return cache_path


def test_cache_takes_priority_over_env(monkeypatch, tmp_path):
    """When the JSON cache has a fresh entry, env vars are ignored."""
    cache_path = _write_cache(
        tmp_path,
        {
            "gaming": {
                "access_token": "CACHED_TOKEN",
                "expires_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            }
        },
    )
    monkeypatch.setattr(
        "genlab_core.publishing.niche_credentials._THREADS_TOKEN_CACHE_PATH",
        str(cache_path),
    )
    monkeypatch.setenv("CRITICALRUSH_THREADS_ACCESS_TOKEN", "ENV_TOKEN")
    monkeypatch.setenv("CRITICALRUSH_THREADS_USER_ID", "USER123")

    from genlab_core.publishing.niche_credentials import resolve_threads_credentials

    token, user_id = resolve_threads_credentials("gaming")
    assert token == "CACHED_TOKEN", "cache must win over env when the cached entry is fresh"
    assert user_id == "USER123"


def test_env_fallback_when_cache_missing(monkeypatch, tmp_path):
    """When the cache file doesn't exist, fall through to env vars."""
    monkeypatch.setattr(
        "genlab_core.publishing.niche_credentials._THREADS_TOKEN_CACHE_PATH",
        str(tmp_path / "nonexistent.json"),
    )
    monkeypatch.setenv("CRITICALRUSH_THREADS_ACCESS_TOKEN", "ENV_TOKEN")

    from genlab_core.publishing.niche_credentials import resolve_threads_credentials

    token, _ = resolve_threads_credentials("gaming")
    assert token == "ENV_TOKEN"


def test_env_fallback_when_cache_entry_expired(monkeypatch, tmp_path):
    """A cache entry whose expires_at is in the past must be treated as
    stale — fall through to env. Otherwise a missed daily refresh could
    leave a long-expired token in the cache, masking the fix the env
    var would have provided."""
    cache_path = _write_cache(
        tmp_path,
        {
            "gaming": {
                "access_token": "STALE_CACHED_TOKEN",
                "expires_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            }
        },
    )
    monkeypatch.setattr(
        "genlab_core.publishing.niche_credentials._THREADS_TOKEN_CACHE_PATH",
        str(cache_path),
    )
    monkeypatch.setenv("CRITICALRUSH_THREADS_ACCESS_TOKEN", "ENV_TOKEN")

    from genlab_core.publishing.niche_credentials import resolve_threads_credentials

    token, _ = resolve_threads_credentials("gaming")
    assert token == "ENV_TOKEN", f"expired cache entry must fall through to env; got {token}"


def test_malformed_cache_falls_through_silently(monkeypatch, tmp_path):
    """A corrupted JSON file (e.g., truncated mid-write) must NOT crash
    the caller. Fall through to env."""
    cache_path = tmp_path / ".threads_tokens.json"
    cache_path.write_text("{not valid json")
    monkeypatch.setattr(
        "genlab_core.publishing.niche_credentials._THREADS_TOKEN_CACHE_PATH",
        str(cache_path),
    )
    monkeypatch.setenv("CRITICALRUSH_THREADS_ACCESS_TOKEN", "ENV_TOKEN")

    from genlab_core.publishing.niche_credentials import resolve_threads_credentials

    token, _ = resolve_threads_credentials("gaming")
    assert token == "ENV_TOKEN"


def test_cache_with_no_entry_for_niche_falls_through(monkeypatch, tmp_path):
    """Cache exists with entries for OTHER niches but not for ours.
    Must not pick up someone else's token."""
    cache_path = _write_cache(
        tmp_path,
        {
            "movies": {
                "access_token": "MOVIES_TOKEN",
                "expires_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            }
        },
    )
    monkeypatch.setattr(
        "genlab_core.publishing.niche_credentials._THREADS_TOKEN_CACHE_PATH",
        str(cache_path),
    )
    monkeypatch.setenv("CRITICALRUSH_THREADS_ACCESS_TOKEN", "ENV_TOKEN")

    from genlab_core.publishing.niche_credentials import resolve_threads_credentials

    token, _ = resolve_threads_credentials("gaming")
    assert token == "ENV_TOKEN", (
        "must not bleed another niche's cached token; per-niche isolation pin"
    )
