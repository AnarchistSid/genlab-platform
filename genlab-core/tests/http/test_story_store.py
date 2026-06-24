"""Tests for :mod:`genlab_core.http.story_store`.

Tier 2 / audit S-2, slice 2.5a — dedicated test file for the fifth
extracted BacklogClient sub-store.

Coverage targets:
* create_story: full success path, scores-dict flattening, niche_id
  conditional include, source resolution via injected callable,
  default priority/status/scores.
* find_story_by_story_id: hit + miss, formula escape, niche_id
  passthrough, ``max_records=1`` pinned, ``_sp_call`` wrapping.
* update_story_status: success, raises ValueError on miss, **kwargs
  passthrough.
* batch_create_stories: returns ids in order, the no-why_it_matters
  bulk-path quirk, scores flattening per row.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from genlab_core.http.story_store import StoryStore


def _make_store(*, resolve_source=None):
    """Build a StoryStore with MagicMock backend + identity sp_call.

    ``sp_call`` is the identity wrapper (no circuit breaker) so tests
    don't need to set one up. ``resolve_source`` defaults to a
    sentinel that returns "Other" — tests that care override it.
    """
    backend_mock = MagicMock()
    backend_mock.create.return_value = {"id": "story_new"}
    backend_mock.find.return_value = []
    backend_mock.update.return_value = None
    backend_mock.batch_create.return_value = []
    backend_lookup = MagicMock(return_value=backend_mock)
    sp_call = lambda fn, *a, **kw: fn(*a, **kw)  # noqa: E731

    resolver = resolve_source or (lambda story: story.get("source", "Other"))
    store = StoryStore(
        sp_call=sp_call,
        backend=backend_lookup,
        resolve_source=resolver,
    )
    return store, backend_mock


# ---------------------------------------------------------------------------
# create_story
# ---------------------------------------------------------------------------


class TestCreateStory:
    def _full_story(self) -> dict:
        return {
            "story_id": "sid_1",
            "title": "Big news",
            "url": "https://example.com/a",
            "source": "TechCrunch",
            "published_at": "2026-06-09T12:00:00Z",
            "summary": "Brief",
            "why_it_matters": "Lots",
            "priority": 0.9,
            "themes": ["ai", "tools"],
            "scores": {"authority": 0.8, "recency": 0.95, "novelty": 0.7},
            "niche_id": "ai_creators",
        }

    def test_returns_record_id_on_success(self) -> None:
        store, backend = _make_store()
        backend.create.return_value = {"id": "rec_42"}
        assert store.create_story(self._full_story()) == "rec_42"

    def test_writes_all_promoted_columns(self) -> None:
        store, backend = _make_store()
        store.create_story(self._full_story())
        fields = backend.create.call_args[0][1]
        assert fields["story_id"] == "sid_1"
        assert fields["authority_score"] == 0.8
        assert fields["recency_score"] == 0.95
        assert fields["novelty_score"] == 0.7
        assert fields["status"] == "INTAKE"
        assert fields["themes"] == ["ai", "tools"]

    def test_calls_injected_resolve_source(self) -> None:
        seen: list[dict] = []

        def resolver(story: dict) -> str:
            seen.append(story)
            return "Resolved!"

        store, backend = _make_store(resolve_source=resolver)
        store.create_story(self._full_story())
        assert len(seen) == 1
        assert backend.create.call_args[0][1]["source"] == "Resolved!"

    def test_niche_id_only_included_when_provided(self) -> None:
        store, backend = _make_store()
        story = self._full_story()
        del story["niche_id"]
        store.create_story(story)
        fields = backend.create.call_args[0][1]
        assert "niche_id" not in fields

    def test_priority_defaults_to_scores_priority_then_half(self) -> None:
        store, backend = _make_store()
        # Explicit priority wins.
        store.create_story({"story_id": "s1", "title": "t", "url": "u", "priority": 0.7})
        assert backend.create.call_args[0][1]["priority"] == 0.7

        # Fallback: scores.priority.
        backend.reset_mock()
        store.create_story(
            {"story_id": "s1", "title": "t", "url": "u", "scores": {"priority": 0.3}}
        )
        assert backend.create.call_args[0][1]["priority"] == 0.3

        # Final fallback: 0.5.
        backend.reset_mock()
        store.create_story({"story_id": "s1", "title": "t", "url": "u"})
        assert backend.create.call_args[0][1]["priority"] == 0.5


# ---------------------------------------------------------------------------
# find_story_by_story_id
# ---------------------------------------------------------------------------


class TestFindStoryByStoryId:
    def test_returns_first_match(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = [{"id": "rec_1"}, {"id": "rec_2"}]
        assert store.find_story_by_story_id("sid_1") == {"id": "rec_1"}

    def test_returns_none_when_not_found(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = []
        assert store.find_story_by_story_id("sid_missing") is None

    def test_uses_story_id_formula(self) -> None:
        store, backend = _make_store()
        store.find_story_by_story_id("sid_1")
        assert backend.find.call_args.kwargs["formula"] == "{story_id}='sid_1'"

    def test_escapes_quote_in_story_id(self) -> None:
        store, backend = _make_store()
        store.find_story_by_story_id("sid'1")
        assert "sid\\'1" in backend.find.call_args.kwargs["formula"]

    def test_niche_id_passthrough(self) -> None:
        store, backend = _make_store()
        store.find_story_by_story_id("sid_1", niche_id="gaming")
        assert backend.find.call_args.kwargs["niche_id"] == "gaming"

    def test_max_records_pinned_to_1(self) -> None:
        store, backend = _make_store()
        store.find_story_by_story_id("sid_1")
        assert backend.find.call_args.kwargs["max_records"] == 1


# ---------------------------------------------------------------------------
# update_story_status
# ---------------------------------------------------------------------------


class TestUpdateStoryStatus:
    def test_raises_value_error_when_missing(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = []
        with pytest.raises(ValueError, match="Story sid_missing not found"):
            store.update_story_status("sid_missing", "VALIDATED")

    def test_updates_existing_story(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = [{"id": "rec_1"}]
        store.update_story_status("sid_1", "VALIDATED")
        backend.update.assert_called_once()
        args = backend.update.call_args[0]
        assert args[0] == "Stories"
        assert args[1] == "rec_1"
        assert args[2] == {"status": "VALIDATED"}

    def test_kwargs_merged_into_update(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = [{"id": "rec_1"}]
        store.update_story_status("sid_1", "VALIDATED", reviewed_at="2026-06-09T12:00:00Z")
        args = backend.update.call_args[0]
        assert args[2] == {
            "status": "VALIDATED",
            "reviewed_at": "2026-06-09T12:00:00Z",
        }

    def test_niche_id_passed_to_find(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = [{"id": "rec_1"}]
        store.update_story_status("sid_1", "VALIDATED", niche_id="gaming")
        # find call has niche_id=gaming
        assert backend.find.call_args.kwargs["niche_id"] == "gaming"


# ---------------------------------------------------------------------------
# batch_create_stories
# ---------------------------------------------------------------------------


class TestBatchCreateStories:
    def test_returns_ids_in_order(self) -> None:
        store, backend = _make_store()
        backend.batch_create.return_value = [
            {"id": "rec_1"},
            {"id": "rec_2"},
            {"id": "rec_3"},
        ]
        result = store.batch_create_stories(
            [
                {"story_id": "s1", "title": "a", "url": "ua"},
                {"story_id": "s2", "title": "b", "url": "ub"},
                {"story_id": "s3", "title": "c", "url": "uc"},
            ]
        )
        assert result == ["rec_1", "rec_2", "rec_3"]

    def test_empty_input_short_circuits(self) -> None:
        store, backend = _make_store()
        backend.batch_create.return_value = []
        assert store.batch_create_stories([]) == []
        # PR #528 adds niche_id kwarg to every backend.batch_create
        # call. For empty input the dispatch evaluates 0 niches →
        # niche_id=None (admin-mode fallback, backward compat).
        backend.batch_create.assert_called_once_with("Stories", [], niche_id=None)

    def test_bulk_path_omits_why_it_matters(self) -> None:
        # Historical quirk: BacklogClient.batch_create_stories never
        # included why_it_matters. Pin that behaviour.
        store, backend = _make_store()
        backend.batch_create.return_value = [{"id": "r1"}]
        store.batch_create_stories(
            [
                {
                    "story_id": "s1",
                    "title": "t",
                    "url": "u",
                    "why_it_matters": "should NOT be passed through",
                }
            ]
        )
        passed = backend.batch_create.call_args[0][1][0]
        assert "why_it_matters" not in passed

    def test_uses_injected_resolve_source_per_row(self) -> None:
        store, backend = _make_store(resolve_source=lambda s: f"resolved-{s['story_id']}")
        store.batch_create_stories(
            [
                {"story_id": "s1", "title": "t1", "url": "u1"},
                {"story_id": "s2", "title": "t2", "url": "u2"},
            ]
        )
        rows = backend.batch_create.call_args[0][1]
        assert rows[0]["source"] == "resolved-s1"
        assert rows[1]["source"] == "resolved-s2"

    def test_flattens_scores_per_row(self) -> None:
        store, backend = _make_store()
        store.batch_create_stories(
            [
                {
                    "story_id": "s1",
                    "title": "t",
                    "url": "u",
                    "scores": {"authority": 0.9, "recency": 0.5, "novelty": 0.1},
                }
            ]
        )
        row = backend.batch_create.call_args[0][1][0]
        assert row["authority_score"] == 0.9
        assert row["recency_score"] == 0.5
        assert row["novelty_score"] == 0.1
