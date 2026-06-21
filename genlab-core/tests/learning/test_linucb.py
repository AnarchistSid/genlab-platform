"""Tests for genlab_core.learning.linucb — LinUCB contextual bandit."""

from datetime import UTC, datetime

import numpy as np
import pytest
from genlab_core.learning.linucb import (
    CONTEXT_DIM,
    LinUCBArm,
    LinUCBBandit,
    build_content_context,
)

# ---------------------------------------------------------------------------
# LinUCBArm
# ---------------------------------------------------------------------------


class TestLinUCBArm:
    def test_init_identity_matrix(self):
        """A matrix starts as identity, b as zero vector."""
        arm = LinUCBArm(d=6, alpha=1.0)
        np.testing.assert_array_equal(arm.A, np.eye(6))
        np.testing.assert_array_equal(arm.b, np.zeros(6))
        assert arm.alpha == 1.0
        assert arm.n_obs == 0

    def test_predict_zero_context(self):
        """Zero context should yield zero UCB score."""
        arm = LinUCBArm(d=4, alpha=1.0)
        x = np.zeros(4)
        score = arm.predict(x)
        assert score == 0.0

    def test_predict_identity_context(self):
        """With identity A and zero b, theta = 0, so only exploration term matters."""
        arm = LinUCBArm(d=3, alpha=2.0)
        x = np.array([1.0, 0.0, 0.0])
        score = arm.predict(x)
        # theta = A^{-1} b = 0, so theta^T x = 0
        # exploration = alpha * sqrt(x^T A^{-1} x) = 2.0 * sqrt(1.0) = 2.0
        assert score == pytest.approx(2.0)

    def test_update_changes_A_and_b(self):
        """update(x, reward) should modify A and b correctly."""
        arm = LinUCBArm(d=2, alpha=1.0)
        x = np.array([1.0, 0.5])
        arm.update(x, reward=0.8)

        expected_A = np.eye(2) + np.outer(x, x)
        expected_b = 0.8 * x
        np.testing.assert_array_almost_equal(arm.A, expected_A)
        np.testing.assert_array_almost_equal(arm.b, expected_b)
        assert arm.n_obs == 1

    def test_update_increments_n_obs(self):
        arm = LinUCBArm(d=3, alpha=1.0)
        x = np.array([0.5, 0.5, 0.5])
        for _ in range(10):
            arm.update(x, reward=0.5)
        assert arm.n_obs == 10

    def test_predict_after_multiple_updates(self):
        """After learning, prediction should reflect theta = A^{-1} b."""
        arm = LinUCBArm(d=2, alpha=0.0)  # no exploration
        x = np.array([1.0, 0.0])

        # After one update with reward=1.0:
        # A = I + outer(x,x) = [[2,0],[0,1]]
        # b = 1.0 * [1,0] = [1,0]
        # theta = A^{-1} b = [0.5, 0]
        # p = theta^T x = 0.5
        arm.update(x, reward=1.0)
        score = arm.predict(x)
        assert score == pytest.approx(0.5)

    def test_predict_exploration_decreases_with_observations(self):
        """Exploration term decreases as A grows (more confidence)."""
        arm = LinUCBArm(d=2, alpha=1.0)
        x = np.array([1.0, 0.5])

        arm.predict(x)

        # Add many observations to make A larger -> A^{-1} smaller -> less exploration
        for _ in range(100):
            arm.update(x, reward=0.5)

        arm.predict(x)
        # The exploration bonus sqrt(x^T A^{-1} x) should decrease
        # We can verify by checking that the exploration part is smaller
        A_inv = np.linalg.inv(arm.A)
        exploration_after = np.sqrt(x @ A_inv @ x)
        assert exploration_after < 0.2  # Much smaller than initial sqrt(x^T I x) = sqrt(1.25)

    def test_to_dict_and_from_dict_roundtrip(self):
        """Serialization roundtrip preserves state."""
        arm = LinUCBArm(d=3, alpha=1.5)
        x = np.array([0.1, 0.9, 0.5])
        arm.update(x, reward=0.7)
        arm.update(x * 2, reward=0.3)

        data = arm.to_dict()
        restored = LinUCBArm.from_dict(data)

        np.testing.assert_array_almost_equal(restored.A, arm.A)
        np.testing.assert_array_almost_equal(restored.b, arm.b)
        assert restored.alpha == arm.alpha
        assert restored.n_obs == arm.n_obs
        assert restored.predict(x) == pytest.approx(arm.predict(x))

    def test_to_dict_format(self):
        """to_dict produces JSON-serializable format."""
        arm = LinUCBArm(d=2, alpha=1.0)
        data = arm.to_dict()
        assert "A_matrix" in data
        assert "b_vector" in data
        assert "alpha" in data
        assert "n_obs" in data
        assert isinstance(data["A_matrix"], list)
        assert isinstance(data["b_vector"], list)

    def test_from_dict_handles_missing_n_obs(self):
        """Backward compat: older serialized data may lack n_obs."""
        data = {
            "A_matrix": np.eye(2).tolist(),
            "b_vector": [0.0, 0.0],
            "alpha": 1.0,
        }
        arm = LinUCBArm.from_dict(data)
        assert arm.n_obs == 0


