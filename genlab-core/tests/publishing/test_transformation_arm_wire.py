"""Pin tests for the full transformation-arm-attribution wire (task #581).

Round-2 audit (2026-07-08) traced the "255 transformation arms have zero
observations" symptom to a discarded return value at
``post_render_transform.py:apply_post_render_transformations``. The
function ran the per-dimension bandit selectors correctly, but only
returned the composite path — the ``arm_ids_by_dimension`` dict was
thrown away. Down the pipeline, every hop worked:

* ``metric_collector.py:1077`` correctly reads
  ``task_record.arm_ids_by_dimension`` and calls the reward router
* ``transformation_reward_router.route_transformation_rewards`` correctly
  updates per-dimension bandit arms

But the source dict was always empty because the FIRST link in the chain
threw it away. Pre-fix: 100% of ``pending_feedback`` rows had
``arm_ids_by_dimension = {}`` verified via prod query 2026-07-08:

    SELECT COUNT(*), COUNT(*) FILTER (WHERE arm_ids_by_dimension IS NOT
    NULL AND arm_ids_by_dimension::text NOT IN ('null', '{}'))
    FROM pending_feedback WHERE created_at >= CURRENT_DATE - INTERVAL '30 days'
    → total=273, with_arms=0

The fix is a 5-hop wire, and these tests pin each hop so a future
refactor at any layer can't silently disconnect the chain again:

  1. ``apply_post_render_transformations()`` returns ``(path, dict)``
  2. ``base_visual_render._compose_frame`` writes to
     ``story["arm_ids_by_dimension"]``
  3. ``push_to_backlog`` copies ``story[...] → fields[...]`` (JSON)
  4. ``register_pending_feedback`` decodes back to dict + passes to
     ``PendingFeedbackTask(arm_ids_by_dimension=...)``
  5. Metric collector reads ``task.arm_ids_by_dimension`` (already
     tested elsewhere — not re-pinned here)

See ``docs/AUDIT-2026-07-08.md`` §11.2 and
``audit-round-4-2026-07-08.md`` for the full trace.
"""

from __future__ import annotations

from genlab_core.publishing.feedback_registration import (
    _parse_arm_ids_by_dimension,
)


class TestParseArmIdsByDimension:
    """Hop 4: register_pending_feedback decodes ``fields["arm_ids_by_dimension"]``.

    ``push_to_backlog`` writes it as a JSON string. Postgres may return it
    as either str or dict depending on the storage backend. The parser
    must accept both AND handle every degenerate input (empty, null,
    malformed JSON) without raising — silent failure keeps publishing
    healthy even if the transformation subsystem produces garbage.
    """

    def test_dict_passthrough(self):
        """Native dict from JSONB decode passes through with str-coerced values."""
        raw = {"music_mood": "transform__music_mood__energetic"}
        assert _parse_arm_ids_by_dimension(raw) == {
            "music_mood": "transform__music_mood__energetic"
        }

    def test_json_string_decoded(self):
        """String from text col gets JSON-decoded."""
        raw = '{"caption_style": "transform__caption_style__bold"}'
        assert _parse_arm_ids_by_dimension(raw) == {
            "caption_style": "transform__caption_style__bold"
        }

    def test_multi_dimension_dict(self):
        """The typical shape — several dims, one arm each."""
        raw = {
            "highlight_moment": "transform__highlight_moment__audio_peak",
            "music_mood": "transform__music_mood__energetic",
            "caption_style": "transform__caption_style__bold",
        }
        parsed = _parse_arm_ids_by_dimension(raw)
        assert len(parsed) == 3
        assert parsed["highlight_moment"].startswith("transform__highlight_moment__")

    def test_empty_dict_returns_empty(self):
        assert _parse_arm_ids_by_dimension({}) == {}

    def test_empty_string_returns_empty(self):
        assert _parse_arm_ids_by_dimension("") == {}

    def test_none_returns_empty(self):
        assert _parse_arm_ids_by_dimension(None) == {}

    def test_malformed_json_returns_empty(self):
        """Malformed JSON must NOT raise — publishing keeps working
        even if transformation subsystem produces garbage."""
        assert _parse_arm_ids_by_dimension('{"broken: json"') == {}

    def test_json_list_not_dict_returns_empty(self):
        """Correct JSON but wrong shape (list, not object) → empty."""
        assert _parse_arm_ids_by_dimension('["not", "a", "dict"]') == {}

    def test_json_null_returns_empty(self):
        """JSON ``null`` decodes to Python None → empty."""
        assert _parse_arm_ids_by_dimension("null") == {}

    def test_non_str_non_dict_returns_empty(self):
        """Numbers, tuples, other unexpected types → empty."""
        assert _parse_arm_ids_by_dimension(42) == {}
        assert _parse_arm_ids_by_dimension(("a", "b")) == {}

    def test_values_coerced_to_str(self):
        """Bandit arm_id is text col; values must be str."""
        raw = {"dim": 12345}  # accidentally-int value
        assert _parse_arm_ids_by_dimension(raw) == {"dim": "12345"}

    def test_empty_key_or_value_filtered(self):
        """Empty keys or empty values are meaningless — filter them."""
        raw = {"": "arm_x", "dim1": "", "dim2": "arm_y"}
        assert _parse_arm_ids_by_dimension(raw) == {"dim2": "arm_y"}


