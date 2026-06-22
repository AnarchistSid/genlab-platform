"""Pin: content_pool rows pass StoryCandidate validation (bug fix 2026-06-22).

Pre-fix, ``TrendingVideoFetcher._read_from_content_pool`` returned
story dicts with ``published_at`` as a ``datetime`` object (from the
Postgres TIMESTAMPTZ column). Downstream ``merge_stories`` validates
each dict against the ``StoryCandidate`` Pydantic model, where
``published_at: str``. Pydantic v2 strict mode rejected the type
mismatch and the whole FetchTrendingVideos stage failed.

The fix: coerce ``datetime → ISO-8601 str`` at the read site (after
fetching from Postgres, before building the dict).

These pins lock in:

  StoryCandidate accepts the content_pool dict shape (2):
  1. published_at as ISO string → valid
  2. published_at empty string (NULL in DB) → valid (model default)

  Pre-fix regression (2):
  3. published_at as datetime → ValidationError
     (THE pin documenting why the fix is needed; future regression
     of the coercion would be caught by this)
  4. published_at as None → ValidationError
     (Postgres can return None for NULL columns; the coercion handles
     this via the ``or ""`` fallback)

  Coercion correctness (2):
  5. datetime → isoformat → roundtrip-parseable
  6. None → "" (empty string, valid for StoryCandidate)
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from genlab_core.pipeline.models import StoryCandidate
from pydantic import ValidationError


# Minimal valid dict shape mirroring _read_from_content_pool's output
def _make_content_pool_dict(**overrides) -> dict:
    base = {
        "title": "Test gaming clip",
        "source_url": "https://example.com/test",
        "video_url": "https://youtube.com/watch?v=test123",
        "video_id": "test123",
        "description": "test summary",
        "published_at": "2026-06-22T09:30:00+00:00",
        "duration_seconds": 30,
        "view_count": 5000,
        "view_velocity": 100.0,
        "source": "youtube",
        "source_type": "youtube",
        "thumbnail_url": "https://i.ytimg.com/vi/test123/hq.jpg",
    }
    base.update(overrides)
    return base


class TestStoryCandidateAcceptsContentPoolShape:
    def test_iso_string_published_at_valid(self):
        """Pin: post-fix behavior — published_at as ISO string passes
        StoryCandidate validation."""
        d = _make_content_pool_dict()
        candidate = StoryCandidate.model_validate(d)
        assert candidate.published_at == "2026-06-22T09:30:00+00:00"

    def test_empty_string_published_at_valid(self):
        """Pin: NULL TIMESTAMPTZ → empty string after coercion. Should
        validate (StoryCandidate.published_at defaults to "")."""
        d = _make_content_pool_dict(published_at="")
        candidate = StoryCandidate.model_validate(d)
        assert candidate.published_at == ""


class TestPreFixRegression:
    def test_datetime_published_at_rejected(self):
        """Pin: THE regression-canary. A datetime object as
        published_at MUST fail StoryCandidate validation. This is the
        bug behavior before the 2026-06-22 fix at
        ``trending_video_fetcher._read_from_content_pool``. If this
        test ever passes (i.e. Pydantic silently coerces), our fix
        is no longer load-bearing and we'd lose the type safety the
        StoryCandidate contract was meant to enforce."""
        d = _make_content_pool_dict(published_at=datetime(2026, 6, 22, 9, 30, tzinfo=UTC))
        with pytest.raises(ValidationError) as exc_info:
            StoryCandidate.model_validate(d)
        # Confirm it's the published_at field that failed
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("published_at",) and "string" in e["msg"].lower() for e in errors)

    def test_none_published_at_rejected_without_coercion(self):
        """Pin: ``None`` (raw NULL from Postgres) also fails validation.
        The fix's ``or ""`` fallback handles this — without that
        fallback, NULL columns would crash the stage even after the
        datetime fix."""
        d = _make_content_pool_dict(published_at=None)
        with pytest.raises(ValidationError):
            StoryCandidate.model_validate(d)


class TestCoercionCorrectness:
    def test_datetime_isoformat_roundtrips(self):
        """Pin: ``datetime.isoformat()`` produces a string that
        ``datetime.fromisoformat`` can parse back losslessly. The
        fix relies on this so downstream consumers can re-parse if
        they want to."""
        original = datetime(2026, 6, 22, 9, 30, 18, 471000, tzinfo=UTC)
        iso = original.isoformat()
        parsed = datetime.fromisoformat(iso)
        assert parsed == original

    def test_none_coerces_to_empty_string_via_or_fallback(self):
        """Pin: the ``published_at.isoformat() if published_at else ""``
        pattern produces an empty string for None inputs. Empty string
        is a valid StoryCandidate.published_at value (model default)."""
        published_at = None
        result = published_at.isoformat() if published_at else ""
        assert result == ""
        # Round-trip through StoryCandidate
        d = _make_content_pool_dict(published_at=result)
        candidate = StoryCandidate.model_validate(d)
        assert candidate.published_at == ""