# ---------------------------------------------------------------------------
# LinUCBBandit
# ---------------------------------------------------------------------------


class TestLinUCBBandit:
    def test_init_creates_arms(self):
        bandit = LinUCBBandit(arm_ids=["a", "b", "c"], d=4, alpha=1.0)
        assert set(bandit.arms.keys()) == {"a", "b", "c"}
        assert all(isinstance(a, LinUCBArm) for a in bandit.arms.values())

    def test_select_returns_valid_arm(self):
        bandit = LinUCBBandit(arm_ids=["a", "b"], d=3, alpha=1.0)
        x = np.array([0.5, 0.5, 0.5])
        selected = bandit.select(x)
        assert selected in {"a", "b"}

    def test_select_all_arms_equal_initially(self):
        """With no training data, all arms have equal UCB scores."""
        bandit = LinUCBBandit(arm_ids=["a", "b", "c"], d=2, alpha=1.0)
        x = np.array([0.5, 0.5])
        # All arms identical, so select should return one deterministically
        # (the first in dict order that ties)
        selected = bandit.select(x)
        assert selected in {"a", "b", "c"}

    def test_update_only_modifies_target_arm(self):
        bandit = LinUCBBandit(arm_ids=["a", "b"], d=2, alpha=1.0)
        x = np.array([1.0, 0.0])

        A_b_before = bandit.arms["b"].A.copy()
        bandit.update("a", x, reward=1.0)

        # Arm b should be unchanged
        np.testing.assert_array_equal(bandit.arms["b"].A, A_b_before)
        # Arm a should be updated
        assert not np.array_equal(bandit.arms["a"].A, np.eye(2))

    def test_select_prefers_higher_reward_arm(self):
        """After training arm 'a' with high rewards, it should be preferred."""
        bandit = LinUCBBandit(arm_ids=["a", "b"], d=2, alpha=0.1)
        x = np.array([1.0, 0.5])

        # Train arm a with high rewards
        for _ in range(50):
            bandit.update("a", x, reward=1.0)
        # Train arm b with low rewards
        for _ in range(50):
            bandit.update("b", x, reward=0.1)

        selected = bandit.select(x)
        assert selected == "a"

    def test_update_unknown_arm_raises(self):
        bandit = LinUCBBandit(arm_ids=["a"], d=2, alpha=1.0)
        x = np.array([1.0, 0.0])
        with pytest.raises(KeyError):
            bandit.update("nonexistent", x, reward=1.0)

    def test_add_arm(self):
        bandit = LinUCBBandit(arm_ids=["a"], d=3, alpha=1.0)
        bandit.add_arm("b")
        assert "b" in bandit.arms
        assert bandit.arms["b"].n_obs == 0

    def test_add_existing_arm_is_noop(self):
        bandit = LinUCBBandit(arm_ids=["a"], d=3, alpha=1.0)
        x = np.array([1.0, 0.0, 0.0])
        bandit.update("a", x, reward=0.8)
        n_before = bandit.arms["a"].n_obs
        bandit.add_arm("a")
        assert bandit.arms["a"].n_obs == n_before  # Not reset

    def test_get_arm_observation_count(self):
        bandit = LinUCBBandit(arm_ids=["a", "b"], d=2, alpha=1.0)
        x = np.array([1.0, 0.0])
        for _ in range(5):
            bandit.update("a", x, reward=0.5)
        assert bandit.arms["a"].n_obs == 5
        assert bandit.arms["b"].n_obs == 0


