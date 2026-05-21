"""Thompson Sampling multi-armed bandit for content formatting optimization.

Uses INDEPENDENT Beta-distributed bandits per dimension (not one bandit over
the full combinatorial space). This keeps the total arm count at ~18 across
4 dimensions instead of 360 combinations, allowing convergence in ~50-100 posts.

Each arm tracks a Beta(alpha, beta) posterior:
  - alpha: accumulated successes + 1 (prior)
  - beta:  accumulated failures + 1 (prior)
  - E[arm] = alpha / (alpha + beta)
  - Var[arm] = alpha*beta / ((alpha+beta)^2 * (alpha+beta+1))

Selection: draw from Beta(alpha, beta) for each arm; pick highest sample.
Update (binary):      success -> alpha += 1; failure -> beta += 1; neutral -> skip.
Update (continuous):  reward in [0,1] -> alpha += reward; beta += (1 - reward).
Discount: for non-stationary environments, decay all alphas/betas BEFORE update.
Exploration boost: shrink alpha/beta toward priors to widen posteriors.

State is persisted to JSON for cross-run continuity.

Transplanted from Gaming Clips execution/utils/bandit_optimizer.py.
Import change: standalone module, no script_bootstrap dependency.
"""

import json
import logging
import random
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLDS = {
    "success": {"completion_rate": 0.70, "engagement_rate": 0.05},
    "failure": {"completion_rate": 0.40, "engagement_rate": 0.01},
}


