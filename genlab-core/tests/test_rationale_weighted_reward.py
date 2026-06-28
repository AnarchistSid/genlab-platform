"""Tests for genlab_core.learning.rationale_weighted_reward.

Pins the rationale-weighted reward wire (PR AA) — the bridge between
the click-rationale classifier (PR #607) and the bandit-update reward
path.

The wire is default-OFF until the env flag
``GENLAB_RATIONALE_WEIGHTED_REWARD_ENABLED=1`` is set. These tests
cover the strict-1 env pattern, fail-OPEN behaviour, the actual
multiplier table from the shipped YAML, and the engagement-positive
bypass.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from genlab_core.learning import rationale_weighted_reward as rwr
from genlab_core.learning.rationale_weighted_reward import (
    apply_rationale_multiplier,
    clear_cache,
    load_weights,
)


@pytest.fixture(autouse=True)
def _reset_module_cache(monkeypatch):
    """Force a fresh YAML read between tests + strip the env flag.

    Without this, env mutations in one test leak into the next AND
    the cached weights table survives across tests that swap in
    custom configs.
    """
    monkeypatch.delenv("GENLAB_RATIONALE_WEIGHTED_REWARD_ENABLED", raising=False)
    clear_cache()
    yield
    clear_cache()


# ---------------------------------------------------------------------------
# 1. Flag off: base reward unchanged regardless of rationale or arm
# ---------------------------------------------------------------------------


class TestFlagOff:
    def test_flag_off_returns_base_reward_unchanged(self, monkeypatch):
        """No env flag => passthrough. This is the default-OFF guarantee
        callers depend on for safe rollout."""
        monkeypatch.delenv("GENLAB_RATIONALE_WEIGHTED_REWARD_ENABLED", raising=False)
        assert apply_rationale_multiplier(-1.0, "weak_hook", "hook") == -1.0
        assert apply_rationale_multiplier(-1.0, "bad_fit", "style") == -1.0
        assert apply_rationale_multiplier(0.5, "weak_hook", "hook") == 0.5


# ---------------------------------------------------------------------------
# 2. Flag on but rationale=None: base reward unchanged (no operator click)
# ---------------------------------------------------------------------------


class TestNoRationale:
    def test_flag_on_no_rationale_returns_base_reward(self, monkeypatch):
        """When the classifier returns no high-confidence verdict the
        caller passes rationale=None. The wire must NOT amplify in
        that case — it should fall through to the default block, which
        for the shipped YAML is -1.0× on both arms (preserves
        pre-PR-AA behaviour).
        """
        monkeypatch.setenv("GENLAB_RATIONALE_WEIGHTED_REWARD_ENABLED", "1")
        # Default block in shipped YAML is -1.0× — base reward times
        # -1.0 = flipped sign. The rationale=None path uses default.
        result_hook = apply_rationale_multiplier(-1.0, None, "hook")
        result_style = apply_rationale_multiplier(-1.0, None, "style")
        assert result_hook == 1.0  # -1.0 * -1.0 (default multiplier)
        assert result_style == 1.0


# ---------------------------------------------------------------------------
# 3. weak_hook amplifies hook arm penalty MORE than style arm
# ---------------------------------------------------------------------------


class TestWeakHookAsymmetry:
    def test_weak_hook_amplifies_hook_arm_penalty_more_than_style(self, monkeypatch):
        """Core asymmetry from PR AA: a `weak_hook` rejection signals
        that the hook arm was wrong (-2.0× multiplier) more than the
        style arm (-0.5×). After the wire, the hook arm's effective
        penalty is 4x larger in magnitude than the style arm's.
        """
        monkeypatch.setenv("GENLAB_RATIONALE_WEIGHTED_REWARD_ENABLED", "1")
        base = -1.0
        hook_adjusted = apply_rationale_multiplier(base, "weak_hook", "hook")
        style_adjusted = apply_rationale_multiplier(base, "weak_hook", "style")
        # YAML: weak_hook → hook=-2.0, style=-0.5
        # -1.0 * -2.0 = +2.0; -1.0 * -0.5 = +0.5.
        assert hook_adjusted == pytest.approx(2.0)
        assert style_adjusted == pytest.approx(0.5)
        # Hook adjustment magnitude > style adjustment magnitude.
        assert abs(hook_adjusted) > abs(style_adjusted)


# ---------------------------------------------------------------------------
# 4. bad_fit dampens both arms (upstream source picked wrong; arms blameless)
# ---------------------------------------------------------------------------


class TestBadFitDampening:
    def test_bad_fit_dampens_both_arms(self, monkeypatch):
        """`bad_fit` = upstream source mismatch (e.g. MMA in anime).
        Neither bandit arm was at fault — the source picker was.
        Multiplier is -0.2× on both arms so the bandit barely moves.
        """
        monkeypatch.setenv("GENLAB_RATIONALE_WEIGHTED_REWARD_ENABLED", "1")
        base = -1.0
        hook_adjusted = apply_rationale_multiplier(base, "bad_fit", "hook")
        style_adjusted = apply_rationale_multiplier(base, "bad_fit", "style")
        # YAML: bad_fit → hook=-0.2, style=-0.2
        assert hook_adjusted == pytest.approx(0.2)
        assert style_adjusted == pytest.approx(0.2)
        # Both dampened below the default magnitude (1.0).
        assert abs(hook_adjusted) < 1.0
        assert abs(style_adjusted) < 1.0


# ---------------------------------------------------------------------------
# 5. Unknown rationale falls through to default multipliers
# ---------------------------------------------------------------------------


class TestUnknownRationaleFallsToDefault:
    def test_unknown_rationale_falls_to_default(self, monkeypatch):
        """Defensive: a category the classifier somehow emits that
        isn't in the YAML (e.g. a future enum value before the YAML
        is updated, or "uncategorized") falls through to the default
        block. Result: same as if rationale were None.
        """
        monkeypatch.setenv("GENLAB_RATIONALE_WEIGHTED_REWARD_ENABLED", "1")
        # Default in shipped YAML is -1.0× on both arms.
        result_hook = apply_rationale_multiplier(-1.0, "something_new_2027", "hook")
        result_style = apply_rationale_multiplier(-1.0, "uncategorized", "style")
        assert result_hook == 1.0
        assert result_style == 1.0


# ---------------------------------------------------------------------------
# 6. Config file loaded from YAML — verifies the actual shipped file
# ---------------------------------------------------------------------------


class TestConfigFileLoad:
    def test_config_file_loaded_from_yaml(self):
        """The shipped YAML at config/learning_rationale_weights.yaml
        must parse, contain all 6 canonical categories, and contain
        a `default` fallback block. Acts as a contract test against
        the file shape.
        """
        weights = load_weights()
        assert isinstance(weights, dict)
        assert "rationale_weights" in weights
        assert "default" in weights

        rationale_weights = weights["rationale_weights"]
        # All 6 canonical categories from
        # rationale_classifier.CANONICAL_CATEGORIES must be present.
        expected_categories = {
            "weak_hook",
            "too_generic",
            "unsupported_claim",
            "bad_fit",
            "too_long",
            "low_value",
        }
        assert expected_categories.issubset(rationale_weights.keys())

        # Every category dict has both arm multiplier keys.
        for cat, mults in rationale_weights.items():
            assert "hook_arm_multiplier" in mults, f"{cat} missing hook_arm_multiplier"
            assert "style_arm_multiplier" in mults, f"{cat} missing style_arm_multiplier"

        # Default block has both arm multiplier keys.
        default = weights["default"]
        assert "hook_arm_multiplier" in default
        assert "style_arm_multiplier" in default

    def test_load_weights_with_custom_path(self, tmp_path: Path):
        """load_weights accepts a path override — used by tests to
        swap in alternative configs without touching the shipped file.
        """
        custom = tmp_path / "custom.yaml"
        custom.write_text(
            yaml.safe_dump(
                {
                    "rationale_weights": {
                        "weak_hook": {
                            "hook_arm_multiplier": -3.0,
                            "style_arm_multiplier": -1.0,
                        },
                    },
                    "default": {
                        "hook_arm_multiplier": -1.0,
                        "style_arm_multiplier": -1.0,
                    },
                }
            )
        )
        loaded = load_weights(custom)
        assert loaded["rationale_weights"]["weak_hook"]["hook_arm_multiplier"] == -3.0


# ---------------------------------------------------------------------------
# 7. Strict-string "1" for env flag (PR #634 pattern — "true"/"yes" invalid)
# ---------------------------------------------------------------------------


class TestStrictOnePattern:
    def test_strict_string_1_for_env_flag(self, monkeypatch):
        """Matches the strict-1 opt-in pattern from PR #634
        (GENLAB_LOG_SELECTION_PROPENSITY) and rationale_classifier.
        ``"true"`` / ``"yes"`` / ``"on"`` / ``"True"`` are explicitly
        NOT accepted — keeps the activation gesture unambiguous.
        """
        # Truthy-looking values that must NOT enable.
        for non_one in ("true", "yes", "on", "True", "TRUE", "1.0", "", "  "):
            monkeypatch.setenv("GENLAB_RATIONALE_WEIGHTED_REWARD_ENABLED", non_one)
            assert apply_rationale_multiplier(-1.0, "weak_hook", "hook") == -1.0, (
                f"{non_one!r} should NOT enable rationale weighting"
            )

        # Only literal "1" (with optional surrounding whitespace) enables.
        monkeypatch.setenv("GENLAB_RATIONALE_WEIGHTED_REWARD_ENABLED", "1")
        result = apply_rationale_multiplier(-1.0, "weak_hook", "hook")
        assert result == pytest.approx(2.0)  # -1.0 * -2.0 from YAML.

        monkeypatch.setenv("GENLAB_RATIONALE_WEIGHTED_REWARD_ENABLED", "  1  ")
        result_padded = apply_rationale_multiplier(-1.0, "weak_hook", "hook")
        assert result_padded == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# 8. Positive (engagement) reward unchanged — wire only affects rejections
# ---------------------------------------------------------------------------


class TestPositiveRewardBypass:
    def test_positive_base_reward_unchanged(self, monkeypatch):
        """Rationale multipliers ONLY apply to negative base rewards
        (the rejection path). Engagement-positive rewards from the
        metric collector path arrive with no rationale (or a stale
        one) and must pass through untouched — amplifying a +0.7
        reward by -2.0 would corrupt the engagement learning signal.
        """
        monkeypatch.setenv("GENLAB_RATIONALE_WEIGHTED_REWARD_ENABLED", "1")
        # Positive rewards bypass even when rationale is set.
        assert apply_rationale_multiplier(0.7, "weak_hook", "hook") == 0.7
        assert apply_rationale_multiplier(0.5, "bad_fit", "style") == 0.5
        assert apply_rationale_multiplier(1.0, "too_generic", "hook") == 1.0
        # Zero is the boundary — treated as non-negative, passes through.
        assert apply_rationale_multiplier(0.0, "weak_hook", "hook") == 0.0


# ---------------------------------------------------------------------------
# Defensive extras — invalid arm_kind + RewardShaper static method wire
# ---------------------------------------------------------------------------


class TestDefensiveBehaviour:
    def test_invalid_arm_kind_returns_base(self, monkeypatch):
        """Typo or unknown arm_kind => return base unchanged. Prevents
        silent miscredit when a caller passes 'header' instead of 'hook'.
        """
        monkeypatch.setenv("GENLAB_RATIONALE_WEIGHTED_REWARD_ENABLED", "1")
        assert apply_rationale_multiplier(-1.0, "weak_hook", "header") == -1.0
        assert apply_rationale_multiplier(-1.0, "weak_hook", "") == -1.0

    def test_missing_config_file_falls_back_to_passthrough(self, monkeypatch, tmp_path: Path):
        """If the YAML disappears (deployment skew, missing config in
        a slim install), every multiplier returns 1.0 — base reward
        unchanged. Fail-OPEN is the contract.
        """
        monkeypatch.setenv("GENLAB_RATIONALE_WEIGHTED_REWARD_ENABLED", "1")
        monkeypatch.setattr(rwr, "_config_path", lambda: tmp_path / "missing.yaml")
        clear_cache()
        # multiplier=1.0 → base unchanged regardless of sign.
        assert apply_rationale_multiplier(-1.0, "weak_hook", "hook") == -1.0

    def test_reward_shaper_static_method_wires_through(self, monkeypatch):
        """RewardShaper.apply_rationale_multiplier is a thin re-export
        so callers already holding a shaper instance don't need an
        extra import. Verify it returns the same value as the
        underlying function.
        """
        from genlab_core.learning.reward_shaper import RewardShaper

        monkeypatch.setenv("GENLAB_RATIONALE_WEIGHTED_REWARD_ENABLED", "1")
        via_shaper = RewardShaper.apply_rationale_multiplier(-1.0, "weak_hook", "hook")
        via_module = apply_rationale_multiplier(-1.0, "weak_hook", "hook")
        assert via_shaper == via_module == pytest.approx(2.0)