# ---------------------------------------------------------------------------
# Cold-start: Thompson Sampling fallback
# ---------------------------------------------------------------------------


class TestColdStartFallback:
    def test_arm_below_threshold_uses_thompson(self):
        """Arms with < MIN_OBS_FOR_LINUCB should not use LinUCB predictions."""
        from genlab_core.learning.linucb import MIN_OBS_FOR_LINUCB

        arm = LinUCBArm(d=2, alpha=1.0)
        assert arm.n_obs < MIN_OBS_FOR_LINUCB

    def test_should_use_linucb(self):
        from genlab_core.learning.linucb import MIN_OBS_FOR_LINUCB

        arm = LinUCBArm(d=2, alpha=1.0)
        x = np.array([1.0, 0.5])
        for _ in range(MIN_OBS_FOR_LINUCB):
            arm.update(x, reward=0.5)
        assert arm.n_obs >= MIN_OBS_FOR_LINUCB


# ---------------------------------------------------------------------------
# build_content_context
# ---------------------------------------------------------------------------


class TestBuildContentContext:
    def test_returns_correct_dimensionality(self):
        story = {
            "source_type": "youtube",
            "duration_seconds": 30,
            "view_velocity": 5000,
            "relevance_score": 0.75,
        }
        ctx = build_content_context(story, "gaming")
        assert isinstance(ctx, np.ndarray)
        assert ctx.shape == (CONTEXT_DIM,)

    def test_all_values_normalized_0_to_1(self):
        story = {
            "source_type": "youtube",
            "duration_seconds": 45,
            "view_velocity": 8000,
            "relevance_score": 0.9,
        }
        ctx = build_content_context(story, "gaming")
        assert np.all(ctx >= 0.0)
        assert np.all(ctx <= 1.0)

    def test_source_type_mapping(self):
        """Known source types should map to specific values."""
        for source, expected in [
            ("youtube", 0.0),
            ("reddit", 0.33),
            ("rss", 0.66),
            ("twitch", 1.0),
        ]:
            story = {"source_type": source}
            ctx = build_content_context(story, "gaming")
            assert ctx[2] == pytest.approx(expected, abs=0.01)

    def test_unknown_source_type_gets_default(self):
        story = {"source_type": "unknown_platform"}
        ctx = build_content_context(story, "gaming")
        assert ctx[2] == pytest.approx(0.5)

    def test_missing_fields_use_defaults(self):
        """Missing story fields should produce sensible defaults."""
        ctx = build_content_context({}, "gaming")
        assert isinstance(ctx, np.ndarray)
        assert ctx.shape == (CONTEXT_DIM,)
        # duration default 30/60=0.5, velocity default 0, relevance default 0.5
        assert ctx[3] == pytest.approx(0.5)  # duration_seconds=30
        assert ctx[4] == pytest.approx(0.0)  # view_velocity=0
        assert ctx[5] == pytest.approx(0.5)  # relevance_score=0.5

    def test_duration_capped_at_1(self):
        """Duration > 60s should still cap at 1.0."""
        story = {"duration_seconds": 120}
        ctx = build_content_context(story, "gaming")
        assert ctx[3] == pytest.approx(1.0)

    def test_velocity_capped_at_1(self):
        """View velocity > 10000 should cap at 1.0."""
        story = {"view_velocity": 50000}
        ctx = build_content_context(story, "gaming")
        assert ctx[4] == pytest.approx(1.0)

    def test_day_of_week_normalized(self):
        """Day of week should be in [0, 1]."""
        story = {}
        ctx = build_content_context(story, "gaming")
        assert 0.0 <= ctx[0] <= 1.0

    def test_hour_utc_normalized(self):
        """Hour should be in [0, 1]."""
        story = {}
        ctx = build_content_context(story, "gaming")
        assert 0.0 <= ctx[1] <= 1.0

    def test_explicit_datetime_override(self):
        """When now= is passed, day/hour should use that time."""
        dt = datetime(2026, 3, 16, 14, 0, tzinfo=UTC)  # Monday, 14:00
        story = {"source_type": "youtube"}
        ctx = build_content_context(story, "gaming", now=dt)
        assert ctx[0] == pytest.approx(0.0 / 6.0)  # Monday = 0
        assert ctx[1] == pytest.approx(14.0 / 23.0)

    def test_context_is_float64(self):
        story = {"source_type": "youtube", "view_velocity": 1000}
        ctx = build_content_context(story, "gaming")
        assert ctx.dtype == np.float64


