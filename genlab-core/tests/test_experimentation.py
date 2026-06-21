"""Pin: experimentation layer drives I1 (random control) + I3 (ab_tests).

The bandit by design exploits — it always picks the best-UCB arm given
context. Without I1, there's never counterfactual data ("what would arm
B have done?"). Without I3, we can't statistically compare named
hypotheses (e.g. question hooks vs imperative hooks for sports).

These pins lock in:

  Opt-in (2):
  1. is_enabled returns False by default, True when env=1

  Allocation validation (4):
  2. valid allocation summing to 1.0 → True
  3. allocation with negative value → False
  4. empty allocation → False
  5. allocation summing to 0.5 → False

  I1 random control (5):
  6. epsilon=0.0 → always bandit
  7. epsilon=1.0 → always random
  8. RNG seed reproducible (same seed = same pick)
  9. selection_mode tagged correctly
  10. Empty all_arms returns bandit pick (graceful)

  I3 experiments (5):
  11. ABTest with valid allocation passes validate_allocation
  12. is_active honors start/end window
  13. is_active accepts open-ended (end_date=None)
  14. assign_to_experiment is deterministic-by-blueprint-id
  15. assign_to_experiment honors allocation distribution at scale
  16. assign_to_experiment returns None for inactive experiment
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from genlab_core.learning.experimentation import ABTest


class TestIsEnabled:
    def test_default_off(self, monkeypatch):
        from genlab_core.learning.experimentation import is_enabled

        monkeypatch.delenv("GENLAB_EXPERIMENTATION_ENABLED", raising=False)
        assert is_enabled() is False

    def test_env_one_is_on(self, monkeypatch):
        from genlab_core.learning.experimentation import is_enabled

        monkeypatch.setenv("GENLAB_EXPERIMENTATION_ENABLED", "1")
        assert is_enabled() is True


class TestValidateAllocation:
    def test_valid_50_50(self):
        from genlab_core.learning.experimentation import validate_allocation

        assert validate_allocation({"a": 0.5, "b": 0.5}) is True

    def test_valid_third_split(self):
        """Float-summation noise (0.333 × 3 = 0.999) must still pass."""
        from genlab_core.learning.experimentation import validate_allocation

        assert validate_allocation({"a": 0.333, "b": 0.333, "c": 0.334}) is True

    def test_empty_is_invalid(self):
        from genlab_core.learning.experimentation import validate_allocation

        assert validate_allocation({}) is False

    def test_negative_value_is_invalid(self):
        from genlab_core.learning.experimentation import validate_allocation

        assert validate_allocation({"a": -0.1, "b": 1.1}) is False

    def test_sum_not_one_is_invalid(self):
        from genlab_core.learning.experimentation import validate_allocation

        assert validate_allocation({"a": 0.3, "b": 0.3}) is False


class TestSelectArmWithRandomControl:
    def test_epsilon_zero_always_bandit(self):
        from genlab_core.learning.experimentation import (
            SELECTION_MODE_BANDIT,
            select_arm_with_random_control,
        )

        # 1000 trials at epsilon=0 — every single one must be bandit mode
        rng = random.Random(42)
        for _ in range(1000):
            sel = select_arm_with_random_control(
                bandit_pick="A",
                all_arms=["A", "B", "C"],
                epsilon=0.0,
                rng=rng,
            )
            assert sel.selection_mode == SELECTION_MODE_BANDIT
            assert sel.arm_id == "A"

    def test_epsilon_one_always_random(self):
        """Pin: epsilon=1.0 forces every play into random_control mode.
        The arm chosen can be anything from all_arms, but the MODE tag
        must always be random_control (data integrity for downstream
        attribution)."""
        from genlab_core.learning.experimentation import (
            SELECTION_MODE_RANDOM_CONTROL,
            select_arm_with_random_control,
        )

        rng = random.Random(42)
        for _ in range(100):
            sel = select_arm_with_random_control(
                bandit_pick="A",
                all_arms=["A", "B", "C"],
                epsilon=1.0,
                rng=rng,
            )
            assert sel.selection_mode == SELECTION_MODE_RANDOM_CONTROL
            assert sel.arm_id in {"A", "B", "C"}

    def test_rng_seed_reproducible(self):
        """Pin: same seed → same sequence of picks. Critical for being
        able to replay decisions in test/debug environments."""
        from genlab_core.learning.experimentation import select_arm_with_random_control

        results_a = [
            select_arm_with_random_control(
                "A", ["A", "B", "C"], epsilon=0.5, rng=random.Random(123)
            ).arm_id
            for _ in range(10)
        ]
        results_b = [
            select_arm_with_random_control(
                "A", ["A", "B", "C"], epsilon=0.5, rng=random.Random(123)
            ).arm_id
            for _ in range(10)
        ]
        assert results_a == results_b

    def test_empty_arms_returns_bandit_pick_gracefully(self):
        """Pin: empty all_arms is a caller bug, but we don't crash —
        return whatever the bandit said and tag it as bandit mode."""
        from genlab_core.learning.experimentation import (
            SELECTION_MODE_BANDIT,
            select_arm_with_random_control,
        )

        sel = select_arm_with_random_control("X", all_arms=[], epsilon=0.5)
        assert sel.arm_id == "X"
        assert sel.selection_mode == SELECTION_MODE_BANDIT

    def test_epsilon_clamped(self):
        """Pin: bogus epsilon values are clamped to [0, 1] not crashed."""
        from genlab_core.learning.experimentation import (
            SELECTION_MODE_BANDIT,
            select_arm_with_random_control,
        )

        # 1000 trials at "epsilon=-5" — should clamp to 0 → always bandit
        rng = random.Random(42)
        for _ in range(100):
            sel = select_arm_with_random_control("A", ["A", "B"], epsilon=-5.0, rng=rng)
            assert sel.selection_mode == SELECTION_MODE_BANDIT


class TestABTestActiveness:
    def test_active_when_in_window(self):
        from genlab_core.learning.experimentation import ABTest, is_active

        e = ABTest(
            experiment_id="exp_001",
            arms=["q", "i"],
            allocation={"q": 0.5, "i": 0.5},
            start_date="2026-01-01T00:00:00+00:00",
            end_date="2026-12-31T00:00:00+00:00",
        )
        now = datetime(2026, 6, 15, tzinfo=UTC)
        assert is_active(e, now=now) is True

    def test_inactive_before_start(self):
        from genlab_core.learning.experimentation import ABTest, is_active

        e = ABTest(
            experiment_id="exp_001",
            arms=["q", "i"],
            allocation={"q": 0.5, "i": 0.5},
            start_date="2026-06-01T00:00:00+00:00",
            end_date="2026-12-31T00:00:00+00:00",
        )
        now = datetime(2026, 5, 15, tzinfo=UTC)
        assert is_active(e, now=now) is False

    def test_inactive_after_end(self):
        from genlab_core.learning.experimentation import ABTest, is_active

        e = ABTest(
            experiment_id="exp_001",
            arms=["q", "i"],
            allocation={"q": 0.5, "i": 0.5},
            start_date="2026-01-01T00:00:00+00:00",
            end_date="2026-06-01T00:00:00+00:00",
        )
        now = datetime(2026, 7, 1, tzinfo=UTC)
        assert is_active(e, now=now) is False

    def test_open_ended_is_active(self):
        """Pin: end_date=None → no upper bound. Rolling experiments
        (e.g. continuous A/B on a UI element) need this."""
        from genlab_core.learning.experimentation import ABTest, is_active

        e = ABTest(
            experiment_id="exp_001",
            arms=["q", "i"],
            allocation={"q": 0.5, "i": 0.5},
            start_date="2026-01-01T00:00:00+00:00",
            end_date=None,
        )
        now = datetime(2030, 1, 1, tzinfo=UTC)
        assert is_active(e, now=now) is True


class TestAssignToExperiment:
    def _exp(self) -> ABTest:
        return ABTest(
            experiment_id="hook_style_2026_07",
            arms=["question", "imperative"],
            allocation={"question": 0.5, "imperative": 0.5},
            start_date="2026-01-01T00:00:00+00:00",
            end_date="2030-12-31T00:00:00+00:00",
        )

    def test_deterministic_by_blueprint_id(self):
        """Pin: same blueprint_id ALWAYS gets the same arm. Critical
        for pipeline re-runs — no double-counting from churn."""
        from genlab_core.learning.experimentation import assign_to_experiment

        exp = self._exp()
        results = [assign_to_experiment("bp_abc_123", exp).arm_id for _ in range(50)]
        # All 50 trials → same arm assignment
        assert len(set(results)) == 1

    def test_distribution_at_scale_matches_allocation(self):
        """Pin: across many blueprint_ids, the 50/50 allocation
        produces ~50/50 distribution. Tolerance: ±15% for n=1000."""
        from genlab_core.learning.experimentation import assign_to_experiment

        exp = self._exp()
        counts: dict[str, int] = {"question": 0, "imperative": 0}
        for i in range(1000):
            sel = assign_to_experiment(f"bp_{i:05d}", exp)
            assert sel is not None
            counts[sel.arm_id] += 1

        # Within ±15% of 50/50
        assert 350 <= counts["question"] <= 650
        assert 350 <= counts["imperative"] <= 650

    def test_returns_none_for_inactive_experiment(self):
        from genlab_core.learning.experimentation import ABTest, assign_to_experiment

        e = ABTest(
            experiment_id="exp_old",
            arms=["q", "i"],
            allocation={"q": 0.5, "i": 0.5},
            start_date="2020-01-01T00:00:00+00:00",
            end_date="2020-12-31T00:00:00+00:00",
        )
        assert assign_to_experiment("bp_001", e) is None

    def test_returns_none_for_invalid_allocation(self):
        """Pin: defensive — assignment refuses to bucket against a
        malformed allocation even if the caller registered it."""
        from genlab_core.learning.experimentation import ABTest, assign_to_experiment

        e = ABTest(
            experiment_id="exp_bad",
            arms=["q", "i"],
            allocation={"q": 0.3, "i": 0.3},  # Doesn't sum to 1.0
            start_date="2026-01-01T00:00:00+00:00",
            end_date="2030-12-31T00:00:00+00:00",
        )
        assert assign_to_experiment("bp_001", e) is None

    def test_selection_mode_and_experiment_id_set(self):
        from genlab_core.learning.experimentation import (
            SELECTION_MODE_AB_TEST,
            assign_to_experiment,
        )

        exp = self._exp()
        sel = assign_to_experiment("bp_xyz", exp)
        assert sel is not None
        assert sel.selection_mode == SELECTION_MODE_AB_TEST
        assert sel.experiment_id == exp.experiment_id

    def test_assignment_decorrelates_across_experiments(self):
        """Pin: hash salting with experiment_id means the same
        blueprint can get different arms in different experiments.
        Without the salt, every test would couple onto the same
        buckets."""
        from genlab_core.learning.experimentation import ABTest, assign_to_experiment

        exp_a = self._exp()
        exp_b = ABTest(
            experiment_id="different_experiment_2026_08",
            arms=["question", "imperative"],
            allocation={"question": 0.5, "imperative": 0.5},
            start_date="2026-01-01T00:00:00+00:00",
            end_date="2030-12-31T00:00:00+00:00",
        )

        # Across 100 blueprints, the per-blueprint assignment in exp_a
        # vs exp_b should differ for ~half of them (independence).
        differs = 0
        for i in range(100):
            bp = f"bp_decorr_{i}"
            a = assign_to_experiment(bp, exp_a)
            b = assign_to_experiment(bp, exp_b)
            if a is not None and b is not None and a.arm_id != b.arm_id:
                differs += 1
        # Independent salting → ~50% disagreement. Tolerance ±20.
        assert 30 <= differs <= 70

    def test_yesterday_start_date_is_active(self):
        """Sanity: an experiment that started yesterday with no end
        date should be active today."""
        from genlab_core.learning.experimentation import ABTest, is_active

        yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        e = ABTest(
            experiment_id="exp_rolling",
            arms=["q", "i"],
            allocation={"q": 0.5, "i": 0.5},
            start_date=yesterday,
            end_date=None,
        )
        assert is_active(e) is True
