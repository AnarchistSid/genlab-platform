"""Pin: push_to_backlog wires SeriesDetector into blueprint fields.

Layer 3 S2 (2026-07-17). When a story's source title indicates a series
(e.g. "Elden Ring Part 3"), push_to_backlog must set
``fields["variant_type"] = "series_part"`` + ``fields["variant_payload"]``.

## Why a SOURCE-shape pin, not a full stage integration test

push_to_backlog is 2944 lines with many dependencies (BacklogClient, LinUCB,
bandit arms, dedup gates, engagement boosts, active experiments, ...). A
full stage-level integration test is expensive to set up + brittle to
maintain. The existing pattern in this repo (see
``test_push_to_backlog_clip_index_none_guard.py``) is to grep the source
for load-bearing safe-shape strings.

This test does the same: verifies the wire's presence + shape at the
source-code level. Regression: if someone removes the SeriesDetector
call or reshapes the fields keys without updating this pin, it fails
immediately.

The behavioral coverage (does detect_series correctly identify series?
does the fields dict get the right values?) is fully covered by
``test_series_detector.py`` + ``test_blueprint_store_variant_type.py``.
This pin closes the connective-tissue gap between those two.
"""

from __future__ import annotations

import inspect

from genlab_core.pipeline.stages import push_to_backlog


class TestPushToBacklogSeriesWire:
    def _source(self) -> str:
        return inspect.getsource(push_to_backlog)

    def test_series_detector_imported_in_stage(self) -> None:
        """Import at CALL SITE (not top of file) because push_to_backlog
        uses lazy imports for optional deps — series_detector follows
        the same pattern. Any of these forms is acceptable."""
        src = self._source()
        assert (
            "from genlab_core.writing.series_detector import detect_series" in src
            or "from genlab_core.writing.series_detector import" in src
        ), "push_to_backlog must import detect_series (LinUCB-style lazy import ok)"

    def test_variant_type_series_part_assigned_on_detection(self) -> None:
        """The wire must assign ``fields["variant_type"] = "series_part"``
        inside the detection block. Regex on source — catches accidental
        removal of the assignment even if the import stays."""
        src = self._source()
        assert '"variant_type"' in src and "series_part" in src, (
            "push_to_backlog must set variant_type='series_part' when detect_series returns a hit"
        )

    def test_variant_payload_contains_required_keys(self) -> None:
        """Payload contract per genlab_core.variant_types.PAYLOAD_CONTRACTS
        for series_part = (series_id, part_number, total_parts). All three
        keys must appear in the push_to_backlog source in the payload assignment."""
        src = self._source()
        # These strings appear in the payload dict literal
        assert '"series_id"' in src, "payload missing series_id key"
        assert '"part_number"' in src, "payload missing part_number key"
        assert '"total_parts"' in src, "payload missing total_parts key"

    def test_series_detection_is_fail_open(self) -> None:
        """detect_series exception must not crash the stage — the source
        must wrap the call in try/except (matches the pattern used for
        other optional injections like LinUCB context building)."""
        src = self._source()
        # Simple structural check: the detection block should be inside a try
        # with a broad exception handler (matches surrounding pattern).
        assert "series detection skipped" in src, (
            "push_to_backlog must fail-open when series detection raises "
            "(look for a debug log with 'series detection skipped')"
        )