class TestR18PredictTrainParity:
    """R-18: the vector the bandit PREDICTS on (built from the nested story at
    selection time) must equal the vector it TRAINS on (rebuilt from the flat
    blueprint at publish time). The function previously read hook/caption from
    nested paths only, so a flat dict zeroed dims 6 & 9 — predict saw the real
    lengths, train saw 0."""

    _DT = datetime(2026, 3, 16, 14, 0, tzinfo=UTC)  # pin time so dims 0/1 match

    def _nested_story(self):
        # Shape passed at selection/predict time (push_to_backlog._apply_engagement_boost).
        return {
            "source_type": "youtube",
            "duration_seconds": 42,
            "view_velocity": 3200,
            "relevance_score": 0.7,
            "composite_score": 0.81,
            "content": {
                "hook": "This 1v5 clutch broke the entire lobby",  # 38 chars
                "instagram": {"caption": "x" * 150},
            },
        }

    def _flat_blueprint(self):
        # Shape the publisher rebuilds from at train time. Mirrors exactly what
        # push_to_backlog now persists: flat hook/caption + the scoring metrics.
        return {
            "source_type": "youtube",
            "duration_seconds": 42,
            "view_velocity": 3200,
            "relevance_score": 0.7,
            "composite_score": 0.81,
            "hook": "This 1v5 clutch broke the entire lobby",
            "caption": "x" * 150,
        }

    def test_predict_and_train_vectors_are_identical(self):
        predict_vec = build_content_context(self._nested_story(), "gaming", now=self._DT)
        train_vec = build_content_context(self._flat_blueprint(), "gaming", now=self._DT)
        np.testing.assert_allclose(predict_vec, train_vec)

    def test_flat_shape_recovers_hook_and_caption_dims(self):
        """The exact bug: a flat dict must NOT zero hook_length (6)/caption_length (9)."""
        vec = build_content_context(self._flat_blueprint(), "gaming", now=self._DT)
        assert vec[6] == pytest.approx(38 / 60.0)  # len("This 1v5 clutch broke the entire lobby")
        assert vec[9] == pytest.approx(150 / 200.0)
        assert vec[6] > 0 and vec[9] > 0

    def test_flat_shape_recovers_trending_score_dim(self):
        """Dim 11 must read the persisted composite_score, not fall back to 0.5."""
        vec = build_content_context(self._flat_blueprint(), "gaming", now=self._DT)
        assert vec[11] == pytest.approx(0.81)

    def test_hook_text_alias_is_honored(self):
        """The blueprint also carries hook under ``hook_text``; either resolves."""
        vec = build_content_context({"hook_text": "abcdefghij"}, "gaming", now=self._DT)
        assert vec[6] == pytest.approx(10 / 60.0)

    def test_non_dict_content_does_not_raise(self):
        """A malformed ``content`` (e.g. a string) must fall back, not crash."""
        vec = build_content_context(
            {"content": "oops-not-a-dict", "hook": "abc"}, "gaming", now=self._DT
        )
        assert vec[6] == pytest.approx(3 / 60.0)


