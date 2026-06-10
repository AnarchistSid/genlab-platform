"""Tests for :mod:`genlab_core.platforms.meta_metric_deprecation`.

Audit ref: R-44 part 2 — observability for Meta metric deprecation.
"""

from __future__ import annotations

import pytest
from genlab_core.platforms import meta_metric_deprecation as mmd


@pytest.fixture(autouse=True)
def _reset_counters():
    mmd.reset_counters()
    yield
    mmd.reset_counters()


# ---------------------------------------------------------------------------
# DEPRECATED_METRICS registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_registry_has_known_entries(self) -> None:
        # Sanity: the IG `plays` deprecation is the most important one
        # for our metric_collector path right now. Pin it.
        assert "plays" in mmd.DEPRECATED_METRICS
        note, hint = mmd.DEPRECATED_METRICS["plays"]
        assert "v22" in note
        assert "views" in hint.lower()

    def test_every_entry_has_note_and_hint(self) -> None:
        for name, (note, hint) in mmd.DEPRECATED_METRICS.items():
            assert note, f"{name}: empty deprecation note"
            assert hint, f"{name}: empty replacement hint"


# ---------------------------------------------------------------------------
# warn_if_deprecated
# ---------------------------------------------------------------------------


class TestWarnIfDeprecated:
    def test_warns_on_known_deprecated_string(self, caplog) -> None:
        with caplog.at_level("WARNING"):
            mmd.warn_if_deprecated("plays")
        assert "deprecated" in caplog.text.lower()
        assert "plays" in caplog.text

    def test_warns_on_known_deprecated_list(self, caplog) -> None:
        with caplog.at_level("WARNING"):
            mmd.warn_if_deprecated(["plays", "reach"])
        # `reach` isn't in the registry (yet); `plays` is.
        assert "plays" in caplog.text
        assert caplog.text.lower().count("deprecated") >= 1

    def test_accepts_comma_separated_meta_request_shape(self, caplog) -> None:
        with caplog.at_level("WARNING"):
            mmd.warn_if_deprecated("plays,reach,likes,comments")
        assert "plays" in caplog.text

    def test_no_warn_when_all_metrics_are_current(self, caplog) -> None:
        with caplog.at_level("WARNING"):
            mmd.warn_if_deprecated(["likes", "reach", "shares"])
        # `likes`, `reach`, `shares` aren't in the registry today.
        assert "deprecated" not in caplog.text.lower()

    def test_context_suffix_appears_in_log(self, caplog) -> None:
        with caplog.at_level("WARNING"):
            mmd.warn_if_deprecated("plays", context="ig_reels")
        assert "[ig_reels]" in caplog.text

    def test_handles_whitespace_in_comma_string(self, caplog) -> None:
        with caplog.at_level("WARNING"):
            mmd.warn_if_deprecated("  plays  ,  reach  ")
        assert "plays" in caplog.text


# ---------------------------------------------------------------------------
# record_observation — silent zero-out detector
# ---------------------------------------------------------------------------


class TestRecordObservation:
    def test_non_zero_keeps_counter_at_zero(self) -> None:
        mmd.record_observation("views", 123)
        assert mmd.get_counter("views") == 0

    def test_zero_increments_counter(self) -> None:
        mmd.record_observation("views", 0)
        mmd.record_observation("views", 0)
        mmd.record_observation("views", 0)
        assert mmd.get_counter("views") == 3

    def test_non_zero_resets_counter(self) -> None:
        mmd.record_observation("views", 0)
        mmd.record_observation("views", 0)
        mmd.record_observation("views", 42)
        assert mmd.get_counter("views") == 0

    def test_no_error_below_threshold(self, caplog) -> None:
        with caplog.at_level("ERROR"):
            for _ in range(mmd._ZERO_OUT_THRESHOLD - 1):
                mmd.record_observation("views", 0)
        assert "SILENT ZERO-OUT" not in caplog.text

    def test_emits_error_at_threshold(self, caplog) -> None:
        with caplog.at_level("ERROR"):
            for _ in range(mmd._ZERO_OUT_THRESHOLD):
                mmd.record_observation("views", 0)
        assert "SILENT ZERO-OUT" in caplog.text
        assert "views" in caplog.text

    def test_error_fires_exactly_once_at_threshold(self, caplog) -> None:
        # The threshold check is "==", not ">=", to avoid log spam
        # on subsequent zeros after the first emit.
        with caplog.at_level("ERROR"):
            for _ in range(mmd._ZERO_OUT_THRESHOLD + 10):
                mmd.record_observation("views", 0)
        # Count ERROR emits.
        assert caplog.text.count("SILENT ZERO-OUT") == 1

    def test_recovery_emits_info_after_threshold_breach(self, caplog) -> None:
        # Reach the threshold first, then return to non-zero — should
        # emit a recovery INFO so operators see the metric is alive again.
        with caplog.at_level("ERROR"):
            for _ in range(mmd._ZERO_OUT_THRESHOLD):
                mmd.record_observation("views", 0)
        with caplog.at_level("INFO"):
            mmd.record_observation("views", 99)
        assert "recovered" in caplog.text
        assert mmd.get_counter("views") == 0

    def test_no_recovery_log_when_threshold_never_reached(self, caplog) -> None:
        # 3 zeros then a non-zero — no recovery log (we never alerted).
        mmd.record_observation("views", 0)
        mmd.record_observation("views", 0)
        mmd.record_observation("views", 0)
        with caplog.at_level("INFO"):
            mmd.record_observation("views", 1)
        assert "recovered" not in caplog.text

    def test_scope_isolates_counters(self) -> None:
        # The same metric name in different scopes (e.g. fb-feed vs
        # fb-video) must have independent counters.
        mmd.record_observation("plays", 0, scope="ig_reels")
        mmd.record_observation("plays", 0, scope="ig_reels")
        mmd.record_observation("plays", 99, scope="ig_basic")
        assert mmd.get_counter("plays", scope="ig_reels") == 2
        assert mmd.get_counter("plays", scope="ig_basic") == 0


# ---------------------------------------------------------------------------
# reset_counters — test helper
# ---------------------------------------------------------------------------


class TestResetCounters:
    def test_clears_all_counters(self) -> None:
        mmd.record_observation("views", 0)
        mmd.record_observation("reach", 0, scope="fb")
        assert mmd.get_counter("views") == 1
        assert mmd.get_counter("reach", scope="fb") == 1
        mmd.reset_counters()
        assert mmd.get_counter("views") == 0
        assert mmd.get_counter("reach", scope="fb") == 0
