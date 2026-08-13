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

    def test_unknown_shape_with_consensus_2_auto_accepts(self):
        """2026-08-14: consensus_count>=2 unlocks unknown-shape fallback.
        Codifies tonight's manual heuristic — repeated LLM emission of
        same proposal across weeks is corroborating evidence."""
        from genlab_core.scheduling.proposal_auto_accept import classify_arm_add

        d = classify_arm_add(
            {"type": "arm_add", "proposed": {"arm_id": "weird_new_shape:foo:bar"}},
            existing_arm_ids=frozenset(),
            proposal_confidence="high",
            consensus_count=2,
        )
        assert d.should_auto_accept is True
        assert "consensus_unknown_shape" in d.reason

    def test_unknown_shape_with_consensus_1_still_gates(self):
        """Single-week unknown shape still requires operator review."""
        from genlab_core.scheduling.proposal_auto_accept import classify_arm_add

        d = classify_arm_add(
            {"type": "arm_add", "proposed": {"arm_id": "weird_new_shape:foo:bar"}},
            existing_arm_ids=frozenset(),
            proposal_confidence="high",
            consensus_count=1,
        )
        assert d.should_auto_accept is False


class TestHourArmClassification:
    """2026-08-14: hour:H:platform:niche arm shape support.
    Tonight's manual review found several hour:20:instagram:gaming
    proposals that fell to unknown_shape."""

    def test_hour_arm_with_existing_peer_auto_accepts(self):
        from genlab_core.scheduling.proposal_auto_accept import classify_arm_add

        d = classify_arm_add(
            {"type": "arm_add", "proposed": {"arm_id": "hour:20:instagram:gaming"}},
            existing_arm_ids=frozenset({"hour:6:instagram:gaming"}),
            proposal_confidence="high",
        )
        assert d.should_auto_accept is True
        assert "hour_variant" in d.reason

    def test_hour_arm_no_peer_operator_gates(self):
        from genlab_core.scheduling.proposal_auto_accept import classify_arm_add

        d = classify_arm_add(
            {"type": "arm_add", "proposed": {"arm_id": "hour:20:instagram:gaming"}},
            existing_arm_ids=frozenset(),
            proposal_confidence="high",
        )
        assert d.should_auto_accept is False
        assert "first_hour_arm" in d.reason

    def test_hour_arm_malformed_out_of_range(self):
        from genlab_core.scheduling.proposal_auto_accept import classify_arm_add

        d = classify_arm_add(
            {"type": "arm_add", "proposed": {"arm_id": "hour:25:instagram:gaming"}},
            existing_arm_ids=frozenset({"hour:6:instagram:gaming"}),
            proposal_confidence="high",
        )
        assert d.should_auto_accept is False
        assert "malformed_hour_arm_id" in d.reason

    def test_hour_arm_wrong_platform_no_auto_accept(self):
        """Peer with different platform shouldn't unlock — bandit
        dimensions are per (platform, niche)."""
        from genlab_core.scheduling.proposal_auto_accept import classify_arm_add

        d = classify_arm_add(
            {"type": "arm_add", "proposed": {"arm_id": "hour:20:instagram:gaming"}},
            existing_arm_ids=frozenset({"hour:6:facebook:gaming"}),  # different platform
            proposal_confidence="high",
        )
        assert d.should_auto_accept is False


