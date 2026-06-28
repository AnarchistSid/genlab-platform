"""Tests for LinUCB IPS propensity logging (Move #8, PR T).

Pins the contract that every LinUCB selection emits a known propensity
p(arm | context) so future Inverse Propensity Scoring offline-policy-
evaluation scripts can reconstruct what would have happened under any
alternative policy. The doc explicitly flags propensity as painful to
add retroactively — these tests pin the forward contract.

Test design notes
-----------------
- Tests never read env directly; instead they pass ``stochastic`` and
  ``temperature`` as kwargs to ``select_with_propensity``. Env coverage
  is in a dedicated TestEnvFlag class via monkeypatch.
- Numpy randomness is seeded per-test in the stochastic class so the
  "chosen arm matches sampled distribution" assertion is reproducible.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from genlab_core.learning.linucb import (
    DEFAULT_TEMPERATURE,
    LinUCBBandit,
    compute_softmax_probabilities,
)
from genlab_core.learning.pending_feedback_store import PendingFeedbackStore
from genlab_core.learning.pending_feedback_task import PendingFeedbackTask

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _trained_bandit(seed: int = 42) -> LinUCBBandit:
    """Three-arm bandit trained with deterministic-but-different per-arm
    rewards so UCB scores diverge. Returns a bandit where arm 'a' is the
    expected argmax under the test context."""
    rng = np.random.default_rng(seed)
    bandit = LinUCBBandit(arm_ids=["a", "b", "c"], d=4, alpha=0.5)
    context = np.array([1.0, 0.5, 0.25, 0.0])
    # Arm 'a' gets high rewards
    for _ in range(20):
        bandit.update("a", context, reward=1.0)
    # Arm 'b' gets medium rewards
    for _ in range(20):
        bandit.update("b", context, reward=0.5)
    # Arm 'c' gets low rewards
    for _ in range(20):
        bandit.update("c", context, reward=0.1)
    # Unused but documents that the seed exists for future randomized
    # variants of this helper.
    _ = rng
    return bandit


# ---------------------------------------------------------------------------
# 1. select returns (arm, propensity) via the new API
# ---------------------------------------------------------------------------


class TestSelectWithPropensitySignature:
    def test_returns_tuple_of_arm_and_propensity(self):
        bandit = _trained_bandit()
        context = np.array([1.0, 0.5, 0.25, 0.0])
        result = bandit.select_with_propensity(context)
        assert isinstance(result, tuple)
        assert len(result) == 2
        arm_id, propensity = result
        assert isinstance(arm_id, str)
        assert isinstance(propensity, float)

    def test_propensity_is_in_zero_one_open_interval(self):
        """Propensity must be in (0, 1] — never exactly 0 (IPS weight
        explodes) and never above 1 (probability)."""
        bandit = _trained_bandit()
        context = np.array([1.0, 0.5, 0.25, 0.0])
        _arm, propensity = bandit.select_with_propensity(context, stochastic=True)
        assert 0.0 < propensity <= 1.0

    def test_arm_is_one_of_configured(self):
        bandit = _trained_bandit()
        context = np.array([1.0, 0.5, 0.25, 0.0])
        arm, _propensity = bandit.select_with_propensity(context)
        assert arm in {"a", "b", "c"}


# ---------------------------------------------------------------------------
# 2. Deterministic mode (default): propensity == 1.0 for argmax
# ---------------------------------------------------------------------------


class TestDeterministicMode:
    def test_argmax_arm_has_propensity_1(self):
        """Default behavior: pure argmax, propensity is the limit case."""
        bandit = _trained_bandit()
        context = np.array([1.0, 0.5, 0.25, 0.0])
        arm, propensity = bandit.select_with_propensity(context, stochastic=False)
        # 'a' was trained with reward=1.0 — should be argmax
        assert arm == "a"
        assert propensity == 1.0

    def test_default_mode_is_deterministic(self, monkeypatch):
        """Env unset → deterministic mode → propensity = 1.0."""
        monkeypatch.delenv("GENLAB_LINUCB_STOCHASTIC_ENABLED", raising=False)
        bandit = _trained_bandit()
        context = np.array([1.0, 0.5, 0.25, 0.0])
        _arm, propensity = bandit.select_with_propensity(context)
        assert propensity == 1.0

    def test_deterministic_select_matches_legacy_select(self):
        """The backward-compat ``select(context)`` must return the same
        arm as ``select_with_propensity`` in deterministic mode."""
        bandit = _trained_bandit()
        context = np.array([1.0, 0.5, 0.25, 0.0])
        legacy = bandit.select(context)
        new, _propensity = bandit.select_with_propensity(context, stochastic=False)
        assert legacy == new


# ---------------------------------------------------------------------------
# 3. Stochastic mode: propensity matches softmax weight
# ---------------------------------------------------------------------------


class TestStochasticMode:
    def setup_method(self):
        # Seed numpy so np.random.choice is reproducible across runs.
        np.random.seed(0)

    def test_softmax_probabilities_sum_to_one(self):
        scores = {"a": 1.2, "b": 0.4, "c": -0.3}
        probs = compute_softmax_probabilities(scores, temperature=0.5)
        assert sum(probs.values()) == pytest.approx(1.0, abs=1e-6)

    def test_chosen_arm_propensity_matches_softmax_weight(self):
        """The returned propensity must equal the chosen arm's softmax
        weight under the active temperature — the foundation of IPS."""
        bandit = _trained_bandit()
        context = np.array([1.0, 0.5, 0.25, 0.0])
        # Re-derive the scores the bandit will see, so we can predict
        # the exact softmax weight independent of the sampled outcome.
        scores = {aid: arm.predict(context) for aid, arm in bandit.arms.items()}
        expected_probs = compute_softmax_probabilities(scores, temperature=0.5)

        arm, propensity = bandit.select_with_propensity(context, stochastic=True, temperature=0.5)
        assert propensity == pytest.approx(expected_probs[arm], abs=1e-9)

    def test_high_score_arm_picked_more_often(self):
        """Sanity check: under moderate temperature the high-UCB arm
        is sampled most often. Pins the principle that stochasticity
        doesn't destroy the bandit's preference, only relaxes it."""
        bandit = _trained_bandit()
        context = np.array([1.0, 0.5, 0.25, 0.0])
        counts: dict[str, int] = {"a": 0, "b": 0, "c": 0}
        for _ in range(200):
            arm, _ = bandit.select_with_propensity(context, stochastic=True, temperature=0.5)
            counts[arm] += 1
        # 'a' was the argmax — should win the plurality of samples
        assert counts["a"] > counts["b"]
        assert counts["a"] > counts["c"]


# ---------------------------------------------------------------------------
# 4. Temperature parameter changes distribution shape
# ---------------------------------------------------------------------------


class TestTemperatureControl:
    """Confirms the temperature kwarg is wired to the softmax formula."""

    def test_low_temperature_is_near_deterministic(self):
        """T → 0 should make argmax's propensity dominate (> 0.9)."""
        # Use very spread scores so the softmax has room to concentrate
        scores = {"a": 5.0, "b": 1.0, "c": 0.0}
        probs = compute_softmax_probabilities(scores, temperature=0.1)
        argmax_arm = max(probs, key=probs.get)
        assert argmax_arm == "a"
        assert probs["a"] > 0.9

    def test_high_temperature_approaches_uniform(self):
        """T → ∞ should flatten propensities towards 1/K."""
        scores = {"a": 5.0, "b": 1.0, "c": 0.0}
        probs = compute_softmax_probabilities(scores, temperature=100.0)
        # Each arm should be near 1/3
        for arm in ("a", "b", "c"):
            assert probs[arm] == pytest.approx(1.0 / 3.0, abs=0.05)
        # No arm hits the > 0.5 threshold a low-T distribution would have
        assert max(probs.values()) < 0.4

    def test_default_temperature_constant_is_positive(self):
        assert DEFAULT_TEMPERATURE > 0.0