class BanditOptimizer:
    """Thompson Sampling multi-armed bandit with independent dimensions.

    Parameters
    ----------
    dimensions : dict[str, list[str]]
        Mapping of dimension_name -> list of arm labels.
        Example: {"hook_style": ["question", "bold_claim", ...]}
    discount_factor : float
        1.0 = stationary (no decay), 0.98 = non-stationary
        (old data has half-weight after ~34 rounds).
    thresholds : dict | None
        Custom success/failure thresholds. If None, uses DEFAULT_THRESHOLDS.
    """

    def __init__(
        self,
        dimensions: dict[str, list[str]],
        discount_factor: float = 1.0,
        thresholds: dict | None = None,
    ) -> None:
        if not dimensions:
            raise ValueError("dimensions must be a non-empty dict")
        for dim_name, arms in dimensions.items():
            if not arms:
                raise ValueError(f"arms for dimension '{dim_name}' must be non-empty")

        self.dimensions = dimensions
        self.discount_factor = discount_factor
        self.thresholds = thresholds or DEFAULT_THRESHOLDS
        self.total_trials = 0

        # Initialize state: Beta(1,1) uniform prior per arm
        self.state: dict = {}
        for dim_name, arms in dimensions.items():
            n = len(arms)
            self.state[dim_name] = {
                "arms": list(arms),
                "alphas": [1.0] * n,
                "betas": [1.0] * n,
                "pulls": [0] * n,
            }

    def select(self) -> dict[str, str]:
        """Select an arm from each dimension via Thompson Sampling.

        Draws a random sample from each arm's Beta(alpha, beta) distribution
        and picks the arm with the highest drawn sample per dimension.

        Returns
        -------
        dict[str, str]
            Mapping of dimension_name -> selected arm label.
        """
        selection = {}
        for dim_name, dim_state in self.state.items():
            best_sample = -1.0
            best_arm = dim_state["arms"][0]
            for i, arm in enumerate(dim_state["arms"]):
                sample = random.betavariate(
                    dim_state["alphas"][i],
                    dim_state["betas"][i],
                )
                if sample > best_sample:
                    best_sample = sample
                    best_arm = arm
            selection[dim_name] = best_arm
        return selection

    def _apply_discount(self, dim_state: dict) -> None:
        """Decay all arms in a dimension toward priors (non-stationary).

        Applied BEFORE the posterior update so that old evidence is decayed
        but the new observation is incorporated at full weight.
        """
        if self.discount_factor >= 1.0:
            return
        for i in range(len(dim_state["arms"])):
            dim_state["alphas"][i] = max(1.0, dim_state["alphas"][i] * self.discount_factor)
            dim_state["betas"][i] = max(1.0, dim_state["betas"][i] * self.discount_factor)

    def _classify_outcome(self, engagement_metrics: dict) -> str:
        """Classify engagement metrics as success, failure, or neutral.

        SUCCESS: completion_rate > threshold OR engagement_rate > threshold
        FAILURE: completion_rate < threshold AND engagement_rate < threshold
        NEUTRAL: everything in between (ambiguous signal, discarded)
        """
        cr = engagement_metrics.get("completion_rate", 0.0)
        er = engagement_metrics.get("engagement_rate", 0.0)

        success_cr = self.thresholds["success"]["completion_rate"]
        success_er = self.thresholds["success"]["engagement_rate"]
        failure_cr = self.thresholds["failure"]["completion_rate"]
        failure_er = self.thresholds["failure"]["engagement_rate"]

        if cr > success_cr or er > success_er:
            return "success"
        if cr < failure_cr and er < failure_er:
            return "failure"
        return "neutral"

    def update_binary(
        self,
        selection: dict[str, str],
        engagement_metrics: dict,
    ) -> str:
        """Update posteriors based on observed engagement (binary classification).

        Parameters
        ----------
        selection : dict[str, str]
            The arm choices that were used (from select()).
        engagement_metrics : dict
            Must contain 'completion_rate' and 'engagement_rate'.

        Returns
        -------
        str
            "success", "failure", or "neutral"
        """
        outcome = self._classify_outcome(engagement_metrics)

        if outcome == "neutral":
            return "neutral"

        reward = 1.0 if outcome == "success" else 0.0

        for dim_name, dim_state in self.state.items():
            arm_label = selection.get(dim_name)
            if arm_label is None:
                continue

            try:
                arm_idx = dim_state["arms"].index(arm_label)
            except ValueError:
                logger.warning(
                    "Unknown arm '%s' for dimension '%s', skipping",
                    arm_label,
                    dim_name,
                )
                continue

            # Discount BEFORE update (non-stationary decay)
            self._apply_discount(dim_state)

            # Update selected arm
            dim_state["alphas"][arm_idx] += reward
            dim_state["betas"][arm_idx] += 1.0 - reward
            dim_state["pulls"][arm_idx] += 1

        self.total_trials += 1
        return outcome

    def update(self, selection: dict[str, str], reward_or_metrics) -> str | None:
        """Update posteriors. Accepts continuous reward (float) or legacy metrics (dict).

        - If reward_or_metrics is a dict: delegates to update_binary()
        - If reward_or_metrics is a float: continuous reward update in [0.0, 1.0]
        """
        if isinstance(reward_or_metrics, dict):
            return self.update_binary(selection, reward_or_metrics)

        reward = reward_or_metrics
        if isinstance(reward, bool):
            raise TypeError("reward must be float or dict, got bool")
        if not isinstance(reward, (int, float)):
            raise TypeError(f"reward must be float or dict, got {type(reward)}")
        if not (0.0 <= reward <= 1.0):
            raise ValueError(f"reward must be in [0.0, 1.0], got {reward}")

        for dim_name, dim_state in self.state.items():
            arm_label = selection.get(dim_name)
            if arm_label is None:
                continue
            try:
                arm_idx = dim_state["arms"].index(arm_label)
            except ValueError:
                logger.warning("Unknown arm '%s' for dimension '%s', skipping", arm_label, dim_name)
                continue

            # Discount BEFORE update (same as binary path)
            self._apply_discount(dim_state)

            dim_state["alphas"][arm_idx] += reward
            dim_state["betas"][arm_idx] += 1.0 - reward
            dim_state["pulls"][arm_idx] += 1

        self.total_trials += 1
        return None

    def exploration_boost(self, factor: float) -> None:
        """Multiply all alphas and betas by factor to increase uncertainty.

        Factor < 1.0 shrinks parameters toward 1.0, widening Beta distributions.
        Floor at 1.0 to preserve valid Beta parameters.
        """
        if factor <= 0.0:
            raise ValueError(f"factor must be > 0.0, got {factor}")
        for dim_state in self.state.values():
            for i in range(len(dim_state["arms"])):
                dim_state["alphas"][i] = max(1.0, dim_state["alphas"][i] * factor)
                dim_state["betas"][i] = max(1.0, dim_state["betas"][i] * factor)

    def get_best_arms(self) -> dict[str, dict]:
        """Return the arm with highest expected value per dimension.

        E[Beta(a,b)] = a / (a + b)
        """
        result = {}
        for dim_name, dim_state in self.state.items():
            best_arm = dim_state["arms"][0]
            best_ev = 0.0
            for i, arm in enumerate(dim_state["arms"]):
                a = dim_state["alphas"][i]
                b = dim_state["betas"][i]
                ev = a / (a + b)
                if ev > best_ev:
                    best_ev = ev
                    best_arm = arm
            result[dim_name] = {"arm": best_arm, "expected_value": round(best_ev, 6)}
        return result

    def get_exploration_scores(self) -> dict[str, dict[str, float]]:
        """Return variance-based uncertainty scores per arm.

        Var[Beta(a,b)] = a*b / ((a+b)^2 * (a+b+1))
        """
        result = {}
        for dim_name, dim_state in self.state.items():
            dim_scores = {}
            for i, arm in enumerate(dim_state["arms"]):
                a = dim_state["alphas"][i]
                b = dim_state["betas"][i]
                ab = a + b
                var = (a * b) / (ab * ab * (ab + 1))
                dim_scores[arm] = round(var, 8)
            result[dim_name] = dim_scores
        return result

    def save(self, path: str) -> None:
        """Serialize optimizer state to JSON."""
        data = {
            "state": self.state,
            "dimensions": self.dimensions,
            "total_trials": self.total_trials,
            "discount_factor": self.discount_factor,
            "thresholds": self.thresholds,
            "saved_at": datetime.now(UTC).isoformat(),
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        logger.info("Bandit state saved to %s (%d trials)", path, self.total_trials)

    @classmethod
    def load(
        cls,
        path: str,
        dimensions: dict[str, list[str]] | None = None,
    ) -> "BanditOptimizer":
        """Load optimizer state from JSON with optional schema migration.

        If dimensions is provided and differs from saved state, new arms
        get Beta(1,1) priors and removed arms are dropped gracefully.
        """
        with open(path) as f:
            data = json.load(f)

        saved_dims = data.get("dimensions", {})
        target_dims = dimensions if dimensions is not None else saved_dims
        discount_factor = data.get("discount_factor", 1.0)
        thresholds = data.get("thresholds", DEFAULT_THRESHOLDS)

        optimizer = cls(target_dims, discount_factor=discount_factor, thresholds=thresholds)
        optimizer.total_trials = data.get("total_trials", 0)

        saved_state = data.get("state", {})

        # Schema migration: merge saved state into fresh state
        for dim_name in optimizer.state:
            if dim_name not in saved_state:
                continue

            old_dim = saved_state[dim_name]
            new_dim = optimizer.state[dim_name]

            old_arm_map = {}
            for i, arm in enumerate(old_dim.get("arms", [])):
                old_arm_map[arm] = i

            for new_idx, arm in enumerate(new_dim["arms"]):
                if arm in old_arm_map:
                    old_idx = old_arm_map[arm]
                    new_dim["alphas"][new_idx] = old_dim["alphas"][old_idx]
                    new_dim["betas"][new_idx] = old_dim["betas"][old_idx]
                    new_dim["pulls"][new_idx] = old_dim["pulls"][old_idx]

        logger.info(
            "Bandit state loaded from %s (%d trials, %d dimensions)",
            path,
            optimizer.total_trials,
            len(optimizer.state),
        )
        return optimizer
