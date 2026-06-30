"""Pin: LinUCB context degradation is no longer silent.

Pre-2026-06-21 the contextual bandit silently fell back to Thompson
Sampling on three paths in ``_default_bandit_updater``:
  (a) ``bandit_context`` not provided (cold-start, legacy blueprints)
  (b) context vector wrong dimensionality (producer bug)
  (c) context construction raised (feature-extraction bug)

All three were invisible: no log, no metric. A single mis-built context
vector could disable contextual updates indefinitely across 5 niches ×
~18 arms without any operator-facing signal.

Lever D2 instruments the path. These pins verify the three counter
keys fire correctly + the WARN-logged paths actually emit warnings.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_counter():
    """Each test starts with an empty counter — prevents cross-test
    pollution from making later assertions trivially pass/fail."""
    from genlab_core.learning.metric_collector import (
        _reset_linucb_degradation_for_tests,
    )

    _reset_linucb_degradation_for_tests()
    yield
    _reset_linucb_degradation_for_tests()


class TestDegradationCounter:
    def test_record_linucb_degradation_increments(self):
        """Counter starts empty, increments by (niche_id, reason)."""
        from genlab_core.learning.metric_collector import (
            _record_linucb_degradation,
            get_linucb_degradation_stats,
        )

        assert get_linucb_degradation_stats() == {}
        _record_linucb_degradation("gaming", "context_shape_mismatch")
        assert get_linucb_degradation_stats() == {("gaming", "context_shape_mismatch"): 1}
        _record_linucb_degradation("gaming", "context_shape_mismatch")
        assert get_linucb_degradation_stats() == {("gaming", "context_shape_mismatch"): 2}

    def test_record_distinguishes_niche_and_reason(self):
        """Different (niche, reason) pairs are tracked independently —
        gaming shape-mismatch is NOT pooled with sports shape-mismatch."""
        from genlab_core.learning.metric_collector import (
            _record_linucb_degradation,
            get_linucb_degradation_stats,
        )

        _record_linucb_degradation("gaming", "context_shape_mismatch")
        _record_linucb_degradation("sports", "context_shape_mismatch")
        _record_linucb_degradation("gaming", "no_context_provided")
        stats = get_linucb_degradation_stats()
        assert stats[("gaming", "context_shape_mismatch")] == 1
        assert stats[("sports", "context_shape_mismatch")] == 1
        assert stats[("gaming", "no_context_provided")] == 1

    def test_get_stats_returns_copy_not_internal_dict(self):
        """External mutation of the returned dict must not affect the
        internal counter — defensive isolation."""
        from genlab_core.learning.metric_collector import (
            _record_linucb_degradation,
            get_linucb_degradation_stats,
        )

        _record_linucb_degradation("gaming", "no_context_provided")
        snapshot = get_linucb_degradation_stats()
        snapshot[("hacked", "key")] = 999
        # Re-query — the internal state should be untouched.
        assert ("hacked", "key") not in get_linucb_degradation_stats()

    def test_empty_niche_id_becomes_unknown_marker(self):
        """When niche_id is empty (legacy paths), the key uses
        ``<unknown>`` so the counter is never silently lost."""
        from genlab_core.learning.metric_collector import (
            _record_linucb_degradation,
            get_linucb_degradation_stats,
        )

        _record_linucb_degradation("", "no_context_provided")
        assert ("<unknown>", "no_context_provided") in get_linucb_degradation_stats()


class TestBanditUpdaterRecordsDegradation:
    """The three fall-through paths in ``_default_bandit_updater`` must
    each bump their corresponding counter. These tests directly exercise
    the ``bandit_context`` branch — the rest of ``_default_bandit_updater``
    is heavily mocked because the goal is to pin the degradation paths,
    not re-test the bandit reward math."""

    def _invoke_with_context(self, bandit_context, caplog):
        """Helper: invoke ``_default_bandit_updater`` with the given
        context and capture the (degradation_counter, warning_records)
        pair. The bandit's reward + proxy + SQL paths are stubbed.

        ``BacklogClient`` is lazy-imported inside the function under
        test, so we patch its import source (``http.backlog_client``)
        rather than the metric_collector module."""
        from genlab_core.learning.metric_collector import (
            _default_bandit_updater,
            get_linucb_degradation_stats,
        )

        fake_proxy = MagicMock()
        fake_proxy.all.return_value = []
        fake_client = MagicMock()
        fake_client.bandit_arms = fake_proxy

        with (
            patch(
                "genlab_core.http.backlog_client.BacklogClient",
                return_value=fake_client,
            ),
            caplog.at_level(logging.WARNING, logger="genlab_core.learning.metric_collector"),
        ):
            _default_bandit_updater(
                niche_id="gaming",
                content_type="esports_highlight",
                platform="youtube",
                reward=0.5,
                bandit_context=bandit_context,
            )

        return get_linucb_degradation_stats(), caplog.records

    def test_no_context_provided_counts_silently(self, caplog):
        """When ``bandit_context`` is None, the counter bumps but no
        WARN is emitted (legitimate cold-start path)."""
        stats, warnings = self._invoke_with_context(None, caplog)
        assert stats.get(("gaming", "no_context_provided")) == 1
        # No WARN-level records about LinUCB degradation
        linucb_warnings = [r for r in warnings if "LinUCB" in r.getMessage()]
        assert linucb_warnings == [], (
            f"Expected no LinUCB warnings for legitimate cold-start path; got: "
            f"{[r.getMessage() for r in linucb_warnings]}"
        )

    def test_context_shape_mismatch_counts_and_warns(self, caplog):
        """When context vector is the wrong dim, counter bumps AND a
        WARN is emitted naming the actual vs expected dim — operator
        gets actionable signal."""
        # Wrong dim — pass a 6-element vector when CONTEXT_DIM is 12
        bad_context = {"linucb_context": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]}
        stats, warnings = self._invoke_with_context(bad_context, caplog)
        assert stats.get(("gaming", "context_shape_mismatch")) == 1
        linucb_warnings = [r for r in warnings if "LinUCB" in r.getMessage()]
        assert len(linucb_warnings) >= 1, "Expected at least one LinUCB warning"
        # The warning text should name the actual + expected dims so
        # operators can debug the producer.
        msg = linucb_warnings[0].getMessage()
        assert "6" in msg, f"Expected actual dim (6) in warning: {msg}"
        # The expected dim should be CONTEXT_DIM (currently 13 after
        # Phase 2 L2 added content_type_showcase). Read it dynamically
        # so this test doesn't break when CONTEXT_DIM changes again.
        from genlab_core.learning.linucb import CONTEXT_DIM

        assert str(CONTEXT_DIM) in msg, f"Expected CONTEXT_DIM={CONTEXT_DIM} in warning: {msg}"
        assert "gaming" in msg, f"Expected niche_id in warning: {msg}"

    def test_context_build_exception_counts_and_warns(self, caplog):
        """When context construction raises (e.g. corrupt input that
        ``len()`` can't handle), counter bumps and WARN is emitted."""

        # Pass an object whose len() raises — simulates a corrupt
        # feature-extraction output. We use a custom class because
        # MagicMock's __len__ would auto-spec a return value.
        class _Unsizeable:
            def __len__(self):
                raise TypeError("simulated corrupt context")

        bad_context = {"linucb_context": _Unsizeable()}
        stats, warnings = self._invoke_with_context(bad_context, caplog)
        assert stats.get(("gaming", "context_build_exception")) == 1
        linucb_warnings = [r for r in warnings if "LinUCB context build failed" in r.getMessage()]
        assert len(linucb_warnings) >= 1, "Expected at least one 'context build failed' warning"