# ---------------------------------------------------------------------------
# 5. Backward compat: select() still returns a plain str
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    def test_select_returns_string_not_tuple(self):
        """Existing callers unpacking ``arm_id = bandit.select(ctx)``
        must keep working — return type stays a bare string."""
        bandit = LinUCBBandit(arm_ids=["a", "b"], d=3, alpha=1.0)
        result = bandit.select(np.array([0.5, 0.5, 0.5]))
        assert isinstance(result, str)
        assert result in {"a", "b"}

    def test_select_calls_select_with_propensity_under_hood(self):
        """``select`` is now a thin alias that drops the propensity.
        Verifies the two methods stay in sync — if a regression makes
        them diverge, this test fires."""
        bandit = _trained_bandit()
        context = np.array([1.0, 0.5, 0.25, 0.0])
        legacy = bandit.select(context)
        new, _ = bandit.select_with_propensity(context, stochastic=False)
        assert legacy == new


# ---------------------------------------------------------------------------
# 6. Persistence: PendingFeedbackTask round-trips propensity + temperature
# ---------------------------------------------------------------------------


class TestPropensityPersistence:
    def test_task_carries_propensity_field(self):
        from datetime import UTC, datetime

        task = PendingFeedbackTask(
            content_id="cid-1",
            platform="instagram",
            niche_id="gaming",
            published_at=datetime.now(UTC),
            platform_post_id="ig:abc",
            bandit_arm="hook:question",
            propensity=0.42,
            temperature=0.5,
        )
        assert task.propensity == 0.42
        assert task.temperature == 0.5

    def test_task_defaults_propensity_to_none(self):
        """Backward compat: callers that don't pass propensity get
        None — the IPS filter ignores those rows, preserving every
        existing call site."""
        from datetime import UTC, datetime

        task = PendingFeedbackTask(
            content_id="cid-1",
            platform="instagram",
            niche_id="gaming",
            published_at=datetime.now(UTC),
            platform_post_id="ig:abc",
        )
        assert task.propensity is None
        assert task.temperature is None

    def test_to_sharepoint_fields_writes_propensity(self):
        """Producer-side write contract: when propensity is set, the
        serialised payload includes the lowercase ``propensity`` +
        ``temperature`` keys so they land in Postgres promoted columns."""
        from datetime import UTC, datetime

        task = PendingFeedbackTask(
            content_id="cid-1",
            platform="instagram",
            niche_id="gaming",
            published_at=datetime.now(UTC),
            platform_post_id="ig:abc",
            bandit_arm="hook:question",
            propensity=0.42,
            temperature=0.5,
        )
        fields = task.to_sharepoint_fields()
        assert fields["propensity"] == 0.42
        assert fields["temperature"] == 0.5

    def test_to_sharepoint_fields_omits_propensity_when_none(self):
        """When propensity is None we MUST NOT write the key — the
        downstream PostgresBackend's None-coercion drops it cleanly,
        but writing the key explicitly with value None would mark the
        column as 'intentionally null' versus 'never set'."""
        from datetime import UTC, datetime

        task = PendingFeedbackTask(
            content_id="cid-1",
            platform="instagram",
            niche_id="gaming",
            published_at=datetime.now(UTC),
            platform_post_id="ig:abc",
        )
        fields = task.to_sharepoint_fields()
        assert "propensity" not in fields
        assert "temperature" not in fields

    def test_proxy_insert_payload_includes_propensity(self):
        """End-to-end: PendingFeedbackStore.create passes the propensity
        + temperature through to the proxy's create() call. This is the
        wire test that proves the column hits storage."""
        from datetime import UTC, datetime

        captured: dict = {}

        class FakeProxy:
            def create(self, fields):
                captured.update(fields)

        class FakeClient:
            pending_feedback = FakeProxy()

        store = PendingFeedbackStore(FakeClient())
        task = PendingFeedbackTask(
            content_id="cid-1",
            platform="instagram",
            niche_id="gaming",
            published_at=datetime.now(UTC),
            platform_post_id="ig:abc",
            bandit_arm="hook:question",
            propensity=0.42,
            temperature=0.5,
        )
        store.create(task)
        assert captured.get("propensity") == 0.42
        assert captured.get("temperature") == 0.5

    def test_from_sharepoint_item_round_trips_propensity(self):
        """Reader side: when Postgres returns rows with propensity +
        temperature populated, the parsed task carries the values."""
        item = {
            "id": "row-1",
            "fields": {
                "post_id": "instagram:abc",
                "platform": "instagram",
                "niche_id": "gaming",
                "publish_time": "2026-06-28T10:00:00+00:00",
                "arm_id": "hook:question",
                "collection_status": "awaiting_6h",
                "propensity": 0.42,
                "temperature": 0.5,
                "bandit_context": {"hour": 14},
            },
        }
        task = PendingFeedbackStore._from_sharepoint_item(item)
        assert task.propensity == pytest.approx(0.42)
        assert task.temperature == pytest.approx(0.5)

    def test_from_sharepoint_item_handles_legacy_row_with_no_propensity(self):
        """Legacy rows predating the migration have NULL propensity —
        the parser MUST tolerate it (returns None, IPS filters out)."""
        item = {
            "id": "row-1",
            "fields": {
                "post_id": "instagram:abc",
                "platform": "instagram",
                "niche_id": "gaming",
                "publish_time": "2026-06-28T10:00:00+00:00",
                "arm_id": "hook:question",
                "collection_status": "awaiting_6h",
                "bandit_context": json.dumps({"hour": 14}),
            },
        }
        task = PendingFeedbackStore._from_sharepoint_item(item)
        assert task.propensity is None
        assert task.temperature is None


