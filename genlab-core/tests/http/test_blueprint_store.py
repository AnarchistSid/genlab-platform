"""Tests for :mod:`genlab_core.http.blueprint_store`.

Tier 2 / audit S-2 — dedicated test file for the largest extracted
BacklogClient sub-store.

Coverage targets:
* create_blueprint: success path, story-not-found ValueError, story
  passthrough (avoid finder call), template passthrough, optional
  niche_id/clip_url/topic_category/etc. fields, validation flags,
  structure list joining + structure_text override, retry-on-unknown-
  field-name (3 known error strings), other-exception propagation.
* find_blueprint_by_candidate_id: hit + miss, formula escape,
  niche_id passthrough, sp_call wrapping.
* get_blueprints_by_status: status formula + escape, niche_id pass.
* get_blueprints_safe_to_cleanup: skip scheduled, priority filter,
  fields/proxy-record shape variation.
* update_blueprint_status: success, missing → ValueError,
  scheduled-post guard called when not forced, guard skipped when
  forced, **kwargs merged, typecast=True passed.
* batch_create_blueprints: caches story lookups across rows,
  caches template lookups, skips blueprints with missing stories,
  returns ids in order.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from genlab_core.http.blueprint_store import BlueprintStore


def _make_store(*, find_story=None, find_template=None, assert_not_scheduled=None):
    """Build a BlueprintStore with MagicMock backend + identity sp_call.

    Default finders return a sensible "found" record so tests don't
    have to wire each one. ``assert_not_scheduled`` defaults to a
    no-op (tests that exercise the guard inject their own).
    """
    backend_mock = MagicMock()
    backend_mock.create.return_value = {"id": "bp_new"}
    backend_mock.find.return_value = []
    backend_mock.update.return_value = None
    backend_mock.batch_create.return_value = []
    backend_lookup = MagicMock(return_value=backend_mock)
    sp_call = lambda fn, *a, **kw: fn(*a, **kw)  # noqa: E731

    store = BlueprintStore(
        sp_call=sp_call,
        backend=backend_lookup,
        find_story=find_story or (lambda sid, **kw: {"id": "story_rec"}),
        find_template=find_template or (lambda tid, **kw: {"id": "tpl_rec"}),
        assert_not_scheduled=assert_not_scheduled or (lambda bp, status: None),
    )
    return store, backend_mock


# ---------------------------------------------------------------------------
# create_blueprint
# ---------------------------------------------------------------------------


class TestCreateBlueprint:
    def _minimal(self) -> dict:
        return {"candidate_id": "cid_1", "story_id": "sid_1"}

    def test_returns_record_id_on_success(self) -> None:
        store, backend = _make_store()
        backend.create.return_value = {"id": "rec_42"}
        assert store.create_blueprint(self._minimal()) == "rec_42"

    def test_raises_value_error_when_story_missing(self) -> None:
        store, _ = _make_store(find_story=lambda sid, **kw: None)
        with pytest.raises(ValueError, match="Story sid_1 not found"):
            store.create_blueprint(self._minimal())

    def test_uses_passed_story_record_without_finder_call(self) -> None:
        seen = []

        def fake_find(sid, **kw):
            seen.append(sid)
            return None

        store, backend = _make_store(find_story=fake_find)
        store.create_blueprint(
            {"candidate_id": "cid_1", "story_id": "sid_1"},
            story_record={"id": "preloaded_rec"},
        )
        assert seen == []  # finder not called
        assert backend.create.call_args[0][1]["story"] == ["preloaded_rec"]

    def test_uses_passed_template_record_without_finder_call(self) -> None:
        seen = []

        def fake_find_tpl(tid, **kw):
            seen.append(tid)
            return None

        store, backend = _make_store(find_template=fake_find_tpl)
        store.create_blueprint(
            {"candidate_id": "cid_1", "story_id": "sid_1", "template_id": "t1"},
            template_record={"id": "preloaded_tpl"},
        )
        assert seen == []
        assert backend.create.call_args[0][1]["template"] == ["preloaded_tpl"]

    def test_template_empty_when_no_template_id(self) -> None:
        store, backend = _make_store()
        store.create_blueprint(self._minimal())
        assert backend.create.call_args[0][1]["template"] == []

    def test_template_empty_when_template_id_resolves_to_none(self) -> None:
        store, backend = _make_store(find_template=lambda tid, **kw: None)
        store.create_blueprint(
            {"candidate_id": "cid_1", "story_id": "sid_1", "template_id": "t_missing"}
        )
        assert backend.create.call_args[0][1]["template"] == []

    def test_status_defaults_to_intel_ready(self) -> None:
        store, backend = _make_store()
        store.create_blueprint(self._minimal())
        assert backend.create.call_args[0][1]["status"] == "INTEL_READY"

    def test_priority_score_defaults_to_half(self) -> None:
        store, backend = _make_store()
        store.create_blueprint(self._minimal())
        assert backend.create.call_args[0][1]["priority_score"] == 0.5

    def test_structure_text_overrides_structure_list(self) -> None:
        store, backend = _make_store()
        store.create_blueprint(
            {
                "candidate_id": "cid_1",
                "story_id": "sid_1",
                "structure_text": "preformatted text",
                "structure": ["IGNORED", "ALSO IGNORED"],
            }
        )
        assert backend.create.call_args[0][1]["structure"] == "preformatted text"

    def test_structure_list_joined_with_newlines_when_no_text(self) -> None:
        store, backend = _make_store()
        store.create_blueprint(
            {
                "candidate_id": "cid_1",
                "story_id": "sid_1",
                "structure": ["Hook", "Body 1", "CTA"],
            }
        )
        assert backend.create.call_args[0][1]["structure"] == "Hook\nBody 1\nCTA"

    def test_validation_flags_flattened(self) -> None:
        store, backend = _make_store()
        store.create_blueprint(
            {
                "candidate_id": "cid_1",
                "story_id": "sid_1",
                "validation_status": {
                    "constraints_passed": True,
                    "claims_passed": False,
                    "risk_acceptable": True,
                },
            }
        )
        fields = backend.create.call_args[0][1]
        assert fields["validation_constraints_passed"] is True
        assert fields["validation_claims_passed"] is False
        assert fields["validation_risk_acceptable"] is True

    def test_niche_id_only_included_when_provided(self) -> None:
        store, backend = _make_store()
        store.create_blueprint(self._minimal())
        assert "niche_id" not in backend.create.call_args[0][1]

        backend.reset_mock()
        store.create_blueprint({"candidate_id": "cid_1", "story_id": "sid_1", "niche_id": "gaming"})
        assert backend.create.call_args[0][1]["niche_id"] == "gaming"

    def test_clip_url_only_included_when_provided(self) -> None:
        store, backend = _make_store()
        store.create_blueprint(
            {
                "candidate_id": "cid_1",
                "story_id": "sid_1",
                "clip_url": "https://x.com/clip.mp4",
            }
        )
        assert backend.create.call_args[0][1]["clip_url"] == "https://x.com/clip.mp4"

    def test_optional_perf_fields_only_when_truthy(self) -> None:
        store, backend = _make_store()
        store.create_blueprint(
            {
                "candidate_id": "cid_1",
                "story_id": "sid_1",
                "topic_category": "BREAKING",
                "hook_formula": "QUESTION",
                "published_hour": 18,
                "published_day": "MON",
            }
        )
        fields = backend.create.call_args[0][1]
        assert fields["topic_category"] == "BREAKING"
        assert fields["hook_formula"] == "QUESTION"
        assert fields["published_hour"] == 18
        assert fields["published_day"] == "MON"

    @pytest.mark.parametrize(
        "err_msg",
        [
            "UNKNOWN_FIELD_NAME: clip_url",
            "columnNotFound: hook_formula",
            "Field 'published_hour' is not recognized",
        ],
    )
    def test_retries_without_optional_columns_on_known_errors(self, err_msg) -> None:
        # The retry mutates the same ``fields`` dict in place (via
        # ``.pop``), so we have to snapshot per-call to compare
        # before/after. ``call_args_list`` alone would show the
        # post-mutation state for both calls.
        snapshots: list[dict] = []

        def fake_create(_table, fields, **_kw):
            snapshots.append(dict(fields))  # shallow copy = snapshot
            if len(snapshots) == 1:
                raise Exception(err_msg)
            return {"id": "rec_after_retry"}

        store, backend = _make_store()
        backend.create.side_effect = fake_create

        result = store.create_blueprint(
            {
                "candidate_id": "cid_1",
                "story_id": "sid_1",
                "template_id": "t1",
                "clip_url": "u",
                "topic_category": "BREAKING",
            }
        )
        assert result == "rec_after_retry"
        first, second = snapshots
        assert "template_id" in first
        assert "template_id" not in second
        assert "topic_category" not in second
        assert "clip_url" not in second

    def test_other_exception_propagates(self) -> None:
        store, backend = _make_store()
        backend.create.side_effect = Exception("totally unrelated")
        with pytest.raises(Exception, match="totally unrelated"):
            store.create_blueprint(self._minimal())


# ---------------------------------------------------------------------------
# find_blueprint_by_candidate_id
# ---------------------------------------------------------------------------


class TestFindBlueprintByCandidateId:
    def test_returns_first_match(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = [{"id": "bp_1"}, {"id": "bp_2"}]
        assert store.find_blueprint_by_candidate_id("cid_1") == {"id": "bp_1"}

    def test_returns_none_when_not_found(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = []
        assert store.find_blueprint_by_candidate_id("cid_missing") is None

    def test_uses_candidate_id_formula(self) -> None:
        store, backend = _make_store()
        store.find_blueprint_by_candidate_id("cid_1")
        assert backend.find.call_args.kwargs["formula"] == "{candidate_id}='cid_1'"

    def test_escapes_quote_in_candidate_id(self) -> None:
        store, backend = _make_store()
        store.find_blueprint_by_candidate_id("cid'1")
        assert "cid\\'1" in backend.find.call_args.kwargs["formula"]

    def test_niche_id_passthrough(self) -> None:
        store, backend = _make_store()
        store.find_blueprint_by_candidate_id("cid_1", niche_id="sports")
        assert backend.find.call_args.kwargs["niche_id"] == "sports"

    def test_max_records_pinned_to_1(self) -> None:
        store, backend = _make_store()
        store.find_blueprint_by_candidate_id("cid_1")
        assert backend.find.call_args.kwargs["max_records"] == 1


# ---------------------------------------------------------------------------
# get_blueprints_by_status
# ---------------------------------------------------------------------------


class TestGetBlueprintsByStatus:
    def test_uses_status_formula(self) -> None:
        store, backend = _make_store()
        store.get_blueprints_by_status("DRAFTED")
        assert backend.find.call_args.kwargs["formula"] == "{status}='DRAFTED'"

    def test_escapes_quote_in_status(self) -> None:
        store, backend = _make_store()
        store.get_blueprints_by_status("D'RAFTED")
        assert "D\\'RAFTED" in backend.find.call_args.kwargs["formula"]

    def test_niche_id_passthrough(self) -> None:
        store, backend = _make_store()
        store.get_blueprints_by_status("DRAFTED", niche_id="movies")
        assert backend.find.call_args.kwargs["niche_id"] == "movies"

    def test_returns_find_results_verbatim(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = [{"id": "bp_a"}, {"id": "bp_b"}]
        assert store.get_blueprints_by_status("DRAFTED") == [
            {"id": "bp_a"},
            {"id": "bp_b"},
        ]


# ---------------------------------------------------------------------------
# get_blueprints_safe_to_cleanup
# ---------------------------------------------------------------------------


class TestGetBlueprintsSafeToCleanup:
    def test_filters_out_scheduled_blueprints(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = [
            {"id": "bp_safe", "fields": {"priority_score": 0.3}},
            {
                "id": "bp_scheduled",
                "fields": {
                    "priority_score": 0.3,
                    "scheduled_for": "2026-06-09T12:00:00Z",
                },
            },
        ]
        safe = store.get_blueprints_safe_to_cleanup("DRAFTED")
        assert len(safe) == 1
        assert safe[0]["id"] == "bp_safe"

    def test_filters_out_high_priority(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = [
            {"id": "bp_low", "fields": {"priority_score": 0.2}},
            {"id": "bp_high", "fields": {"priority_score": 0.9}},
        ]
        safe = store.get_blueprints_safe_to_cleanup("DRAFTED", max_priority=0.5)
        assert [bp["id"] for bp in safe] == ["bp_low"]

    def test_max_priority_default_keeps_all_unscheduled(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = [
            {"id": "bp_a", "fields": {"priority_score": 0.99}},
            {"id": "bp_b", "fields": {"priority_score": 0.01}},
        ]
        safe = store.get_blueprints_safe_to_cleanup("DRAFTED")
        assert len(safe) == 2

    def test_handles_flat_record_shape(self) -> None:
        # Postgres backend returns records WITHOUT a nested "fields"
        # key — they're flat dicts. The historical helper used
        # ``bp.get("fields", bp)`` to handle both. Pin that.
        store, backend = _make_store()
        backend.find.return_value = [
            {"id": "bp_flat", "priority_score": 0.3, "scheduled_for": None},
        ]
        safe = store.get_blueprints_safe_to_cleanup("DRAFTED")
        assert len(safe) == 1

    def test_treats_zero_priority_as_safe(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = [
            {"id": "bp_zero", "fields": {"priority_score": 0}},
            # None should also coerce to 0.
            {"id": "bp_none", "fields": {"priority_score": None}},
        ]
        safe = store.get_blueprints_safe_to_cleanup("DRAFTED", max_priority=0.5)
        assert len(safe) == 2


# ---------------------------------------------------------------------------
# update_blueprint_status
# ---------------------------------------------------------------------------


class TestUpdateBlueprintStatus:
    def test_raises_value_error_when_missing(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = []
        with pytest.raises(ValueError, match="Blueprint cid_missing not found"):
            store.update_blueprint_status("cid_missing", "DRAFTED")

    def test_calls_assert_not_scheduled_by_default(self) -> None:
        seen = []
        store, backend = _make_store(
            assert_not_scheduled=lambda bp, status: seen.append((bp["id"], status)),
        )
        backend.find.return_value = [{"id": "bp_1"}]
        store.update_blueprint_status("cid_1", "VISUAL_READY")
        assert seen == [("bp_1", "VISUAL_READY")]

    def test_force_skips_assert_not_scheduled(self) -> None:
        seen = []
        store, backend = _make_store(
            assert_not_scheduled=lambda bp, status: seen.append(status),
        )
        backend.find.return_value = [{"id": "bp_1"}]
        store.update_blueprint_status("cid_1", "DRAFTED", force=True)
        assert seen == []

    def test_assert_not_scheduled_value_error_propagates(self) -> None:
        def reject(bp, status):
            raise ValueError("scheduled — won't demote")

        store, backend = _make_store(assert_not_scheduled=reject)
        backend.find.return_value = [{"id": "bp_1"}]
        with pytest.raises(ValueError, match="scheduled"):
            store.update_blueprint_status("cid_1", "DRAFTED")
        backend.update.assert_not_called()

    def test_update_passes_typecast(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = [{"id": "bp_1"}]
        store.update_blueprint_status("cid_1", "VISUAL_READY")
        assert backend.update.call_args.kwargs == {"typecast": True}

    def test_kwargs_merged_into_update_fields(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = [{"id": "bp_1"}]
        store.update_blueprint_status(
            "cid_1",
            "VISUAL_READY",
            scheduled_for="2026-06-09T12:00:00Z",
        )
        args = backend.update.call_args[0]
        assert args[2] == {
            "status": "VISUAL_READY",
            "scheduled_for": "2026-06-09T12:00:00Z",
        }

    def test_niche_id_passed_to_find(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = [{"id": "bp_1"}]
        store.update_blueprint_status("cid_1", "VISUAL_READY", niche_id="gaming")
        assert backend.find.call_args.kwargs["niche_id"] == "gaming"


# ---------------------------------------------------------------------------
# batch_create_blueprints
# ---------------------------------------------------------------------------


class TestBatchCreateBlueprints:
    def test_caches_story_lookup_across_rows(self) -> None:
        story_calls: list[str] = []

        def fake_find_story(sid, **kw):
            story_calls.append(sid)
            return {"id": f"story-{sid}"}

        store, backend = _make_store(find_story=fake_find_story)
        backend.batch_create.return_value = [{"id": "r1"}, {"id": "r2"}, {"id": "r3"}]
        store.batch_create_blueprints(
            [
                {"candidate_id": "c1", "story_id": "s_shared"},
                {"candidate_id": "c2", "story_id": "s_shared"},
                {"candidate_id": "c3", "story_id": "s_other"},
            ]
        )
        # Two unique story_ids → 2 finder calls (not 3).
        assert sorted(story_calls) == ["s_other", "s_shared"]

    def test_caches_template_lookup_across_rows(self) -> None:
        template_calls: list[str] = []

        def fake_find_tpl(tid, **kw):
            template_calls.append(tid)
            return {"id": f"tpl-{tid}"}

        store, backend = _make_store(find_template=fake_find_tpl)
        backend.batch_create.return_value = [{"id": "r1"}, {"id": "r2"}]
        store.batch_create_blueprints(
            [
                {"candidate_id": "c1", "story_id": "s1", "template_id": "t_shared"},
                {"candidate_id": "c2", "story_id": "s1", "template_id": "t_shared"},
            ]
        )
        assert template_calls == ["t_shared"]

    def test_skips_blueprint_with_missing_story(self, caplog) -> None:
        store, backend = _make_store(
            find_story=lambda sid, **kw: None if sid == "s_missing" else {"id": "ok"},
        )
        backend.batch_create.return_value = [{"id": "r1"}]
        with caplog.at_level("WARNING"):
            result = store.batch_create_blueprints(
                [
                    {"candidate_id": "c1", "story_id": "s_missing"},
                    {"candidate_id": "c2", "story_id": "s_ok"},
                ]
            )
        assert result == ["r1"]
        # Only one record made it through.
        assert len(backend.batch_create.call_args[0][1]) == 1
        assert "Story s_missing not found" in caplog.text

    def test_returns_ids_in_order(self) -> None:
        store, backend = _make_store()
        backend.batch_create.return_value = [
            {"id": "r1"},
            {"id": "r2"},
            {"id": "r3"},
        ]
        result = store.batch_create_blueprints(
            [
                {"candidate_id": "c1", "story_id": "s1"},
                {"candidate_id": "c2", "story_id": "s2"},
                {"candidate_id": "c3", "story_id": "s3"},
            ]
        )
        assert result == ["r1", "r2", "r3"]

    def test_bulk_path_field_set_matches_legacy(self) -> None:
        # Pin the historical narrower field set on bulk path —
        # except for ``niche_id``, which PR #527 (2026-06-24)
        # deliberately adds because the bulk-path-without-niche
        # behaviour was exactly the SR-C bug we're closing
        # (rows landing without tenant binding). ``clip_url``
        # is still legitimately omitted on the bulk path
        # (the renderer-asset trail) — that's still legacy
        # behaviour and not a tenant-isolation concern.
        store, backend = _make_store()
        backend.batch_create.return_value = [{"id": "r1"}]
        store.batch_create_blueprints(
            [
                {
                    "candidate_id": "c1",
                    "story_id": "s1",
                    "clip_url": "would be ignored",
                    "niche_id": "gaming",
                }
            ]
        )
        row = backend.batch_create.call_args[0][1][0]
        assert "clip_url" not in row
        # PR #527: niche_id IS now passed through (was the SR-C bug).
        # See tests/http/test_blueprint_store_batch_niche_id.py for
        # the full dispatch-logic coverage.
        assert row.get("niche_id") == "gaming"
