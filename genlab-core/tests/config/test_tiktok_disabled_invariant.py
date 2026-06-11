"""Pin R-15: TikTok must not appear in any niche's `platforms_enabled`
list. The publish path uses that list to decide which platforms to
target; a stray `tiktok` entry would silently breach the
TikTok-disabled-until-audit policy.

The per-niche `tiktok: { enabled: false, audit_approved: false }`
policy blocks remain as documentation — those are explicit "off"
statements, not enablement.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]


NICHE_YAMLS = [
    REPO_ROOT / "BlackboxBrief" / "config" / "niche.yaml",
    REPO_ROOT / "ClutchWire" / "config" / "niche.yaml",
    REPO_ROOT / "FrameDrift" / "config" / "niche.yaml",
    REPO_ROOT / "SpliceReel" / "config" / "niche.yaml",
    REPO_ROOT / "CriticalRush" / "niches" / "gaming" / "config" / "niche.yaml",
]


@pytest.mark.parametrize("yaml_path", NICHE_YAMLS, ids=lambda p: p.parents[1].name)
def test_tiktok_not_in_platforms_enabled(yaml_path: Path) -> None:
    data = yaml.safe_load(yaml_path.read_text())
    # ``platforms_enabled`` can live at the root or nested under
    # ``content_pipeline`` / ``publishing``. Walk the dict and find
    # every occurrence.
    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "platforms_enabled" and isinstance(v, list):
                    yield v
                yield from walk(v)
        elif isinstance(obj, list):
            for item in obj:
                yield from walk(item)

    found_lists = list(walk(data))
    assert found_lists, (
        f"{yaml_path}: expected at least one `platforms_enabled` list"
    )
    for plist in found_lists:
        assert "tiktok" not in plist, (
            f"{yaml_path}: `tiktok` found in platforms_enabled "
            f"{plist!r} — see R-15 (TikTok disabled until audit)."
        )


def test_gaming_publishing_yaml_doesnt_enable_tiktok() -> None:
    """gaming `publishing.yaml` had `tiktok` under `platforms.enabled`
    before R-15. Pin the cleanup."""
    pub_yaml = (
        REPO_ROOT
        / "CriticalRush"
        / "niches"
        / "gaming"
        / "config"
        / "publishing.yaml"
    )
    data = yaml.safe_load(pub_yaml.read_text())
    enabled = (data.get("platforms") or {}).get("enabled") or []
    assert "tiktok" not in enabled, (
        f"gaming publishing.yaml `platforms.enabled` still contains "
        f"tiktok: {enabled!r} — see R-15."
    )