class TestAutoRejectStale:
    def test_manual_action_with_spearman_marker_rejects(self):
        from genlab_core.scheduling.proposal_auto_accept import classify_reject_stale

        d = classify_reject_stale({
            "type": "manual_action",
            "target": "operator.attention",
            "current": "Spearman=0.0 for 7 consecutive days",
            "proposed": "Audit reward signal pipeline",
        })
        assert d.should_auto_accept is True
        assert "stale_reward_signal_marker" in d.reason

    def test_manual_action_with_reward_signal_broken_rejects(self):
        from genlab_core.scheduling.proposal_auto_accept import classify_reject_stale

        d = classify_reject_stale({
            "type": "manual_action",
            "current": "system state ok",
            "proposed": "The current reward signal broken. Fix now.",
        })
        assert d.should_auto_accept is True

    def test_non_manual_action_skipped(self):
        from genlab_core.scheduling.proposal_auto_accept import classify_reject_stale

        d = classify_reject_stale({
            "type": "arm_add",
            "current": "Spearman=0.0",
        })
        assert d.should_auto_accept is False
        assert "not_manual_action" in d.reason

    def test_no_stale_marker_keeps(self):
        from genlab_core.scheduling.proposal_auto_accept import classify_reject_stale

        d = classify_reject_stale({
            "type": "manual_action",
            "current": "gaming pipeline needs baseline instrumentation",
        })
        assert d.should_auto_accept is False


class TestAutoRejectScope:
    def test_tiktok_mention_rejects(self):
        from genlab_core.scheduling.proposal_auto_accept import classify_reject_scope

        d = classify_reject_scope({
            "type": "manual_action",
            "proposed": "Distribute to TikTok for viral reach",
        })
        assert d.should_auto_accept is True
        assert "scope_violation_rule_23" in d.reason

    def test_paid_boost_rejects(self):
        from genlab_core.scheduling.proposal_auto_accept import classify_reject_scope

        d = classify_reject_scope({
            "type": "manual_action",
            "proposed": "Run a minimal paid boost ($5-20) on top-performing FB post",
        })
        assert d.should_auto_accept is True

    def test_organic_action_keeps(self):
        from genlab_core.scheduling.proposal_auto_accept import classify_reject_scope

        d = classify_reject_scope({
            "type": "manual_action",
            "proposed": "Post more content to Facebook",  # in-scope
        })
        assert d.should_auto_accept is False


class TestPerTypeRateLimits:
    def test_per_type_dict_exists(self):
        from genlab_core.scheduling.proposal_auto_accept import (
            MAX_AUTO_ACCEPTS_PER_TYPE_PER_WEEK,
        )
        for t in ("arm_add", "reward_weight", "novelty_rate", "gate_threshold"):
            assert t in MAX_AUTO_ACCEPTS_PER_TYPE_PER_WEEK

    def test_arm_add_more_permissive_than_gate_threshold(self):
        from genlab_core.scheduling.proposal_auto_accept import (
            MAX_AUTO_ACCEPTS_PER_TYPE_PER_WEEK,
        )
        assert (MAX_AUTO_ACCEPTS_PER_TYPE_PER_WEEK["arm_add"]
                > MAX_AUTO_ACCEPTS_PER_TYPE_PER_WEEK["gate_threshold"])

    def test_env_override_respects_bounds(self, monkeypatch):
        from genlab_core.scheduling.proposal_auto_accept import (
            get_max_auto_accepts_per_week,
        )
        monkeypatch.setenv("GENLAB_MAX_AUTO_ACCEPTS_PER_WEEK", "10")
        assert get_max_auto_accepts_per_week() == 10

    def test_env_override_clamps_high(self, monkeypatch):
        from genlab_core.scheduling.proposal_auto_accept import (
            get_max_auto_accepts_per_week,
        )
        monkeypatch.setenv("GENLAB_MAX_AUTO_ACCEPTS_PER_WEEK", "9999")
        assert get_max_auto_accepts_per_week() == 20

    def test_env_override_clamps_low(self, monkeypatch):
        from genlab_core.scheduling.proposal_auto_accept import (
            get_max_auto_accepts_per_week,
        )
        monkeypatch.setenv("GENLAB_MAX_AUTO_ACCEPTS_PER_WEEK", "0")
        assert get_max_auto_accepts_per_week() == 1

    def test_env_missing_falls_back(self, monkeypatch):
        from genlab_core.scheduling.proposal_auto_accept import (
            MAX_AUTO_ACCEPTS_PER_WEEK, get_max_auto_accepts_per_week,
        )
        monkeypatch.delenv("GENLAB_MAX_AUTO_ACCEPTS_PER_WEEK", raising=False)
        assert get_max_auto_accepts_per_week() == MAX_AUTO_ACCEPTS_PER_WEEK

    def test_env_bad_value_falls_back(self, monkeypatch):
        from genlab_core.scheduling.proposal_auto_accept import (
            MAX_AUTO_ACCEPTS_PER_WEEK, get_max_auto_accepts_per_week,
        )
        monkeypatch.setenv("GENLAB_MAX_AUTO_ACCEPTS_PER_WEEK", "not_an_int")
        assert get_max_auto_accepts_per_week() == MAX_AUTO_ACCEPTS_PER_WEEK


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


