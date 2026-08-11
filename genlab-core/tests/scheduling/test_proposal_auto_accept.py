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


class TestRiskFieldFallback:
    """2026-08-11 Bug 3: real strategist proposals emit ``risk``
    (low/medium/high), NOT ``confidence``. Original code required
    proposal_confidence='high' kwarg — but the caller (auto_accept
    script) doesn't have anywhere to pull that from because
    strategist doesn't emit a confidence field per-proposal.

    Prod discovery: 0/25 proposals in 30 days had a `confidence`
    field, so the field-name mismatch was silently blocking every
    auto-accept for months. Fix: fall back to proposal.risk when
    proposal_confidence is empty. risk='low' → treat as high
    confidence (safe to auto-accept)."""

    def test_risk_low_treated_as_high_confidence(self):
        """The load-bearing fix: a proposal with risk='low' and no
        explicit proposal_confidence kwarg auto-accepts."""
        from genlab_core.scheduling.proposal_auto_accept import classify_arm_add

        d = classify_arm_add(
            {
                "type": "arm_add",
                "risk": "low",  # THE fix — was ignored, now unlocks auto-accept
                "proposed": {"arm_id": "style:anime:tier_list_reaction"},
            },
            existing_arm_ids=frozenset({"style:anime:bold_claim"}),
            proposal_confidence="",  # empty — no explicit confidence
        )
        assert d.should_auto_accept is True, (
            f"risk='low' proposal must auto-accept when style dimension "
            f"already exists. Regression: reverting the fix re-introduces "
            f"the 0/25-auto-accepts-in-30-days silent-dead state. Got: {d}"
        )
        assert "auto_accept" in d.reason

    def test_risk_medium_stays_gated(self):
        """risk='medium' proposals still need operator review — no
        fallback to high confidence."""
        from genlab_core.scheduling.proposal_auto_accept import classify_arm_add

        d = classify_arm_add(
            {
                "type": "arm_add",
                "risk": "medium",
                "proposed": {"arm_id": "style:anime:character_debate"},
            },
            existing_arm_ids=frozenset({"style:anime:bold_claim"}),
            proposal_confidence="",
        )
        assert d.should_auto_accept is False

    def test_risk_high_stays_gated(self):
        """risk='high' definitively rejects — never gets auto-accepted."""
        from genlab_core.scheduling.proposal_auto_accept import classify_arm_add

        d = classify_arm_add(
            {
                "type": "arm_add",
                "risk": "high",
                "proposed": {"arm_id": "style:gaming:weird_experiment"},
            },
            existing_arm_ids=frozenset({"style:gaming:comparison"}),
            proposal_confidence="",
        )
        assert d.should_auto_accept is False

    def test_explicit_confidence_overrides_risk_field(self):
        """If caller passes proposal_confidence explicitly (e.g. from
        causal_hypotheses lookup), that takes precedence over risk field.
        Backward compat: existing callers that pass confidence='high'
        with no risk in proposal still work."""
        from genlab_core.scheduling.proposal_auto_accept import classify_arm_add

        # Explicit high confidence + high risk → still auto-accept
        # (explicit confidence signal is more trusted than risk heuristic)
        d = classify_arm_add(
            {
                "type": "arm_add",
                "risk": "high",  # would normally block
                "proposed": {"arm_id": "style:sports:reversal"},
            },
            existing_arm_ids=frozenset({"style:sports:comparison"}),
            proposal_confidence="high",  # explicit — wins
        )
        assert d.should_auto_accept is True

    def test_no_risk_no_confidence_stays_gated(self):
        """Defensive: proposal with neither field stays gated."""
        from genlab_core.scheduling.proposal_auto_accept import classify_arm_add

        d = classify_arm_add(
            {"type": "arm_add", "proposed": {"arm_id": "style:x:y"}},
            existing_arm_ids=frozenset({"style:x:a"}),
            proposal_confidence="",
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


class TestProposedFieldStringHandling:
    """Prod discovery 2026-07-24: strategist writes ``proposed`` as
    JSON-encoded string, not a dict. 9/9 arm_add proposals in the 5
    unreviewed prod reports skipped as malformed. Classifier must
    defensively parse JSON strings + reject narrative prose.

    Sibling to test_missing_type_skips — these test the shape
    normalisation guard."""

    def test_proposed_as_json_string_parses_and_classifies(self):
        from genlab_core.scheduling.proposal_auto_accept import classify_arm_add

        # Real prod shape: proposed is a JSON string carrying arm_id.
        d = classify_arm_add(
            {
                "type": "arm_add",
                "proposed": '{"arm_id": "style:gaming:aggressive"}',
            },
            existing_arm_ids=frozenset({"style:gaming:cautious"}),
            proposal_confidence="high",
        )
        # Should auto_accept the style variant, NOT skip.
        assert d.should_auto_accept is True
        assert d.reason.startswith("auto_accept:style_variant")

    def test_proposed_as_narrative_prose_skips_cleanly(self):
        from genlab_core.scheduling.proposal_auto_accept import classify_arm_add

        # Real prod shape: some proposed fields are narrative strings
        # like "Force-explore each of the 5 uninitiated transform arms...".
        # These are not structured — should skip without raising.
        d = classify_arm_add(
            {
                "type": "arm_add",
                "proposed": "Force-explore each of the 5 uninitiated arms.",
            },
            proposal_confidence="high",
        )
        assert d.should_auto_accept is False
        assert "narrative" in d.reason

    def test_proposed_as_unparseable_json_skips_cleanly(self):
        from genlab_core.scheduling.proposal_auto_accept import classify_arm_add

        # Malformed JSON string — should skip, not raise.
        d = classify_arm_add(
            {"type": "arm_add", "proposed": "{malformed json here"},
            proposal_confidence="high",
        )
        # Not JSON-looking (missing closing brace) — falls to narrative branch.
        assert d.should_auto_accept is False
        assert "narrative" in d.reason or "unparseable" in d.reason


class TestRateLimitConstant:
    """Pin the max value so operator dashboards + downstream aggregators
    can rely on it."""

    def test_max_is_two_per_week(self):
        from genlab_core.scheduling.proposal_auto_accept import (
            MAX_AUTO_ACCEPTS_PER_WEEK,
        )

        assert MAX_AUTO_ACCEPTS_PER_WEEK == 2