class TestReturnTypePinContractStable:
    """Hop 1: ``apply_post_render_transformations`` MUST return a
    2-tuple ``(str, dict)``. If someone reverts to a bare-str return,
    every consumer downstream silently loses arm attribution again.

    This test doesn't invoke the function (that's covered by
    ``test_post_render_transform_wire.py`` + ``_min_duration_guard.py``)
    — it just asserts the annotated return type shape via
    ``typing.get_type_hints``, so a naive revert would fail at type
    check even before running the tests.
    """

    def test_return_annotation_is_tuple_of_str_and_dict(self):
        import inspect
        import typing

        from genlab_core.media import post_render_transform

        sig = inspect.signature(post_render_transform.apply_post_render_transformations)
        hints = typing.get_type_hints(post_render_transform.apply_post_render_transformations)
        return_hint = hints.get("return")
        assert return_hint is not None, (
            "apply_post_render_transformations has no return annotation — "
            "task #581 requires tuple[str, dict[str, str]] to route "
            "transformation arm attribution."
        )
        # Python typing: get_origin returns tuple; get_args returns (str, dict).
        origin = typing.get_origin(return_hint)
        assert origin is tuple, (
            f"Return type must be tuple, got {origin!r}. If you reverted "
            f"to bare-str return, 255 transformation arms silently lose "
            f"reward attribution. See task #581 + "
            f"``audit-round-4-2026-07-08.md``."
        )
        args = typing.get_args(return_hint)
        assert len(args) == 2, f"Return tuple must be 2-tuple, got {len(args)}"
        assert args[0] is str, f"First tuple element must be str, got {args[0]!r}"
        # Second element: dict[str, str] — assert it's a dict-origin
        dict_arg = args[1]
        dict_origin = typing.get_origin(dict_arg)
        assert dict_origin is dict, (
            f"Second tuple element must be dict, got {dict_origin!r}. "
            f"Task #581 requires arm_ids_by_dimension: dict[str, str]."
        )

        # Also verify the signature has the callers' kwargs unchanged
        # (defense against someone "cleaning up" the API without
        # migrating the 3 caller sites).
        params = set(sig.parameters.keys())
        assert "niche_id" in params
        assert "niche_root" in params
        assert "visuals_yaml_path" in params
        assert "blueprint_context" in params
        assert "video_duration_s" in params