# ---------------------------------------------------------------------------
# 7. Env-driven opt-in (stochastic mode flag)
# ---------------------------------------------------------------------------


class TestEnvFlag:
    def test_env_flag_off_means_deterministic(self, monkeypatch):
        monkeypatch.setenv("GENLAB_LINUCB_STOCHASTIC_ENABLED", "0")
        bandit = _trained_bandit()
        ctx = np.array([1.0, 0.5, 0.25, 0.0])
        _arm, propensity = bandit.select_with_propensity(ctx)
        assert propensity == 1.0

    def test_env_flag_on_means_stochastic(self, monkeypatch):
        monkeypatch.setenv("GENLAB_LINUCB_STOCHASTIC_ENABLED", "1")
        bandit = _trained_bandit()
        ctx = np.array([1.0, 0.5, 0.25, 0.0])
        np.random.seed(0)
        _arm, propensity = bandit.select_with_propensity(ctx)
        # Stochastic mode → propensity should be strictly < 1.0
        # (any non-degenerate softmax with multiple arms returns < 1).
        assert 0.0 < propensity < 1.0

    def test_only_literal_1_enables_stochastic(self, monkeypatch):
        """Strictness on the env value matches the memory note: only
        ``"1"`` enables; ``"true"`` / ``"yes"`` are deliberately disabled
        to avoid the AUTO #2 env-string ambiguity bug class."""
        for value in ("true", "yes", "True", "TRUE", " "):
            monkeypatch.setenv("GENLAB_LINUCB_STOCHASTIC_ENABLED", value)
            bandit = _trained_bandit()
            ctx = np.array([1.0, 0.5, 0.25, 0.0])
            _arm, propensity = bandit.select_with_propensity(ctx)
            assert propensity == 1.0, f"value {value!r} should NOT enable stochastic"

    def test_temperature_env_override(self, monkeypatch):
        """GENLAB_LINUCB_TEMPERATURE is read when temperature kwarg is
        None. Verify by reading the env path then comparing to the
        explicit-kwarg path."""
        monkeypatch.setenv("GENLAB_LINUCB_STOCHASTIC_ENABLED", "1")
        monkeypatch.setenv("GENLAB_LINUCB_TEMPERATURE", "0.1")
        bandit = _trained_bandit()
        ctx = np.array([1.0, 0.5, 0.25, 0.0])
        # Replicate the expected softmax under T=0.1
        scores = {aid: arm.predict(ctx) for aid, arm in bandit.arms.items()}
        expected = compute_softmax_probabilities(scores, temperature=0.1)
        np.random.seed(0)
        arm, propensity = bandit.select_with_propensity(ctx)
        assert propensity == pytest.approx(expected[arm], abs=1e-9)

    def test_invalid_temperature_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("GENLAB_LINUCB_STOCHASTIC_ENABLED", "1")
        monkeypatch.setenv("GENLAB_LINUCB_TEMPERATURE", "not-a-float")
        bandit = _trained_bandit()
        ctx = np.array([1.0, 0.5, 0.25, 0.0])
        scores = {aid: arm.predict(ctx) for aid, arm in bandit.arms.items()}
        expected = compute_softmax_probabilities(scores, temperature=DEFAULT_TEMPERATURE)
        np.random.seed(0)
        arm, propensity = bandit.select_with_propensity(ctx)
        assert propensity == pytest.approx(expected[arm], abs=1e-9)

    def test_zero_temperature_falls_back_to_default(self, monkeypatch):
        """T=0 would divide-by-zero in softmax — must be rejected."""
        monkeypatch.setenv("GENLAB_LINUCB_STOCHASTIC_ENABLED", "1")
        monkeypatch.setenv("GENLAB_LINUCB_TEMPERATURE", "0")
        bandit = _trained_bandit()
        ctx = np.array([1.0, 0.5, 0.25, 0.0])
        scores = {aid: arm.predict(ctx) for aid, arm in bandit.arms.items()}
        expected = compute_softmax_probabilities(scores, temperature=DEFAULT_TEMPERATURE)
        np.random.seed(0)
        arm, propensity = bandit.select_with_propensity(ctx)
        assert propensity == pytest.approx(expected[arm], abs=1e-9)


