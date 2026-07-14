"""Pin tests for the Threads CDN rotation gate (2026-07-14).

Session 2026-07-14 audit found ai_creators Threads publishing failing
2 days in a row with Meta's own ``"UNKNOWN"`` error_message. Extending
the retry gate to include this marker gives an intermittent Meta-side
processing failure a chance to recover via CDN rotation, at the cost
of one extra upload attempt per failure.

These tests pin the class-of-bug: rotation gate must be a NAMED helper
(not an inline substring check) so future marker additions/removals go
through a single reviewable surface. They also pin case-sensitivity —
Meta emits ``"UNKNOWN"`` literally, so a case-insensitive match would
false-fire on any error containing the word.
"""

from __future__ import annotations

from genlab_core.platforms.threads import (
    _CDN_ROTATION_ERROR_MARKERS,
    _cdn_rotation_worthwhile,
)


class TestCdnRotationWorthwhile:
    def test_2207077_documented_cdn_fetch_failure(self):
        assert _cdn_rotation_worthwhile("Threads: video 2207077 CDN fetch failed")

    def test_unknown_meta_opaque_error(self):
        assert _cdn_rotation_worthwhile("container processing error: UNKNOWN")

    def test_token_error_not_worthwhile(self):
        assert not _cdn_rotation_worthwhile("Access token invalid")

    def test_video_spec_violation_not_worthwhile(self):
        assert not _cdn_rotation_worthwhile("Video codec H.265 not supported")

    def test_empty_error_not_worthwhile(self):
        assert not _cdn_rotation_worthwhile("")

    def test_case_sensitive_unknown_only(self):
        """Meta emits UNKNOWN literally — lower-case must NOT match.

        Without case-sensitivity, ``"unknown error occurred"`` would
        false-trigger CDN rotation on generic client-side errors.
        """
        assert not _cdn_rotation_worthwhile("unknown error occurred")
        assert not _cdn_rotation_worthwhile("Unknown host error")


class TestCdnRotationMarkerSet:
    """Pin the marker set shape — additions here must be intentional."""

    def test_markers_frozenset(self):
        assert isinstance(_CDN_ROTATION_ERROR_MARKERS, frozenset)

    def test_current_markers_are_2207077_and_UNKNOWN(self):
        assert _CDN_ROTATION_ERROR_MARKERS == frozenset({"2207077", "UNKNOWN"})

    def test_no_lowercase_markers(self):
        """Any lowercase marker would false-fire on English error text."""
        for marker in _CDN_ROTATION_ERROR_MARKERS:
            assert marker.upper() == marker, (
                f"Marker {marker!r} contains lowercase — would false-fire"
            )
