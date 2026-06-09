"""Tests for :mod:`genlab_core.http.asset_store`.

Tier 2 / audit S-2, slice 2.5b — dedicated test file for the sixth
extracted BacklogClient sub-store.

Coverage targets:
* create_asset: full success, story/blueprint link injection,
  quality-fields fallback retry on UNKNOWN_FIELD_NAME/columnNotFound,
  source_url -> file mapping when status=READY, source_type
  normalization via injected callable.
* find_asset_by_asset_id: hit + miss, formula escape, niche_id
  passthrough.
* find_asset_by_url: same, plus the quote-in-url edge case.
* find_assets_by_story_id: story lookup miss returns empty list,
  hit translates to story_link formula.
* update_asset_status: raises ValueError on miss, success path,
  **kwargs passthrough.
* batch_create_assets: skips existing, swallows per-asset failures
  (logs + continues), returns successful ids.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from genlab_core.http.asset_store import AssetStore


def _make_store(*, find_story=None, find_blueprint=None, normalize=None):
    """Build an AssetStore with MagicMock backend + identity helpers."""
    backend_mock = MagicMock()
    backend_mock.create.return_value = {"id": "asset_new"}
    backend_mock.find.return_value = []
    backend_mock.update.return_value = None
    backend_lookup = MagicMock(return_value=backend_mock)

    store = AssetStore(
        backend=backend_lookup,
        find_story=find_story or (lambda sid, **kw: None),
        find_blueprint=find_blueprint or (lambda cid, **kw: None),
        normalize_asset_source_type=normalize or (lambda src, atype: src or "hero_image"),
    )
    return store, backend_mock


# ---------------------------------------------------------------------------
# create_asset
# ---------------------------------------------------------------------------


class TestCreateAsset:
    def _minimal_asset(self) -> dict:
        return {"asset_id": "a1", "type": "image"}

    def test_returns_record_id_on_success(self) -> None:
        store, backend = _make_store()
        backend.create.return_value = {"id": "rec_42"}
        assert store.create_asset(self._minimal_asset()) == "rec_42"

    def test_status_defaults_to_generating(self) -> None:
        store, backend = _make_store()
        store.create_asset(self._minimal_asset())
        # Last call (the non-quality path) gets just `fields`.
        assert backend.create.call_args[0][1]["status"] == "GENERATING"

    def test_url_prefers_source_url(self) -> None:
        store, backend = _make_store()
        store.create_asset({"asset_id": "a1", "type": "image", "source_url": "https://x.com/a.jpg"})
        assert backend.create.call_args[0][1]["url"] == "https://x.com/a.jpg"

    def test_url_falls_back_to_url_key(self) -> None:
        store, backend = _make_store()
        store.create_asset({"asset_id": "a1", "type": "image", "url": "https://x.com/b.jpg"})
        assert backend.create.call_args[0][1]["url"] == "https://x.com/b.jpg"

    def test_calls_injected_normalize(self) -> None:
        store, backend = _make_store(
            normalize=lambda src, atype: f"normalized:{src or 'none'}:{atype}"
        )
        store.create_asset({"asset_id": "a1", "type": "video", "source": "youtube"})
        assert backend.create.call_args[0][1]["source_type"] == "normalized:youtube:video"

    def test_story_link_injected_when_story_found(self) -> None:
        store, backend = _make_store(find_story=lambda sid, **kw: {"id": "story_rec_99"})
        store.create_asset({"asset_id": "a1", "type": "image", "story_id": "sid_1"})
        assert backend.create.call_args[0][1]["story"] == ["story_rec_99"]

    def test_story_link_omitted_when_story_missing(self) -> None:
        store, backend = _make_store(find_story=lambda sid, **kw: None)
        store.create_asset({"asset_id": "a1", "type": "image", "story_id": "sid_missing"})
        assert "story" not in backend.create.call_args[0][1]

    def test_blueprint_link_injected_when_blueprint_found(self) -> None:
        store, backend = _make_store(find_blueprint=lambda cid, **kw: {"id": "bp_rec_5"})
        store.create_asset({"asset_id": "a1", "type": "image", "blueprint_id": "cand_1"})
        assert backend.create.call_args[0][1]["blueprint"] == ["bp_rec_5"]

    def test_file_field_set_when_status_ready_with_source_url(self) -> None:
        store, backend = _make_store()
        store.create_asset(
            {
                "asset_id": "a1",
                "type": "image",
                "source_url": "https://x.com/a.jpg",
                "status": "READY",
            }
        )
        assert backend.create.call_args[0][1]["file"] == [{"url": "https://x.com/a.jpg"}]

    def test_file_field_omitted_when_status_not_ready(self) -> None:
        store, backend = _make_store()
        store.create_asset(
            {
                "asset_id": "a1",
                "type": "image",
                "source_url": "https://x.com/a.jpg",
                "status": "GENERATING",
            }
        )
        assert "file" not in backend.create.call_args[0][1]

    def test_quality_fields_retry_on_unknown_field(self) -> None:
        store, backend = _make_store()
        # First call fails with UNKNOWN_FIELD_NAME, second succeeds.
        backend.create.side_effect = [
            Exception("UNKNOWN_FIELD_NAME: quality_tier"),
            {"id": "rec_after_retry"},
        ]
        result = store.create_asset(
            {
                "asset_id": "a1",
                "type": "image",
                "quality_tier": "premium",
                "width": 1080,
                "height": 1920,
            }
        )
        assert result == "rec_after_retry"
        # First call had quality fields; second didn't.
        first_fields = backend.create.call_args_list[0][0][1]
        second_fields = backend.create.call_args_list[1][0][1]
        assert "quality_tier" in first_fields
        assert "quality_tier" not in second_fields

    def test_quality_fields_retry_on_column_not_found(self) -> None:
        store, backend = _make_store()
        backend.create.side_effect = [
            Exception("columnNotFound: width"),
            {"id": "rec_retry"},
        ]
        assert store.create_asset({"asset_id": "a1", "type": "image", "width": 800}) == "rec_retry"

    def test_quality_fields_failure_with_other_exception_propagates(self) -> None:
        store, backend = _make_store()
        backend.create.side_effect = Exception("totally different error")
        with pytest.raises(Exception, match="totally different error"):
            store.create_asset({"asset_id": "a1", "type": "image", "quality_tier": "premium"})

    def test_skips_quality_path_entirely_when_no_quality_fields(self) -> None:
        store, backend = _make_store()
        store.create_asset({"asset_id": "a1", "type": "image"})
        # Single call (no retry).
        assert backend.create.call_count == 1


# ---------------------------------------------------------------------------
# find_asset_by_asset_id
# ---------------------------------------------------------------------------


class TestFindAssetByAssetId:
    def test_returns_first_match(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = [{"id": "rec_1"}, {"id": "rec_2"}]
        assert store.find_asset_by_asset_id("a1") == {"id": "rec_1"}

    def test_returns_none_when_not_found(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = []
        assert store.find_asset_by_asset_id("a_missing") is None

    def test_uses_asset_id_formula(self) -> None:
        store, backend = _make_store()
        store.find_asset_by_asset_id("a1")
        assert backend.find.call_args.kwargs["formula"] == "{asset_id}='a1'"

    def test_escapes_quote_in_asset_id(self) -> None:
        store, backend = _make_store()
        store.find_asset_by_asset_id("a'1")
        assert "a\\'1" in backend.find.call_args.kwargs["formula"]

    def test_niche_id_passthrough(self) -> None:
        store, backend = _make_store()
        store.find_asset_by_asset_id("a1", niche_id="gaming")
        assert backend.find.call_args.kwargs["niche_id"] == "gaming"

    def test_max_records_pinned_to_1(self) -> None:
        store, backend = _make_store()
        store.find_asset_by_asset_id("a1")
        assert backend.find.call_args.kwargs["max_records"] == 1


# ---------------------------------------------------------------------------
# find_asset_by_url
# ---------------------------------------------------------------------------


class TestFindAssetByUrl:
    def test_uses_url_formula(self) -> None:
        store, backend = _make_store()
        store.find_asset_by_url("https://x.com/a.jpg")
        assert backend.find.call_args.kwargs["formula"] == "{url}='https://x.com/a.jpg'"

    def test_escapes_quote_in_url(self) -> None:
        store, backend = _make_store()
        store.find_asset_by_url("https://x.com/a'b.jpg")
        assert "a\\'b" in backend.find.call_args.kwargs["formula"]

    def test_returns_none_when_not_found(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = []
        assert store.find_asset_by_url("https://x.com/missing.jpg") is None


# ---------------------------------------------------------------------------
# find_assets_by_story_id
# ---------------------------------------------------------------------------


class TestFindAssetsByStoryId:
    def test_empty_list_when_story_missing(self) -> None:
        store, backend = _make_store(find_story=lambda sid, **kw: None)
        assert store.find_assets_by_story_id("sid_missing") == []
        backend.find.assert_not_called()

    def test_translates_story_id_to_story_link_formula(self) -> None:
        store, backend = _make_store(find_story=lambda sid, **kw: {"id": "story_rec_99"})
        store.find_assets_by_story_id("sid_1")
        assert backend.find.call_args.kwargs["formula"] == "{story_link}='story_rec_99'"

    def test_niche_id_passed_to_find_story_and_assets(self) -> None:
        seen_kw = {}

        def fake_find_story(sid, **kw):
            seen_kw.update(kw)
            return {"id": "rec1"}

        store, backend = _make_store(find_story=fake_find_story)
        store.find_assets_by_story_id("sid_1", niche_id="anime")
        assert seen_kw["niche_id"] == "anime"
        assert backend.find.call_args.kwargs["niche_id"] == "anime"


# ---------------------------------------------------------------------------
# update_asset_status
# ---------------------------------------------------------------------------


class TestUpdateAssetStatus:
    def test_raises_value_error_when_missing(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = []
        with pytest.raises(ValueError, match="Asset a_missing not found"):
            store.update_asset_status("a_missing", "READY")

    def test_updates_existing_asset(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = [{"id": "rec_1"}]
        store.update_asset_status("a1", "READY")
        args = backend.update.call_args[0]
        assert args[0] == "Assets"
        assert args[1] == "rec_1"
        assert args[2] == {"status": "READY"}

    def test_kwargs_merged_into_update(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = [{"id": "rec_1"}]
        store.update_asset_status("a1", "READY", file_size=1024)
        assert backend.update.call_args[0][2] == {"status": "READY", "file_size": 1024}


# ---------------------------------------------------------------------------
# batch_create_assets
# ---------------------------------------------------------------------------


class TestBatchCreateAssets:
    def test_returns_only_newly_created_ids(self) -> None:
        store, backend = _make_store()
        # 3 assets: a1 already exists (skip), a2 succeeds, a3 fails.
        # Drive find: a1 hit, a2 miss, a3 miss; create: a2→rec_2, a3→raise.
        backend.find.side_effect = [
            [{"id": "rec_existing"}],  # a1 exists
            [],  # a2 missing
            [],  # a3 missing
        ]
        backend.create.side_effect = [
            {"id": "rec_2"},
            Exception("network blip"),
        ]
        result = store.batch_create_assets(
            [
                {"asset_id": "a1", "type": "image"},
                {"asset_id": "a2", "type": "image"},
                {"asset_id": "a3", "type": "image"},
            ]
        )
        assert result == ["rec_2"]

    def test_empty_input_returns_empty(self) -> None:
        store, backend = _make_store()
        assert store.batch_create_assets([]) == []
        backend.find.assert_not_called()
        backend.create.assert_not_called()

    def test_swallows_individual_failures(self, caplog) -> None:
        store, backend = _make_store()
        backend.find.return_value = []  # all missing
        backend.create.side_effect = Exception("boom")
        with caplog.at_level("ERROR"):
            result = store.batch_create_assets([{"asset_id": "a1", "type": "image"}])
        assert result == []
        assert "Failed to create asset a1" in caplog.text
