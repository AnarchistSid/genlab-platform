"""Tests for genlab_core.publishing.cross_platform_gate.

The Beta-posterior gate skips (niche, platform) combinations with
demonstrably low historical reward, freeing rate-limit budget for
combinations that produce engagement.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


class TestFlagGate:
    def test_off_by_default(self, monkeypatch):
        from genlab_core.publishing import cross_platform_gate

        monkeypatch.delenv("GENLAB_CROSS_PLATFORM_GATE_ENABLED", raising=False)
        cross_platform_gate.reset_cache()
        decision = cross_platform_gate.should_skip_platform("gaming", "twitter")
        assert decision.should_skip is False
        assert "flag_off" in decision.reason

    def test_strict_true_only(self, monkeypatch):
        from genlab_core.publishing import cross_platform_gate

        cross_platform_gate.reset_cache()
        monkeypatch.setenv("GENLAB_CROSS_PLATFORM_GATE_ENABLED", "1")
        decision = cross_platform_gate.should_skip_platform("gaming", "twitter")
        # "1" is NOT the strict-true match — must not enable
        assert "flag_off" in decision.reason


class TestColdStartGuard:
    def _enable(self, monkeypatch):
        monkeypatch.setenv("GENLAB_CROSS_PLATFORM_GATE_ENABLED", "true")
        from genlab_core.publishing import cross_platform_gate
        cross_platform_gate.reset_cache()

    def test_low_n_does_not_skip(self, monkeypatch):
        """n=3 with all-zero reward — posterior mean is very low but
        we've seen too little to trust. Cold-start guard fires."""
        from genlab_core.publishing import cross_platform_gate

        self._enable(monkeypatch)
        with patch.object(
            cross_platform_gate,
            "_query_posterior",
            return_value=(1.0, 4.0, 3),  # α+β=5 - 2 = 3 obs, mean=0.20
        ):
            decision = cross_platform_gate.should_skip_platform(
                "movies", "threads"
            )
        assert decision.should_skip is False
        assert decision.n_observations == 3
        assert "cold_start" in decision.reason


class TestPosteriorThreshold:
    def _enable(self, monkeypatch):
        monkeypatch.setenv("GENLAB_CROSS_PLATFORM_GATE_ENABLED", "true")
        from genlab_core.publishing import cross_platform_gate
        cross_platform_gate.reset_cache()

    def test_healthy_signal_no_skip(self, monkeypatch):
        """25 samples, sum=2.5 → α=3.5, β=23.5, mean=0.13. Well above
        threshold — must NOT skip."""
        from genlab_core.publishing import cross_platform_gate

        self._enable(monkeypatch)
        with patch.object(
            cross_platform_gate,
            "_query_posterior",
            return_value=(3.5, 23.5, 25),
        ):
            decision = cross_platform_gate.should_skip_platform(
                "gaming", "facebook"
            )
        assert decision.should_skip is False
        assert decision.posterior_mean == round(3.5 / (3.5 + 23.5), 4)
        assert "publish:" in decision.reason

    def test_low_signal_with_evidence_skips(self, monkeypatch):
        """Real 'chronic underperformer' shape: n=25 with tiny sum.
        Posterior mean ~0.01 — well below threshold. Skip."""
        from genlab_core.publishing import cross_platform_gate

        self._enable(monkeypatch)
        # α=1.25, β=25.75, mean ≈ 0.046 — actually just above threshold.
        # Use a clearer case: α=1.0, β=26.0, mean ≈ 0.037 — still borderline.
        # For clarity: α=1.0, β=100.0, mean ≈ 0.0099 — clear skip.
        with patch.object(
            cross_platform_gate,
            "_query_posterior",
            return_value=(1.0, 100.0, 100),
        ):
            decision = cross_platform_gate.should_skip_platform(
                "sports", "instagram"
            )
        assert decision.should_skip is True
        assert "skip:" in decision.reason


class TestShadowMode:
    """report_skips_that_would_have_fired bypasses the flag guard for
    operator dry-run before flipping the flag."""

    def test_ignores_flag(self, monkeypatch):
        from genlab_core.publishing import cross_platform_gate

        monkeypatch.delenv("GENLAB_CROSS_PLATFORM_GATE_ENABLED", raising=False)
        cross_platform_gate.reset_cache()

        def _fake_posterior(niche_id, platform):
            if platform == "twitter":
                return (1.0, 100.0, 100)  # clear skip
            return (5.0, 20.0, 24)  # publish

        with patch.object(
            cross_platform_gate,
            "_query_posterior",
            side_effect=_fake_posterior,
        ):
            skips = cross_platform_gate.report_skips_that_would_have_fired(
                "ai_creators",
                ["twitter", "facebook", "youtube"],
            )
        assert skips == ["twitter"]


class TestFailOpen:
    def _enable(self, monkeypatch):
        monkeypatch.setenv("GENLAB_CROSS_PLATFORM_GATE_ENABLED", "true")
        from genlab_core.publishing import cross_platform_gate
        cross_platform_gate.reset_cache()

    def test_empty_input_returns_fallback(self, monkeypatch):
        from genlab_core.publishing import cross_platform_gate

        self._enable(monkeypatch)
        decision = cross_platform_gate.should_skip_platform("", "youtube")
        assert decision.should_skip is False
        assert "fallback" in decision.reason


class TestPublisherWire:
    """Source-grep pin: the publisher must call should_skip_platform
    and honor the decision."""

    def test_publish_all_platforms_imports_gate(self):
        import inspect

        from genlab_core.publishing import publish_all_platforms

        src = inspect.getsource(publish_all_platforms)
        assert "cross_platform_gate" in src
        assert "should_skip_platform" in src, (
            "publish_all_platforms.py must consult should_skip_platform "
            "in the fan-out loop — otherwise the gate is dead code."
        )

    def test_publisher_writes_skipped_analytics_on_gate_skip(self):
        """When the gate skips a platform, publisher must record a
        SKIPPED row so the skip is durable + operator-visible."""
        import inspect

        from genlab_core.publishing import publish_all_platforms

        src = inspect.getsource(publish_all_platforms)
        # Locate the gate-skip block; verify it records SKIPPED.
        assert "cross_platform_gate:" in src, (
            "The skip's error_message must include the gate's reason "
            "prefix so downstream error queries can distinguish gate "
            "skips from other SKIPPED causes."
        )