class TestPushToBacklogWiresStoryToFields:
    """Hop 3: ``push_to_backlog`` must copy
    ``story["arm_ids_by_dimension"] → fields["arm_ids_by_dimension"]``
    (JSON-serialized).

    Grep-pin: verifies the source contains the copy sites. A real
    functional test would require the full backlog client + DB — too
    heavy for a unit-test-scoped pin. This grep-pin catches the
    regression (someone deletes the block during a refactor) in ~1 ms.
    """

    def test_push_to_backlog_source_contains_arm_ids_write(self):
        from pathlib import Path

        # Resolve from this test's location so CI + worktree checkouts work.
        repo_root = Path(__file__).resolve().parents[3]
        path = (
            repo_root
            / "genlab-core"
            / "src"
            / "genlab_core"
            / "pipeline"
            / "stages"
            / "push_to_backlog.py"
        )
        src = path.read_text()
        assert 'story.get("arm_ids_by_dimension")' in src, (
            f"{path}: push_to_backlog must READ story['arm_ids_by_dimension']. "
            f"Task #581 wire hop 3. Removed = 255 transformation arms silently "
            f"stop accumulating observations again."
        )
        assert 'fields["arm_ids_by_dimension"]' in src, (
            f"{path}: push_to_backlog must WRITE fields['arm_ids_by_dimension']. "
            f"Removed = downstream feedback_registration reads None."
        )


class TestBaseVisualRenderWritesStoryKey:
    """Hop 2: ``base_visual_render._compose_frame`` must unpack the tuple
    return AND write to ``story["arm_ids_by_dimension"]``. Same grep-pin
    approach — catches deletion during refactor.

    Also verifies BlackboxBrief's parallel strategy file has the same
    unpacking (`bb_strategies/visual_render.py`) — the 2 strategy files
    are the ONLY callers of `apply_post_render_transformations`.
    """

    def test_base_visual_render_unpacks_arm_ids(self):
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[3]
        path = (
            repo_root
            / "genlab-core"
            / "src"
            / "genlab_core"
            / "strategies"
            / "base_visual_render.py"
        )
        if not path.is_file():
            import pytest

            pytest.skip(f"{path} not in this checkout — nothing to pin")
        src = path.read_text()
        assert "composite_path, _arm_ids = apply_post_render_transformations(" in src, (
            f"{path}: must unpack the tuple return from "
            f"apply_post_render_transformations. Reverting to bare-str "
            f"assignment loses arm attribution for sports+movies+anime."
        )
        assert 'story["arm_ids_by_dimension"] = dict(_arm_ids)' in src, (
            f"{path}: must write arm_ids to the story dict so push_to_backlog "
            f"can copy it into fields. Deleted = wire breaks."
        )

    def test_bb_visual_render_unpacks_arm_ids(self):
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[3]
        path = repo_root / "BlackboxBrief" / "bb_strategies" / "visual_render.py"
        if not path.is_file():
            import pytest

            pytest.skip(f"{path} not in this checkout — nothing to pin")
        src = path.read_text()
        assert "result, _arm_ids = apply_post_render_transformations(" in src, (
            f"{path}: BlackboxBrief must ALSO unpack the tuple. Sibling to "
            f"base_visual_render — ai_creators bandit arms depend on it."
        )
        assert 'story["arm_ids_by_dimension"] = dict(_arm_ids)' in src


class TestFeedbackRegistrationPassesArmIds:
    """Hop 4b: ``register_pending_feedback`` must pass
    ``arm_ids_by_dimension`` to ``PendingFeedbackTask(...)``. Without
    this final passthrough, all the upstream wiring is wasted.
    """

    def test_feedback_registration_source_contains_pass(self):
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[3]
        path = (
            repo_root
            / "genlab-core"
            / "src"
            / "genlab_core"
            / "publishing"
            / "feedback_registration.py"
        )
        src = path.read_text()
        assert "_parse_arm_ids_by_dimension(" in src, (
            f"{path}: must call _parse_arm_ids_by_dimension helper. "
            f"Deleted = arm_ids never make it to PendingFeedbackTask."
        )
        assert "arm_ids_by_dimension=arm_ids_by_dim" in src, (
            f"{path}: PendingFeedbackTask() kwarg missing. Task #581 hop 4."
        )
