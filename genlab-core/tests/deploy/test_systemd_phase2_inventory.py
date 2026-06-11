"""R-05: pin the engagement-poller unit's presence in ``systemd-phase2/``.

Before R-05 the file lived only in ``deploy/systemd/`` (phase 1). A clean
phase-2 re-bootstrap would have silently shipped FB/IG-only engagement
(Meta webhooks cover IG + FB; YT/X/Threads need the poller). The live
prod box happened to carry the unit from a manual install — but the
source-of-truth bundle didn't, so any rebuild would lose it.

This test is a regression pin: future drift between the phase-2
bundle and the engagement architecture will fail CI, not silently
ship at 3am when nobody's looking.
"""

from __future__ import annotations

from pathlib import Path

# Walk up from ``genlab-core/tests/deploy/test_*.py`` to the workspace root.
_GENLAB_ROOT = Path(__file__).resolve().parents[3]
_PHASE2_DIR = _GENLAB_ROOT / "deploy" / "systemd-phase2"


def test_r05_engagement_poller_unit_is_in_phase2() -> None:
    """The engagement-poller systemd unit must be installable from the
    phase-2 bundle. The audit's R-05 was a configuration-drift finding —
    pin it shut."""
    poller_unit = _PHASE2_DIR / "genlab-engagement-poller.service"
    assert poller_unit.is_file(), (
        f"R-05 regression: {poller_unit} is missing. "
        "The phase-2 deploy bundle must ship the engagement poller — "
        "without it, a clean re-bootstrap leaves YT/X/Threads "
        "engagement dark (Meta webhooks only cover FB/IG)."
    )


def test_r05_engagement_poller_unit_has_dispatch_dramatiq() -> None:
    """The poller must dispatch to Dramatiq — otherwise comments are
    polled and logged but never replied to (observe mode)."""
    poller_unit = _PHASE2_DIR / "genlab-engagement-poller.service"
    content = poller_unit.read_text()
    assert "ENGAGEMENT_DISPATCH=dramatiq" in content, (
        "Engagement poller must be configured with ENGAGEMENT_DISPATCH=dramatiq "
        "so polled comments reach the engagement worker queue. Without it, "
        "the poller runs in observe-only mode and no replies ship."
    )


def test_r05_engagement_poller_sets_agent_root() -> None:
    """AGENT_ROOT must be set (matches the engagement-worker unit).
    The 2026-05-20 incident left engagement broken for 40 days because
    a missing AGENT_ROOT silently retry-exhausted every task into the
    DLQ. Pin the env var presence."""
    poller_unit = _PHASE2_DIR / "genlab-engagement-poller.service"
    content = poller_unit.read_text()
    assert "Environment=AGENT_ROOT=" in content, (
        "Engagement poller is missing AGENT_ROOT — this is the trap that "
        "caused 29k DLQ messages and 40 days of broken engagement in the "
        "2026-05-20 forensic. Worker unit sets it; poller must too."
    )
