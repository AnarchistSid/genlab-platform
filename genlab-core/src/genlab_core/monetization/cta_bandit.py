"""CTA variant selection via Thompson Sampling.

Selects CTA templates using a Thompson Sampling bandit. Each platform
has multiple CTA variants as arms. The bandit learns which converts best
from click-through data.

State is persisted to a JSON file so that learned posteriors survive
process restarts.

Usage:
    bandit = CTABandit()
    variant = bandit.select(platform="instagram")
    cta_text = variant.format(product_name="PS5 Console", url="...")
"""
from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_VARIANTS_PATH = Path(__file__).parent.parent.parent.parent / "config" / "cta_variants.yaml"
_STATE_PATH = Path(__file__).parent.parent.parent.parent.parent / ".tmp" / "cta_bandit_state.json"


@dataclass
class CTAVariant:
    arm_id: str
    platform: str
    template: str
    emoji: str = "🔗"
    alpha: float = 1.0  # Beta distribution prior
    beta: float = 1.0

    def format(self, **kwargs) -> str:
        """Format the CTA template with product_name, url, etc."""
        text = self.template.format(**kwargs)
        return f"{self.emoji} {text}"


class CTABandit:
    """Thompson Sampling bandit for CTA variant selection."""

    def __init__(self, config_path: Path | None = None, state_path: Path | None = None) -> None:
        self._config_path = config_path or _VARIANTS_PATH
        self._state_path = state_path or _STATE_PATH
        self._variants: dict[str, list[CTAVariant]] = self._load_variants()
        self.load_state()

    def select(self, platform: str) -> CTAVariant:
        """Select a CTA variant using Thompson Sampling.

        Samples from each arm's Beta(alpha, beta) posterior and picks
        the arm with the highest sample. Falls back to a default if
        no variants configured for the platform.
        """
        candidates = self._variants.get(platform, [])
        if not candidates:
            return CTAVariant(
                arm_id="default",
                platform=platform,
                template="{product_name}",
            )

        # Thompson Sampling: sample from Beta posterior for each arm
        samples = []
        for v in candidates:
            sample = random.betavariate(v.alpha, v.beta)
            samples.append((sample, v))

        # Pick the arm with the highest sample
        best = max(samples, key=lambda x: x[0])
        selected = best[1]
        logger.debug(
            "[CTABandit] Selected %s for %s (alpha=%.1f, beta=%.1f)",
            selected.arm_id, platform, selected.alpha, selected.beta,
        )
        return selected

    def get_variants(self, platform: str) -> list[CTAVariant]:
        """Return all variants for a platform."""
        return self._variants.get(platform, [])

    def update(self, arm_id: str, platform: str, clicked: bool) -> None:
        """Update posterior after observing click/no-click.

        clicked=True: increment alpha (successes)
        clicked=False: increment beta (failures)

        Persists state to disk after each update.
        """
        for v in self._variants.get(platform, []):
            if v.arm_id == arm_id:
                if clicked:
                    v.alpha += 1.0
                else:
                    v.beta += 1.0
                logger.debug(
                    "[CTABandit] Updated %s: alpha=%.1f, beta=%.1f",
                    arm_id, v.alpha, v.beta,
                )
                self.save_state()
                return

    def save_state(self) -> None:
        """Persist bandit posterior state to JSON file."""
        try:
            state: dict[str, dict[str, float]] = {}
            for platform, variants in self._variants.items():
                for v in variants:
                    key = f"{platform}:{v.arm_id}"
                    state[key] = {"alpha": v.alpha, "beta": v.beta}
            with open(self._state_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            logger.debug("[CTABandit] State saved to %s (%d arms)", self._state_path, len(state))
        except Exception as e:
            logger.warning("[CTABandit] Failed to save state: %s", e)

    def load_state(self) -> None:
        """Load bandit posterior state from JSON file.

        Restores alpha/beta values for each arm from the persisted state.
        Arms not found in the state file keep their default priors (1.0, 1.0).
        """
        if not self._state_path.exists():
            logger.debug("[CTABandit] No state file found at %s, using priors", self._state_path)
            return
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            restored = 0
            for platform, variants in self._variants.items():
                for v in variants:
                    key = f"{platform}:{v.arm_id}"
                    if key in state:
                        v.alpha = float(state[key].get("alpha", 1.0))
                        v.beta = float(state[key].get("beta", 1.0))
                        restored += 1
            logger.info("[CTABandit] Restored state for %d arms from %s", restored, self._state_path)
        except Exception as e:
            logger.warning("[CTABandit] Failed to load state: %s", e)

    def _load_variants(self) -> dict[str, list[CTAVariant]]:
        """Load variant definitions from YAML config."""
        if not self._config_path.exists():
            logger.warning("[CTABandit] Config not found: %s", self._config_path)
            return {}

        with open(self._config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

        variants: dict[str, list[CTAVariant]] = {}
        for platform, pdata in config.get("platforms", {}).items():
            variants[platform] = []
            for v in pdata.get("variants", []):
                variants[platform].append(CTAVariant(
                    arm_id=v["arm_id"],
                    platform=platform,
                    template=v["template"],
                    emoji=v.get("emoji", "🔗"),
                ))

        total = sum(len(v) for v in variants.values())
        logger.info("[CTABandit] Loaded %d variants across %d platforms", total, len(variants))
        return variants