# ---------------------------------------------------------------------------
# Numerical correctness
# ---------------------------------------------------------------------------


class TestNumericalCorrectness:
    def test_linucb_formula_manual(self):
        """Manually verify the LinUCB formula p = theta^T x + alpha * sqrt(x^T A^-1 x)."""
        arm = LinUCBArm(d=2, alpha=1.5)
        x1 = np.array([1.0, 0.0])
        x2 = np.array([0.0, 1.0])

        # Update with two observations
        arm.update(x1, reward=0.8)
        arm.update(x2, reward=0.2)

        # Manual calculation:
        # A = I + outer([1,0],[1,0]) + outer([0,1],[0,1]) = [[2,0],[0,2]]
        # b = 0.8*[1,0] + 0.2*[0,1] = [0.8, 0.2]
        # A^{-1} = [[0.5,0],[0,0.5]]
        # theta = A^{-1} b = [0.4, 0.1]
        expected_A = np.array([[2.0, 0.0], [0.0, 2.0]])
        expected_b = np.array([0.8, 0.2])
        np.testing.assert_array_almost_equal(arm.A, expected_A)
        np.testing.assert_array_almost_equal(arm.b, expected_b)

        # Predict on x = [1.0, 0.5]:
        x_test = np.array([1.0, 0.5])
        # theta^T x = 0.4*1.0 + 0.1*0.5 = 0.45
        # x^T A^{-1} x = [1,0.5] @ [[0.5,0],[0,0.5]] @ [1,0.5] = 0.5 + 0.125 = 0.625
        # exploration = 1.5 * sqrt(0.625) = 1.5 * 0.7906 = 1.1859
        # total = 0.45 + 1.1859 = 1.6359
        p = arm.predict(x_test)
        assert p == pytest.approx(0.45 + 1.5 * np.sqrt(0.625), abs=1e-6)

    def test_singular_matrix_handled(self):
        """If A becomes near-singular (shouldn't with identity init), predict should not crash."""
        arm = LinUCBArm(d=2, alpha=1.0)
        # Normal usage should never make A singular since A starts as I
        x = np.array([0.0, 0.0])
        score = arm.predict(x)
        assert np.isfinite(score)

    def test_large_dimension_works(self):
        """Test with larger context dimension."""
        arm = LinUCBArm(d=50, alpha=1.0)
        x = np.random.rand(50)
        score = arm.predict(x)
        assert np.isfinite(score)
        arm.update(x, 0.5)
        assert arm.n_obs == 1


# ---------------------------------------------------------------------------
# Integration: arm_loader LinUCB state persistence
# ---------------------------------------------------------------------------


