"""Tests for the CTA Thompson Sampling bandit."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from genlab_core.monetization.cta_bandit import CTABandit, CTAVariant

# Path to the real config file
_REAL_CONFIG = Path(__file__).parent.parent / "config" / "cta_variants.yaml"


def _fresh_bandit(**kwargs) -> CTABandit:
    """Create a bandit with a temp state path so tests don't pollute real config."""
    if "state_path" not in kwargs:
        kwargs["state_path"] = Path(tempfile.mktemp(suffix=".json"))
    return CTABandit(**kwargs)


class TestSelect:
    def test_select_returns_variant(self):
        bandit = _fresh_bandit()
        variant = bandit.select(platform="instagram")
        assert isinstance(variant, CTAVariant)

    def test_select_default_for_unknown_platform(self):
        bandit = _fresh_bandit()
        # ``snapchat`` has no variant config — falls back to the default arm.
        # (Threads used to be the unknown platform but acquired variants
        # in the cluster D stylistic fix.)
        variant = bandit.select(platform="snapchat")
        assert variant.arm_id == "default"
        assert variant.platform == "snapchat"

    def test_all_platforms_have_variants(self):
        bandit = _fresh_bandit()
        for platform in ("instagram", "youtube", "facebook", "threads"):
            variants = bandit.get_variants(platform)
            assert len(variants) > 0, f"Expected variants for {platform}"


class TestVariantFormat:
    def test_variant_format(self):
        variant = CTAVariant(
            arm_id="test_arm",
            platform="youtube",
            template="Get {product_name} here: {url}",
            emoji="🔗",
        )
        result = variant.format(product_name="PS5 Console", url="https://example.com")
        assert "PS5 Console" in result
        assert "https://example.com" in result

    def test_variant_format_with_emoji(self):
        variant = CTAVariant(
            arm_id="test_arm",
            platform="instagram",
            template="{product_name} — link in bio",
            emoji="🔗",
        )
        result = variant.format(product_name="PS5 Console")
        assert result.startswith("🔗")


class TestUpdate:
    def test_update_increments_alpha(self):
        bandit = _fresh_bandit()
        variants = bandit.get_variants("instagram")
        arm = variants[0]
        initial_alpha = arm.alpha
        bandit.update(arm_id=arm.arm_id, platform="instagram", clicked=True)
        assert arm.alpha == initial_alpha + 1.0

    def test_update_increments_beta(self):
        bandit = _fresh_bandit()
        variants = bandit.get_variants("instagram")
        arm = variants[0]
        initial_beta = arm.beta
        bandit.update(arm_id=arm.arm_id, platform="instagram", clicked=False)
        assert arm.beta == initial_beta + 1.0


class TestThompsonSampling:
    def test_thompson_sampling_converges(self):
        """After strongly rewarding one arm, it should be selected >70% of the time."""
        bandit = _fresh_bandit()
        variants = bandit.get_variants("instagram")
        assert len(variants) >= 2, "Need at least 2 variants for convergence test"

        winning_arm_id = variants[0].arm_id

        # Simulate 100 clicks on the winning arm, no clicks on others
        for _ in range(100):
            bandit.update(arm_id=winning_arm_id, platform="instagram", clicked=True)

        # Give the other arms some data too so they have defined posteriors
        for arm in variants[1:]:
            for _ in range(5):
                bandit.update(arm_id=arm.arm_id, platform="instagram", clicked=False)

        # Run 100 selections and count how many times the winning arm is picked
        win_count = sum(
            1 for _ in range(100)
            if bandit.select(platform="instagram").arm_id == winning_arm_id
        )
        assert win_count > 70, (
            f"Expected winning arm to be selected >70% of the time, got {win_count}%"
        )


class TestPersistence:
    def test_save_and_load_state(self):
        """State should survive save/load cycle."""
        state_path = Path(tempfile.mktemp(suffix=".json"))
        bandit = _fresh_bandit(state_path=state_path)
        variants = bandit.get_variants("instagram")
        arm = variants[0]
        arm_id = arm.arm_id

        # Update and save
        bandit.update(arm_id=arm_id, platform="instagram", clicked=True)
        expected_alpha = arm.alpha

        # Create a new bandit from the same state file
        bandit2 = _fresh_bandit(state_path=state_path)
        restored = bandit2.get_variants("instagram")
        restored_arm = [v for v in restored if v.arm_id == arm_id][0]
        assert restored_arm.alpha == expected_alpha

        # Clean up
        state_path.unlink(missing_ok=True)

    def test_missing_state_file_uses_priors(self):
        """No state file means fresh priors (1.0, 1.0)."""
        bandit = _fresh_bandit(state_path=Path("/nonexistent/state.json"))
        for v in bandit.get_variants("instagram"):
            assert v.alpha == 1.0
            assert v.beta == 1.0


class TestLoadConfig:
    def test_load_real_config(self):
        """Loads the actual cta_variants.yaml and validates structure."""
        assert _REAL_CONFIG.exists(), f"Real config not found at {_REAL_CONFIG}"
        # Use a nonexistent state path so no saved state contaminates priors
        bandit = _fresh_bandit(config_path=_REAL_CONFIG)

        for platform in ("instagram", "youtube", "facebook"):
            variants = bandit.get_variants(platform)
            assert len(variants) > 0, f"No variants loaded for {platform}"
            for v in variants:
                assert v.arm_id, f"arm_id is empty for a variant in {platform}"
                assert v.template, f"template is empty for arm {v.arm_id}"
                assert v.emoji, f"emoji is empty for arm {v.arm_id}"
                assert v.alpha == 1.0
                assert v.beta == 1.0

    def test_missing_config_returns_empty(self):
        """A nonexistent config path returns empty variants without crashing."""
        bandit = _fresh_bandit(config_path=Path("/nonexistent/path/cta_variants.yaml"))
        assert bandit.get_variants("instagram") == []
        assert bandit.get_variants("youtube") == []
        # select() should still return a default variant gracefully
        variant = bandit.select(platform="instagram")
        assert variant.arm_id == "default"