class TestClassifyRewardWeight:
    """2026-08-11 Session 2: reward_weight classifier pins.

    Consumer (reward_shaper.py:295) REPLACES the weight and clamps to
    [0.0, 5.0]. Classifier adds a relative-change gate on top so
    auto-accept can't swing a metric wildly in one step. Same
    confidence/risk gate as classify_arm_add.
    """

    def _high_conf(self, **kwargs) -> dict:
        base = {"type": "reward_weight", "risk": "low"}
        base.update(kwargs)
        return base

    def test_wrong_type_skips(self):
        from genlab_core.scheduling.proposal_auto_accept import (
            classify_reward_weight,
        )

        d = classify_reward_weight(
            {"type": "arm_add", "target": "x", "proposed": 0.2}
        )
        assert d.should_auto_accept is False
        assert d.reason == "skip:not_reward_weight"

    def test_valid_retune_accepts(self):
        """gaming/youtube/views base 0.3 -> proposed 0.4 is a 1.33x
        retune within [0.5x, 2.0x] AND within ±0.1 delta. Should
        auto-accept when risk=low."""
        from genlab_core.scheduling.proposal_auto_accept import (
            classify_reward_weight,
        )

        d = classify_reward_weight(
            self._high_conf(
                target="gaming.reward_weight.youtube.views",
                proposed=0.4,
            ),
            niche_id="gaming",
        )
        assert d.should_auto_accept is True
        assert d.reason.startswith("auto_accept:reward_weight_retune")

    def test_missing_target_skips(self):
        from genlab_core.scheduling.proposal_auto_accept import (
            classify_reward_weight,
        )

        d = classify_reward_weight(self._high_conf(proposed=0.3))
        assert d.should_auto_accept is False
        assert d.reason == "skip:missing_target"

    def test_malformed_target_skips(self):
        from genlab_core.scheduling.proposal_auto_accept import (
            classify_reward_weight,
        )

        d = classify_reward_weight(
            self._high_conf(target="not_a_valid_target", proposed=0.3)
        )
        assert d.should_auto_accept is False
        assert d.reason.startswith("skip:malformed_target")

    def test_target_niche_mismatch_skips(self):
        """Belt-and-suspenders — if a proposal ends up in the wrong
        niche's report, the target's niche prefix should force a
        skip rather than mutate the wrong niche's weights."""
        from genlab_core.scheduling.proposal_auto_accept import (
            classify_reward_weight,
        )

        d = classify_reward_weight(
            self._high_conf(
                target="gaming.reward_weight.youtube.views",
                proposed=0.4,
            ),
            niche_id="sports",  # runner's niche differs from target's
        )
        assert d.should_auto_accept is False
        assert "target_niche_mismatch" in d.reason

    def test_unknown_platform_gates_to_operator(self):
        """Better to operator-gate than to accept an override the
        consumer will silently drop (unknown platform → no BASE_WEIGHTS
        entry → the metric key check at reward_shaper.py:293 fails)."""
        from genlab_core.scheduling.proposal_auto_accept import (
            classify_reward_weight,
        )

        d = classify_reward_weight(
            self._high_conf(
                target="gaming.reward_weight.myspace.views",
                proposed=0.4,
            ),
            niche_id="gaming",
        )
        assert d.should_auto_accept is False
        assert "unknown_platform" in d.reason

    def test_unknown_metric_gates_to_operator(self):
        from genlab_core.scheduling.proposal_auto_accept import (
            classify_reward_weight,
        )

        d = classify_reward_weight(
            self._high_conf(
                target="gaming.reward_weight.youtube.does_not_exist",
                proposed=0.4,
            ),
            niche_id="gaming",
        )
        assert d.should_auto_accept is False
        assert "unknown_metric" in d.reason
        assert "silently no-op" in d.reason  # actionable guidance

    def test_out_of_range_proposed_gates(self):
        """Consumer clamps to [0.0, 5.0]. Proposal at 6.0 would
        silently no-op; better to operator-gate."""
        from genlab_core.scheduling.proposal_auto_accept import (
            classify_reward_weight,
        )

        d = classify_reward_weight(
            self._high_conf(
                target="gaming.reward_weight.youtube.views",
                proposed=6.0,
            ),
            niche_id="gaming",
        )
        assert d.should_auto_accept is False
        assert "proposed_out_of_range" in d.reason

    def test_wild_swing_gates(self):
        """views base 0.3 -> proposed 4.9 is within [0.0, 5.0] but a
        16x jump. Must operator-gate."""
        from genlab_core.scheduling.proposal_auto_accept import (
            classify_reward_weight,
        )

        d = classify_reward_weight(
            self._high_conf(
                target="gaming.reward_weight.youtube.views",
                proposed=4.9,
            ),
            niche_id="gaming",
        )
        assert d.should_auto_accept is False
        assert "wild_swing" in d.reason

    def test_negative_base_uses_abs_delta_floor(self):
        """instagram.skip_rate has base=-0.05. Ratios flip sign, so
        relative-change gate can't apply. Proposal at 0.05 is a
        +0.10 abs delta — should accept via the abs floor."""
        from genlab_core.scheduling.proposal_auto_accept import (
            classify_reward_weight,
        )

        d = classify_reward_weight(
            self._high_conf(
                target="ai_creators.reward_weight.instagram.skip_rate",
                proposed=0.05,
            ),
            niche_id="ai_creators",
        )
        # ±0.10 abs delta unlocks — base -0.05 to 0.05 is 0.10 delta
        assert d.should_auto_accept is True

    def test_non_numeric_proposed_skips(self):
        from genlab_core.scheduling.proposal_auto_accept import (
            classify_reward_weight,
        )

        d = classify_reward_weight(
            self._high_conf(
                target="gaming.reward_weight.youtube.views",
                proposed="not_a_number",
            ),
            niche_id="gaming",
        )
        assert d.should_auto_accept is False
        assert "non_numeric_proposed" in d.reason

    def test_low_confidence_gates_even_when_safe(self):
        """Same rule as arm_add: without high confidence OR risk=low,
        even a well-formed proposal is operator-gated."""
        from genlab_core.scheduling.proposal_auto_accept import (
            classify_reward_weight,
        )

        d = classify_reward_weight(
            {
                "type": "reward_weight",
                "target": "gaming.reward_weight.youtube.views",
                "proposed": 0.4,
                # no risk=low, no confidence=high
            },
            niche_id="gaming",
        )
        assert d.should_auto_accept is False
        assert "operator_gate:effective_confidence" in d.reason

    def test_explicit_high_confidence_accepts(self):
        """proposal_confidence kwarg from causal_hypotheses lookup
        also unlocks even without risk=low on the proposal itself."""
        from genlab_core.scheduling.proposal_auto_accept import (
            classify_reward_weight,
        )

        d = classify_reward_weight(
            {
                "type": "reward_weight",
                "target": "gaming.reward_weight.youtube.views",
                "proposed": 0.4,
            },
            niche_id="gaming",
            proposal_confidence="high",
        )
        assert d.should_auto_accept is True


