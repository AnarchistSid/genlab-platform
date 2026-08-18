"""Pin NARR-01 storytime/NARR-01 mutex rule (Amendment A3, 2026-08-18).

storytime compositor and NARR-01 both produce narration-bearing reels
but via different audio shapes:
  * storytime: source video muted + TTS as PRIMARY audio
  * NARR-01:   source audio + music bed + VO (all present)

They must not fire simultaneously. Storytime wins when
variant_type=storytime AND GENLAB_STORYTIME_COMPOSITOR_ENABLED=1
because the storytime compositor already owns the narration render
path (frame_compositor.py:605 compose_storytime).

The mutex triggers a ``storytime_mutex`` degraded reason but per
the aggregation rule (plan §4) this is a ROUTING OUTCOME, not a
failure — excluded from degradation-rate aggregation.
"""
from __future__ import annotations

import pytest

from genlab_core.media.transformation_orchestrator import (
    _storytime_compositor_enabled,
)


class TestStorytimeCompositorFlag:
    """The mutex needs to read the same flag the compositor reads."""

    @pytest.mark.parametrize("val", ["1", "true", "yes", "on"])
    def test_on_tokens(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_STORYTIME_COMPOSITOR_ENABLED", val)
        assert _storytime_compositor_enabled() is True

    @pytest.mark.parametrize("val", ["", "0", "false", "no", "off"])
    def test_off_tokens(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_STORYTIME_COMPOSITOR_ENABLED", val)
        assert _storytime_compositor_enabled() is False

    def test_unset_off(self, monkeypatch):
        monkeypatch.delenv(
            "GENLAB_STORYTIME_COMPOSITOR_ENABLED", raising=False,
        )
        assert _storytime_compositor_enabled() is False


class TestMutexRoutingIsExcludedFromAggregation:
    """Structural pin — storytime_mutex is NOT in the failure-reason
    aggregation set. Any operator query that filters degradation by
    reason must exclude this slug because it's routing not failure.

    This test doesn't execute the orchestrator — it asserts the plan
    documents the aggregation rule so future authors can grep for
    'storytime_mutex' and land in the right context.
    """

    def test_plan_documents_aggregation_exclusion(self):
        """The plan file must explicitly call out that storytime_mutex
        is excluded from degradation aggregation."""
        import pathlib

        plan_path = (
            pathlib.Path(__file__).parents[3]
            / ".audit" / "NARR-01-plan.md"
        )
        if not plan_path.exists():
            pytest.skip("plan file not present in this checkout")
        src = plan_path.read_text()
        # The exact aggregation rule must be discoverable via grep
        assert "storytime_mutex" in src
        assert "ROUTING OUTCOME" in src.upper() or "routing outcome" in src
        assert "excluded" in src.lower() or "exclude" in src.lower()

    def test_transformation_orchestrator_documents_mutex(self):
        """The orchestrator's storytime branch must log that this is
        a routing outcome, not a degradation. Grep for the string
        in the source — the log line is the operator-facing signal."""
        import pathlib

        src = (
            pathlib.Path(__file__).parents[2]
            / "src" / "genlab_core" / "media"
            / "transformation_orchestrator.py"
        ).read_text()
        assert "storytime_mutex" in src
        # Log line must name it as a routing outcome for operator clarity
        assert "routing outcome" in src.lower()