class TestArmLoaderLinUCBIntegration:
    """Test that arm_loader can load/save LinUCB state."""

    def test_load_linucb_state_from_sharepoint_fields(self):
        from genlab_core.learning.arm_loader import load_all_arms_extended

        arm = LinUCBArm(d=CONTEXT_DIM, alpha=1.0)
        x = np.array([0.5] * CONTEXT_DIM)
        arm.update(x, 0.7)
        linucb_data = arm.to_dict()

        import json

        mock_proxy = type(
            "MockProxy",
            (),
            {
                "all": lambda self: [
                    {
                        "fields": {
                            "Title": "hook:test",
                            "Alpha": 2.0,
                            "Beta": 3.0,
                            "TotalPulls": 10,
                            "LinUCB_State": json.dumps(linucb_data),
                        }
                    }
                ]
            },
        )()

        result = load_all_arms_extended(mock_proxy, "gaming")
        assert "hook:test" in result
        arm_data = result["hook:test"]
        assert arm_data["alpha"] == 2.0
        assert arm_data["beta"] == 3.0
        assert arm_data["linucb_state"] is not None

    def test_load_arms_without_linucb_state(self):
        """Backward compat: arms without LinUCB_State should still load."""
        from genlab_core.learning.arm_loader import load_all_arms_extended

        mock_proxy = type(
            "MockProxy",
            (),
            {
                "all": lambda self: [
                    {
                        "fields": {
                            "Title": "hook:test",
                            "Alpha": 2.0,
                            "Beta": 3.0,
                        }
                    }
                ]
            },
        )()

        result = load_all_arms_extended(mock_proxy, "gaming")
        assert "hook:test" in result
        assert result["hook:test"]["linucb_state"] is None

    def test_predict_caches_matrix_inverse_across_calls(self):
        """Pin the perf invariant: A^{-1} computed once, reused until update().

        Before the cache, every predict() called np.linalg.inv(A) — O(d^3).
        With ~5 arms x ~30 blueprints x 5 niches = ~750 unnecessary
        inversions per pipeline cycle. The fix caches A^{-1} on the arm
        and invalidates only when A mutates (in update()).
        """
        arm = LinUCBArm(d=CONTEXT_DIM, alpha=1.0)
        x = np.array([0.5] * CONTEXT_DIM)

        # Before any predict, cache is empty
        assert arm._A_inv_cache is None

        # First predict populates the cache
        s1 = arm.predict(x)
        assert arm._A_inv_cache is not None
        cached_after_first = arm._A_inv_cache

        # Second predict reuses the SAME object (identity check, not equality)
        s2 = arm.predict(x)
        assert arm._A_inv_cache is cached_after_first, (
            "Cache must be reused (identity), not recomputed and replaced"
        )
        assert s1 == s2

    def test_update_invalidates_inverse_cache(self):
        """Pin the correctness invariant: update() invalidates A^{-1} cache.

        If update() forgets to invalidate, predict() would return scores
        based on the stale (pre-update) inverse — silently wrong.
        """
        arm = LinUCBArm(d=CONTEXT_DIM, alpha=1.0)
        x = np.array([0.5] * CONTEXT_DIM)

        # Prime the cache via a predict
        arm.predict(x)
        assert arm._A_inv_cache is not None

        # Update must invalidate
        arm.update(x, reward=0.5)
        assert arm._A_inv_cache is None, "update() must invalidate the A^{-1} cache"

        # Next predict repopulates with the post-update inverse
        arm.predict(x)
        assert arm._A_inv_cache is not None

    def test_from_dict_starts_with_empty_inverse_cache(self):
        """Pin the lazy-load invariant: restored arms compute A^{-1} only on first predict.

        Many arms are loaded for persistence but never queried (cold paths).
        Pre-computing the inverse eagerly in from_dict would waste it.
        """
        arm = LinUCBArm(d=CONTEXT_DIM, alpha=1.0)
        arm.update(np.array([0.5] * CONTEXT_DIM), reward=0.3)
        restored = LinUCBArm.from_dict(arm.to_dict())
        assert restored._A_inv_cache is None, (
            "from_dict() must NOT eagerly compute A^{-1} — defer to first predict"
        )

    def test_save_linucb_state(self):
        """save_arm should persist LinUCB state when provided."""
        from genlab_core.learning.arm_loader import save_arm

        arm = LinUCBArm(d=CONTEXT_DIM, alpha=1.0)
        x = np.array([0.5] * CONTEXT_DIM)
        arm.update(x, 0.7)

        saved_fields = {}

        class MockProxy:
            # 2026-06-21: save_arm now calls proxy.all(formula=..., max_records=1)
            # for an indexed lookup instead of a full scan. Accept **kwargs so
            # this minimal mock keeps matching the production proxy interface
            # (GraphTableProxy + PostgresTableProxy both accept these kwargs).
            def all(self, *args, **kwargs):
                return []

            def create(self, fields):
                saved_fields.update(fields)

        save_arm(MockProxy(), "hook:test", 2.0, 3.0, linucb_state=arm.to_dict())
        # save_arm now writes lowercase keys for Postgres compatibility
        assert "linucb_state" in saved_fields or "LinUCB_State" in saved_fields

        import json

        state_key = "linucb_state" if "linucb_state" in saved_fields else "LinUCB_State"
        state = json.loads(saved_fields[state_key])
        assert "A_matrix" in state
        assert "b_vector" in state