class TestClassifyGateThreshold:
    """2026-08-11 Session 3: gate_threshold classifier pins.

    Consumer: auto_approval_gate.py:167 uses the override in place of
    `composite_score >= 0.3`. Clamps to [0.05, 0.85] at
    strategy_phase.py:225. Baseline = 0.3.

    Auto-accept range: baseline ±0.15 -> [0.15, 0.45]. Wider swings
    operator-gate.
    """

    def test_wrong_type_skips(self):
        from genlab_core.scheduling.proposal_auto_accept import (
            classify_gate_threshold,
        )

        d = classify_gate_threshold({"type": "arm_add", "proposed": 0.4})
        assert d.should_auto_accept is False
        assert d.reason == "skip:not_gate_threshold"

    def test_within_baseline_delta_accepts(self):
        """0.30 -> 0.40 is +0.10 delta, within ±0.15 bound. Accept."""
        from genlab_core.scheduling.proposal_auto_accept import (
            classify_gate_threshold,
        )

        d = classify_gate_threshold(
            {"type": "gate_threshold", "proposed": 0.4, "risk": "low"}
        )
        assert d.should_auto_accept is True
        assert d.reason.startswith("auto_accept:gate_threshold")

    def test_at_boundary_of_baseline_delta_accepts(self):
        """0.30 -> 0.45 is +0.15 delta (exactly the max). Accept."""
        from genlab_core.scheduling.proposal_auto_accept import (
            classify_gate_threshold,
        )

        d = classify_gate_threshold(
            {"type": "gate_threshold", "proposed": 0.45, "risk": "low"}
        )
        assert d.should_auto_accept is True

    def test_beyond_baseline_delta_gates(self):
        """0.30 -> 0.60 is +0.30 delta, beyond bound. Operator-gate.

        Note: this is well within the consumer's absolute clamp
        [0.05, 0.85] — the classifier's tighter bound is deliberate
        blast-radius sizing (rule #22 sibling: one accept shouldn't
        move the gate 2x from baseline)."""
        from genlab_core.scheduling.proposal_auto_accept import (
            classify_gate_threshold,
        )

        d = classify_gate_threshold(
            {"type": "gate_threshold", "proposed": 0.60, "risk": "low"}
        )
        assert d.should_auto_accept is False
        assert "delta_exceeds_baseline_bound" in d.reason

    def test_out_of_consumer_range_gates(self):
        """0.99 would silently no-op at consumer (clamped out). Better
        to operator-gate than to accept a dead proposal."""
        from genlab_core.scheduling.proposal_auto_accept import (
            classify_gate_threshold,
        )

        d = classify_gate_threshold(
            {"type": "gate_threshold", "proposed": 0.99, "risk": "low"}
        )
        assert d.should_auto_accept is False
        assert "proposed_out_of_range" in d.reason

    def test_non_numeric_proposed_skips(self):
        from genlab_core.scheduling.proposal_auto_accept import (
            classify_gate_threshold,
        )

        d = classify_gate_threshold(
            {"type": "gate_threshold", "proposed": "high", "risk": "low"}
        )
        assert d.should_auto_accept is False
        assert "non_numeric_proposed" in d.reason

    def test_low_confidence_gates(self):
        from genlab_core.scheduling.proposal_auto_accept import (
            classify_gate_threshold,
        )

        d = classify_gate_threshold(
            {"type": "gate_threshold", "proposed": 0.4}  # no risk / conf
        )
        assert d.should_auto_accept is False
        assert "operator_gate:effective_confidence" in d.reason


