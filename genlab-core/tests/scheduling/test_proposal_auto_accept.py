"""Tests for proposal_auto_accept classifier."""

from __future__ import annotations

import pytest


class TestFlagGate:
    def test_off_by_default(self, monkeypatch):
        from genlab_core.scheduling.proposal_auto_accept import is_enabled

        monkeypatch.delenv("GENLAB_PROPOSAL_AUTO_ACCEPT_ENABLED", raising=False)
        assert is_enabled() is False

    def test_strict_true_only(self, monkeypatch):
        from genlab_core.scheduling.proposal_auto_accept import is_enabled

        monkeypatch.setenv("GENLAB_PROPOSAL_AUTO_ACCEPT_ENABLED", "1")
        assert is_enabled() is False
        monkeypatch.setenv("GENLAB_PROPOSAL_AUTO_ACCEPT_ENABLED", "true")
        assert is_enabled() is True


class TestConfidenceGate:
    def test_low_confidence_rejects(self):
        from genlab_core.scheduling.proposal_auto_accept import classify_arm_add

        d = classify_arm_add(
            {"type": "arm_add", "proposed": {"arm_id": "style:gaming:absurd"}},
            existing_arm_ids=frozenset({"style:gaming:comparison"}),
            proposal_confidence="low",
        )
        assert d.should_auto_accept is False
        assert "confidence" in d.reason

    def test_medium_confidence_rejects(self):
        from genlab_core.scheduling.proposal_auto_accept import classify_arm_add

        d = classify_arm_add(
            {"type": "arm_add", "proposed": {"arm_id": "style:gaming:absurd"}},
            existing_arm_ids=frozenset({"style:gaming:comparison"}),
            proposal_confidence="medium",
        )
        assert d.should_auto_accept is False


class TestStyleVariantAutoAccept:
    def test_extending_existing_style_dimension_auto_accepts(self):
        from genlab_core.scheduling.proposal_auto_accept import classify_arm_add

        d = classify_arm_add(
            {"type": "arm_add", "proposed": {"arm_id": "style:gaming:absurd"}},
            existing_arm_ids=frozenset({
                "style:gaming:comparison",
                "style:gaming:question",
                "clip",
            }),
            proposal_confidence="high",
        )
        assert d.should_auto_accept is True
        assert "style_variant" in d.reason

    def test_first_style_arm_operator_gates(self):
        """When the niche has NO existing style arms, adding the first
        one is a dimensional expansion — operator review required."""
        from genlab_core.scheduling.proposal_auto_accept import classify_arm_add

        d = classify_arm_add(
            {"type": "arm_add", "proposed": {"arm_id": "style:new_niche:variant"}},
            existing_arm_ids=frozenset({"clip", "trailer"}),
            proposal_confidence="high",
        )
        assert d.should_auto_accept is False
        assert "first_style_arm_for_niche" in d.reason


class TestTransformVariantAutoAccept:
    def test_extending_existing_transform_dim_auto_accepts(self):
        from genlab_core.scheduling.proposal_auto_accept import classify_arm_add

        d = classify_arm_add(
            {
                "type": "arm_add",
                "proposed": {"arm_id": "transform__caption_pacing__450"},
            },
            existing_arm_ids=frozenset({
                "transform__caption_pacing__600",
                "transform__caption_pacing__700",
            }),
            proposal_confidence="high",
        )
        assert d.should_auto_accept is True
        assert "transform_variant" in d.reason

    def test_new_transform_dim_operator_gates(self):
        from genlab_core.scheduling.proposal_auto_accept import classify_arm_add

        d = classify_arm_add(
            {
                "type": "arm_add",
                "proposed": {"arm_id": "transform__brand_new_dim__foo"},
            },
            existing_arm_ids=frozenset({
                "transform__caption_pacing__600",
            }),
            proposal_confidence="high",
        )
        assert d.should_auto_accept is False
        assert "first_transform_dim" in d.reason


class TestSourceOperatorGate:
    def test_any_new_source_operator_gates(self):
        """Sources broaden the fetch surface + interact with quota
        planning. Always operator scope regardless of confidence."""
        from genlab_core.scheduling.proposal_auto_accept import classify_arm_add

        d = classify_arm_add(
            {"type": "arm_add", "proposed": {"arm_id": "source:vimeo_trending"}},
            existing_arm_ids=frozenset({"source:youtube_trending"}),
            proposal_confidence="high",
        )
        assert d.should_auto_accept is False
        assert "new_source" in d.reason


class TestUnknownShape:
    def test_unknown_arm_id_shape_operator_gates(self):
        from genlab_core.scheduling.proposal_auto_accept import classify_arm_add

        d = classify_arm_add(
            {"type": "arm_add", "proposed": {"arm_id": "weird_new_shape:foo:bar"}},
            existing_arm_ids=frozenset(),
            proposal_confidence="high",
        )
        assert d.should_auto_accept is False
        assert "unknown_shape" in d.reason


class TestMalformedProposal:
    def test_missing_type_skips(self):
        from genlab_core.scheduling.proposal_auto_accept import classify_arm_add

        d = classify_arm_add({}, proposal_confidence="high")
        assert d.should_auto_accept is False
        assert d.reason.startswith("skip:")

    def test_missing_arm_id_skips(self):
        from genlab_core.scheduling.proposal_auto_accept import classify_arm_add

        d = classify_arm_add(
            {"type": "arm_add", "proposed": {}},
            proposal_confidence="high",
        )
        assert d.should_auto_accept is False
        assert d.reason.startswith("skip:")

    def test_non_arm_add_type_skips(self):
        from genlab_core.scheduling.proposal_auto_accept import classify_arm_add

        d = classify_arm_add(
            {"type": "some_other_action", "proposed": {"arm_id": "x"}},
            proposal_confidence="high",
        )
        assert d.reason == "skip:not_arm_add"


class TestRateLimitConstant:
    """Pin the max value so operator dashboards + downstream aggregators
    can rely on it."""

    def test_max_is_two_per_week(self):
        from genlab_core.scheduling.proposal_auto_accept import (
            MAX_AUTO_ACCEPTS_PER_WEEK,
        )

        assert MAX_AUTO_ACCEPTS_PER_WEEK == 2
