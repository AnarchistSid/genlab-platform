"""Pin: 2026-06-28 — every niche's publishing.yaml must have an
``auto_publish:`` block so the AUTO #2 enforcement worker can read
its per-niche policy.

Background: before PR Y, only BlackboxBrief had the block. The other
4 niches lacked the file shape, so a future operator wanting to flip
gaming/sports/movies/anime to AUTO #2 enforcement would have had to
scramble for the file template under time pressure. PR Y added the
block to all 4 with ``enabled: false`` + ``rollout_pct: 0.0`` (no
behavior change today — operator flips individual niches via a
1-line PR when calibration data justifies).

This pin guards against silent regression: if a future config refactor
loses the block from any niche, the AUTO #2 worker would silently
no-op for that niche (load_policy returns a disabled-default policy
when the block is missing).

Reference: ``docs/runbooks/AUTO_2_ROLLOUT_2026-06-15.md`` step S7.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

# Map niche_id → publishing.yaml path. Mirrors the per-channel layout
# (each channel package owns its niche config; CriticalRush nests it
# under niches/gaming/ because that package was the first to gain
# multi-niche scaffolding).
_NICHE_CONFIG_PATHS = {
    "ai_creators": REPO_ROOT / "BlackboxBrief" / "config" / "publishing.yaml",
    "sports": REPO_ROOT / "ClutchWire" / "config" / "publishing.yaml",
    "movies": REPO_ROOT / "SpliceReel" / "config" / "publishing.yaml",
    "anime": REPO_ROOT / "FrameDrift" / "config" / "publishing.yaml",
    "gaming": REPO_ROOT / "CriticalRush" / "niches" / "gaming" / "config" / "publishing.yaml",
}


@pytest.mark.parametrize("niche_id,config_path", list(_NICHE_CONFIG_PATHS.items()))
def test_niche_has_auto_publish_block(niche_id: str, config_path: Path) -> None:
    """Every niche's publishing.yaml must have an ``auto_publish:``
    block — the AUTO #2 enforcement worker reads it via load_policy(niche).
    Missing block → worker silently no-ops for that niche."""
    assert config_path.exists(), (
        f"{niche_id}: {config_path} must exist. The AUTO #2 worker's "
        f"load_policy({niche_id!r}) walks this path."
    )
    cfg = yaml.safe_load(config_path.read_text())
    assert "auto_publish" in cfg, (
        f"{niche_id}: {config_path} must declare an `auto_publish:` block. "
        f"Without it, the AUTO #2 enforcement worker silently returns a "
        f"disabled-default policy for this niche — operator-facing flip "
        f"becomes a no-op."
    )


@pytest.mark.parametrize("niche_id,config_path", list(_NICHE_CONFIG_PATHS.items()))
def test_auto_publish_has_required_keys(niche_id: str, config_path: Path) -> None:
    """auto_publish block must have the 4 keys the worker reads:
    enabled, min_confidence, max_approvals_per_pass, rollout_pct."""
    cfg = yaml.safe_load(config_path.read_text())
    block = cfg["auto_publish"]
    required = {"enabled", "min_confidence", "max_approvals_per_pass", "rollout_pct"}
    missing = required - set(block.keys())
    assert not missing, (
        f"{niche_id}: auto_publish block missing keys {missing}. "
        f"All 4 are required by the load_policy() reader."
    )


def test_only_ai_creators_is_enabled() -> None:
    """As of 2026-06-28, only ai_creators has AUTO #2 enabled (10%
    rollout, Week-1 ramp per PR #462). The other 4 niches are
    disabled-default. Any divergence from this expectation should be
    intentional (a per-niche flip PR) — pin catches accidental flips.

    To flip a niche on, this test will need to be UPDATED as part of
    the same PR — that's the audit-trail value (changing this pin
    requires a code-review touchpoint, preventing silent flips)."""
    enabled = {}
    for niche_id, path in _NICHE_CONFIG_PATHS.items():
        cfg = yaml.safe_load(path.read_text())
        enabled[niche_id] = cfg["auto_publish"].get("enabled", False)

    assert enabled["ai_creators"] is True, (
        "ai_creators should be enabled (Week-1 ramp at 10% per PR #462). "
        "If this fails, ai_creators was disabled — verify intentional."
    )
    for niche in ("sports", "movies", "anime", "gaming"):
        assert enabled[niche] is False, (
            f"{niche} should remain disabled. To enable, intentionally "
            f"update both this test AND the YAML in the same PR (per "
            f"AUTO_2_ROLLOUT runbook §9 — no silent flips)."
        )