class TestClassifyNoveltyRate:
    """2026-08-11 Session 3: novelty_rate classifier pins.

    Consumer: push_to_backlog force-explore rate. Clamps to
    [0.0, 0.50] at strategy_phase.py:233. Baseline = 0.25.

    Auto-accept range: baseline ±0.15 -> [0.10, 0.40].
    """

    def test_wrong_type_skips(self):
        from genlab_core.scheduling.proposal_auto_accept import (
            classify_novelty_rate,
        )

        d = classify_novelty_rate({"type": "arm_add", "proposed": 0.3})
        assert d.should_auto_accept is False
        assert d.reason == "skip:not_novelty_rate"

    def test_within_baseline_delta_accepts(self):
        """0.25 -> 0.35 is +0.10 delta. Accept."""
        from genlab_core.scheduling.proposal_auto_accept import (
            classify_novelty_rate,
        )

        d = classify_novelty_rate(
            {"type": "novelty_rate", "proposed": 0.35, "risk": "low"}
        )
        assert d.should_auto_accept is True
        assert d.reason.startswith("auto_accept:novelty_rate")

    def test_beyond_baseline_delta_gates(self):
        """0.25 -> 0.50 is +0.25 delta. Operator-gate even though
        it's exactly at the consumer's absolute max."""
        from genlab_core.scheduling.proposal_auto_accept import (
            classify_novelty_rate,
        )

        d = classify_novelty_rate(
            {"type": "novelty_rate", "proposed": 0.50, "risk": "low"}
        )
        assert d.should_auto_accept is False
        assert "delta_exceeds_baseline_bound" in d.reason

    def test_out_of_consumer_range_gates(self):
        """0.75 would silently no-op at consumer. Operator-gate."""
        from genlab_core.scheduling.proposal_auto_accept import (
            classify_novelty_rate,
        )

        d = classify_novelty_rate(
            {"type": "novelty_rate", "proposed": 0.75, "risk": "low"}
        )
        assert d.should_auto_accept is False
        assert "proposed_out_of_range" in d.reason

    def test_zero_is_valid_range_but_beyond_delta_gates(self):
        """0.25 -> 0.0 is -0.25 delta. Within consumer range [0.0, 0.5]
        but beyond ±0.15 bound. Operator-gate."""
        from genlab_core.scheduling.proposal_auto_accept import (
            classify_novelty_rate,
        )

        d = classify_novelty_rate(
            {"type": "novelty_rate", "proposed": 0.0, "risk": "low"}
        )
        assert d.should_auto_accept is False
        assert "delta_exceeds_baseline_bound" in d.reason

    def test_baseline_delta_downward_accepts(self):
        """0.25 -> 0.10 is -0.15 delta (exactly the max). Accept."""
        from genlab_core.scheduling.proposal_auto_accept import (
            classify_novelty_rate,
        )

        d = classify_novelty_rate(
            {"type": "novelty_rate", "proposed": 0.10, "risk": "low"}
        )
        assert d.should_auto_accept is True

    def test_low_confidence_gates(self):
        from genlab_core.scheduling.proposal_auto_accept import (
            classify_novelty_rate,
        )

        d = classify_novelty_rate(
            {"type": "novelty_rate", "proposed": 0.3}
        )
        assert d.should_auto_accept is False


class TestScalarOverrideSharedContract:
    """The two scalar classifiers share `_classify_scalar_override` —
    this class pins the shared contract in one place so a refactor
    of the shared impl catches regressions across both types."""

    def test_both_reject_wrong_type(self):
        from genlab_core.scheduling.proposal_auto_accept import (
            classify_gate_threshold,
            classify_novelty_rate,
        )

        payload = {"type": "reward_weight", "proposed": 0.3, "risk": "low"}
        assert classify_gate_threshold(payload).should_auto_accept is False
        assert classify_novelty_rate(payload).should_auto_accept is False

    def test_both_share_the_confidence_gate(self):
        """proposal_confidence='high' kwarg unlocks both when
        risk/confidence fields are absent on the proposal itself."""
        from genlab_core.scheduling.proposal_auto_accept import (
            classify_gate_threshold,
            classify_novelty_rate,
        )

        gate_d = classify_gate_threshold(
            {"type": "gate_threshold", "proposed": 0.4},
            proposal_confidence="high",
        )
        novelty_d = classify_novelty_rate(
            {"type": "novelty_rate", "proposed": 0.3},
            proposal_confidence="high",
        )
        assert gate_d.should_auto_accept is True
        assert novelty_d.should_auto_accept is True
