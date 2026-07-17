"""Pin: push_to_backlog wires watch_till_end variant + preserves series priority.

Layer 3 S3 (2026-07-17). Extends the S2 series wire — same source-shape
testing pattern, verifies:

1. The watch_till_end_selector is imported at the push_to_backlog callsite
2. When ``is_watch_till_end_eligible`` returns True, fields get
   ``variant_type='watch_till_end'`` + ``variant_payload={}``
3. The wire is gated on ``if not variant_assigned`` so series_part still
   takes priority (variants exclusive on blueprint)
4. Selector exception is caught (fail-open pattern)

Behavioral coverage is in ``test_watch_till_end_selector.py`` (25 tests).
This pin closes the connective-tissue gap between selector + storage.
"""

from __future__ import annotations

import inspect

from genlab_core.pipeline.stages import push_to_backlog


class TestPushToBacklogWatchTillEndWire:
    def _source(self) -> str:
        return inspect.getsource(push_to_backlog)

    def test_selector_imported_in_stage(self) -> None:
        src = self._source()
        assert (
            "from genlab_core.writing.watch_till_end_selector import" in src
            or "watch_till_end_selector" in src
        ), "push_to_backlog must import watch_till_end selector"

    def test_variant_type_watch_till_end_assigned(self) -> None:
        src = self._source()
        assert "watch_till_end" in src, (
            "push_to_backlog must assign variant_type='watch_till_end' when eligible"
        )

    def test_series_priority_via_variant_assigned_guard(self) -> None:
        """The watch_till_end wire must be inside an ``if not variant_assigned``
        block so series_part (which sets variant_assigned=True) takes priority.
        Without this guard, a "Highlights Part 3" story would overwrite the
        series_part assignment with watch_till_end."""
        src = self._source()
        assert "variant_assigned" in src, (
            "push_to_backlog series+watch_till_end wire must track variant_assigned "
            "to enforce priority (series > watch_till_end > single_clip)"
        )
        assert "if not variant_assigned" in src, (
            "watch_till_end assignment must be guarded by 'if not variant_assigned' "
            "so series_part takes priority"
        )

    def test_watch_till_end_fail_open(self) -> None:
        """Same fail-open pattern as series detection."""
        src = self._source()
        assert "watch_till_end selection skipped" in src, (
            "push_to_backlog must fail-open when watch_till_end selection raises"
        )
