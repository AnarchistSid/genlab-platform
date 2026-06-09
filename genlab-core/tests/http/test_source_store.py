"""Tests for :mod:`genlab_core.http.source_store`.

Tier 2 / audit S-2, slice 2.4 (sources half) — dedicated test file
for the fourth extracted BacklogClient sub-store.

Coverage targets:
* create_source: success path returning the new record id, defaults
  applied to optional fields, no typecast (Sources doesn't pass it).
* update_source_fetch_status: find-then-update flow, no-op on miss,
  ISO 8601 UTC timestamp, formula escape on source_id.
* get_enabled_sources: passes ``{enabled}=TRUE()`` formula, niche_id
  passthrough, returns find verbatim.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from genlab_core.http.source_store import SourceStore


def _make_store():
    backend_mock = MagicMock()
    backend_mock.create.return_value = {"id": "src_new"}
    backend_mock.find.return_value = []
    backend_mock.update.return_value = None
    backend_lookup = MagicMock(return_value=backend_mock)
    return SourceStore(backend=backend_lookup), backend_mock


# ---------------------------------------------------------------------------
# create_source
# ---------------------------------------------------------------------------


class TestCreateSource:
    def _full_source(self) -> dict:
        return {
            "source_id": "src_1",
            "domain": "example.com",
            "name": "Example News",
            "url": "https://example.com/feed",
            "type": "rss",
            "priority": 0.9,
            "enabled": False,
            "authority_score": 0.75,
        }

    def test_returns_record_id_on_success(self) -> None:
        store, backend = _make_store()
        backend.create.return_value = {"id": "src_42"}
        assert store.create_source(self._full_source()) == "src_42"

    def test_passes_all_promoted_columns(self) -> None:
        store, backend = _make_store()
        store.create_source(self._full_source())
        fields = backend.create.call_args[0][1]
        assert fields == {
            "source_id": "src_1",
            "domain": "example.com",
            "name": "Example News",
            "url": "https://example.com/feed",
            "type": "rss",
            "priority": 0.9,
            "enabled": False,
            "authority_score": 0.75,
        }

    def test_name_defaults_to_domain(self) -> None:
        store, backend = _make_store()
        store.create_source(
            {
                "source_id": "s1",
                "domain": "example.com",
                "url": "u",
                "type": "rss",
            }
        )
        fields = backend.create.call_args[0][1]
        assert fields["name"] == "example.com"

    def test_priority_defaults_to_one(self) -> None:
        store, backend = _make_store()
        store.create_source({"source_id": "s1", "domain": "d", "url": "u", "type": "rss"})
        fields = backend.create.call_args[0][1]
        assert fields["priority"] == 1.0

    def test_enabled_defaults_to_true(self) -> None:
        store, backend = _make_store()
        store.create_source({"source_id": "s1", "domain": "d", "url": "u", "type": "rss"})
        fields = backend.create.call_args[0][1]
        assert fields["enabled"] is True

    def test_authority_score_defaults_to_half(self) -> None:
        store, backend = _make_store()
        store.create_source({"source_id": "s1", "domain": "d", "url": "u", "type": "rss"})
        fields = backend.create.call_args[0][1]
        assert fields["authority_score"] == 0.5

    def test_no_typecast_kwarg_passed(self) -> None:
        # Unlike Templates/AB_Tests, Sources.create doesn't pass
        # typecast=True. Pin that behaviour.
        store, backend = _make_store()
        store.create_source({"source_id": "s1", "domain": "d", "url": "u", "type": "rss"})
        assert "typecast" not in backend.create.call_args.kwargs


# ---------------------------------------------------------------------------
# update_source_fetch_status
# ---------------------------------------------------------------------------


class TestUpdateSourceFetchStatus:
    def test_noop_when_source_missing(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = []
        store.update_source_fetch_status("src_missing", "ok")
        backend.find.assert_called_once()
        backend.update.assert_not_called()

    def test_finds_by_source_id_then_updates(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = [{"id": "rec_1"}]
        store.update_source_fetch_status("src_1", "ok")
        backend.find.assert_called_once()
        backend.update.assert_called_once()
        args = backend.update.call_args[0]
        assert args[0] == "Sources"
        assert args[1] == "rec_1"
        update_fields = args[2]
        assert update_fields["last_fetch_status"] == "ok"

    def test_timestamp_is_iso8601_utc(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = [{"id": "rec_1"}]
        store.update_source_fetch_status("src_1", "ok")
        ts = backend.update.call_args[0][2]["last_fetch_at"]
        assert "T" in ts
        # tzaware UTC marker — datetime.now(UTC).isoformat() emits +00:00
        assert "+00:00" in ts

    def test_escapes_quote_in_source_id(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = []
        store.update_source_fetch_status("src'1", "ok")
        assert "src\\'1" in backend.find.call_args.kwargs["formula"]


# ---------------------------------------------------------------------------
# get_enabled_sources
# ---------------------------------------------------------------------------


class TestGetEnabledSources:
    def test_uses_enabled_true_formula(self) -> None:
        store, backend = _make_store()
        store.get_enabled_sources()
        assert backend.find.call_args.kwargs["formula"] == "{enabled}=TRUE()"

    def test_niche_id_passthrough(self) -> None:
        store, backend = _make_store()
        store.get_enabled_sources(niche_id="anime")
        assert backend.find.call_args.kwargs["niche_id"] == "anime"

    def test_no_niche_id_defaults_to_none(self) -> None:
        store, backend = _make_store()
        store.get_enabled_sources()
        assert backend.find.call_args.kwargs["niche_id"] is None

    def test_returns_find_results_verbatim(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = [{"id": "a"}, {"id": "b"}]
        assert store.get_enabled_sources() == [{"id": "a"}, {"id": "b"}]