# ---------------------------------------------------------------------------
# 8. compute_softmax_probabilities: standalone helper contract
# ---------------------------------------------------------------------------


class TestSoftmaxHelper:
    def test_empty_input_returns_empty(self):
        assert compute_softmax_probabilities({}) == {}

    def test_single_arm_gets_probability_one(self):
        probs = compute_softmax_probabilities({"only": 3.7})
        assert probs == {"only": pytest.approx(1.0)}

    def test_equal_scores_give_uniform_distribution(self):
        probs = compute_softmax_probabilities({"a": 0.5, "b": 0.5, "c": 0.5})
        for arm in ("a", "b", "c"):
            assert probs[arm] == pytest.approx(1.0 / 3.0)

    def test_no_propensity_below_floor(self):
        """Even with extremely spread scores at low T, no arm has
        propensity < the numerical floor — bounds IPS weights."""
        scores = {"a": 100.0, "b": 0.0, "c": -100.0}
        probs = compute_softmax_probabilities(scores, temperature=0.01)
        # The 'c' arm would normally be at exp(-20000) ≈ 0; floor catches it.
        assert all(p > 0.0 for p in probs.values())
        assert min(probs.values()) >= 1e-6

    def test_numerical_stability_with_large_scores(self):
        """Without the subtract-max trick, exp(1000) overflows. Verify
        the helper produces finite, summing-to-1 probabilities."""
        scores = {"a": 1000.0, "b": 1001.0, "c": 999.0}
        probs = compute_softmax_probabilities(scores, temperature=1.0)
        for p in probs.values():
            assert np.isfinite(p)
        assert sum(probs.values()) == pytest.approx(1.0, abs=1e-6)
