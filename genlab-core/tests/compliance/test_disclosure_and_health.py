"""PR #566 (2026-06-25) — disclosure.py + account_health.py stubs.

Both modules ship the public-surface API in #566; the actual
per-platform logic plugs in via PRs #567 (disclosure wire) and
#570 (account health monitoring).

Pin the API shape today so downstream PRs have a stable contract
to build against.
"""

from __future__ import annotations

# ── AI disclosure ─────────────────────────────────────────────────


def test_disclosure_covers_all_publish_platforms():
    """Every platform our pipeline publishes to MUST have a
    disclosure string. Catches missing-platform omission BEFORE
    PR #567 wires the disclosure into publish_all_platforms (which
    would silently skip disclosure for any missing platform)."""
    from genlab_core.compliance.disclosure import AI_DISCLOSURE_BY_PLATFORM

    required_platforms = {"instagram", "youtube", "facebook", "tiktok", "x_twitter", "threads"}
    for platform in required_platforms:
        assert platform in AI_DISCLOSURE_BY_PLATFORM
        text = AI_DISCLOSURE_BY_PLATFORM[platform]
        assert text, f"empty disclosure for {platform}"
        # Each disclosure should mention 'AI' explicitly so platforms'
        # automated detectors recognize the disclaimer
        assert "AI" in text or "ai" in text


def test_generate_returns_disclosure_for_known_platform():
    from genlab_core.compliance.disclosure import generate_ai_disclosure

    for platform in ["instagram", "youtube", "tiktok", "facebook", "x_twitter", "threads"]:
        text = generate_ai_disclosure(platform)
        assert text
        assert isinstance(text, str)


def test_generate_returns_empty_for_unknown_platform():
    """Unknown platform → '' (caller proceeds without disclosure).
    Pin tests in PR #567 catch missing platforms; this fail-safe
    just keeps the system running."""
    from genlab_core.compliance.disclosure import generate_ai_disclosure

    assert generate_ai_disclosure("rumble") == ""
    assert generate_ai_disclosure("") == ""


def test_generate_is_case_insensitive():
    """Operators may pass 'Instagram' or 'INSTAGRAM' — should
    resolve to the lowercase canonical."""
    from genlab_core.compliance.disclosure import generate_ai_disclosure

    assert generate_ai_disclosure("INSTAGRAM") == generate_ai_disclosure("instagram")
    assert generate_ai_disclosure("YouTube") == generate_ai_disclosure("youtube")


def test_youtube_disclosure_mentions_creator_credit():
    """YouTube's 2024 policy specifically calls out 'altered or
    synthetic media that depicts realistic people/events'. Our
    rendered reels use third-party clips; the disclosure MUST
    mention attribution to avoid Content ID / strike risk."""
    from genlab_core.compliance.disclosure import generate_ai_disclosure

    text = generate_ai_disclosure("youtube").lower()
    assert "creator" in text or "credit" in text or "original" in text


# ── Account health stub ──────────────────────────────────────────


def test_health_signal_dataclass_frozen():
    """Same safety rationale as Tenant / User / ComplianceDecision —
    auth-path callers can't mutate health state mid-evaluation."""
    import dataclasses

    import pytest
    from genlab_core.compliance.account_health import HealthSignal

    s = HealthSignal(state="healthy", message="ok")
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.state = "critical"  # type: ignore[misc]


def test_health_signal_state_literal_values():
    """The 3 valid states are pinned in the dataclass typing;
    runtime construction with any string would NOT raise (dataclass
    Literal is type-hint only). The CONSUMERS (PR #570 + dashboard)
    rely on these 3 values to render correctly."""
    from genlab_core.compliance.account_health import HealthSignal

    # Sanity: all 3 documented states construct
    HealthSignal(state="healthy", message="ok")
    HealthSignal(state="warning", message="reach dropped")
    HealthSignal(state="critical", message="2 strikes")


def test_get_signal_stub_returns_none(monkeypatch, caplog):
    """PR #566 stub — get_signal always returns None until PR #570
    plugs in per-platform implementations. Critical: this lets the
    Mission Control dashboard card ship in observation mode without
    waiting for real platform monitoring."""
    import logging

    from genlab_core.compliance.account_health import get_signal

    with caplog.at_level(logging.DEBUG):
        result = get_signal("instagram", "gaming")
    assert result is None


def test_get_signal_empty_inputs_return_none_safely():
    """Defensive: empty platform or niche → None, no crash."""
    from genlab_core.compliance.account_health import get_signal

    assert get_signal("", "gaming") is None
    assert get_signal("instagram", "") is None
    assert get_signal("", "") is None


def test_health_signal_evidence_defaults_empty_dict():
    from genlab_core.compliance.account_health import HealthSignal

    s = HealthSignal(state="healthy", message="ok")
    assert s.evidence == {}
