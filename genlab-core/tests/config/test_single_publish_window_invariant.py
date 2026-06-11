"""Pin R-10: each niche's ``schedule.yaml`` declares exactly ONE
``publishing_windows`` entry.

The audit found gaming, sports, movies declared two windows (06:30
and 14:00 UTC) despite ``max_publishes_per_day: 1`` and the
documented "1 reel per channel per day" rule. The launchd publisher
timer fires at 06:35 (and 10:30) which matches no YAML window — the
``schedule.yaml`` is decorative, but operators read it as the
source of truth, so the duplicated window was actively misleading.

After R-10, all 5 niches have a single ``06:30`` UTC window. This
test fails the moment a future edit adds a second window back,
preventing the most direct duplicate-publish hazard from regressing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]

SCHEDULE_YAMLS = [
    REPO_ROOT / "BlackboxBrief" / "config" / "schedule.yaml",
    REPO_ROOT / "ClutchWire" / "config" / "schedule.yaml",
    REPO_ROOT / "FrameDrift" / "config" / "schedule.yaml",
    REPO_ROOT / "SpliceReel" / "config" / "schedule.yaml",
    REPO_ROOT / "CriticalRush" / "niches" / "gaming" / "config" / "schedule.yaml",
]


@pytest.mark.parametrize(
    "yaml_path",
    SCHEDULE_YAMLS,
    ids=lambda p: p.parents[1].name,
)
def test_schedule_yaml_has_exactly_one_publishing_window(yaml_path: Path) -> None:
    data = yaml.safe_load(yaml_path.read_text())
    windows = data.get("publishing_windows", [])
    assert isinstance(windows, list)
    assert len(windows) == 1, (
        f"{yaml_path}: expected exactly 1 publishing_windows entry, "
        f"got {len(windows)} ({[w.get('time') for w in windows]}). "
        f"See R-10 — multiple windows is the most direct "
        f"duplicate-publish hazard."
    )


@pytest.mark.parametrize(
    "yaml_path",
    SCHEDULE_YAMLS,
    ids=lambda p: p.parents[1].name,
)
def test_schedule_yaml_window_is_06_30_utc(yaml_path: Path) -> None:
    """The canonical window is 06:30 UTC (= 12:00 IST). A future
    edit that introduces a different time should be intentional and
    cross-reference the publisher launchd timer."""
    data = yaml.safe_load(yaml_path.read_text())
    windows = data.get("publishing_windows", [])
    window = windows[0]
    assert window.get("time") == "06:30", (
        f"{yaml_path}: expected 06:30 publishing window, "
        f"got {window.get('time')!r}. R-10 reconciliation pinned "
        f"this to match the launchd timer (06:35 UTC)."
    )
    assert window.get("timezone", "UTC") == "UTC", (
        f"{yaml_path}: window timezone must be UTC, got {window.get('timezone')!r}."
    )


@pytest.mark.parametrize(
    "yaml_path",
    SCHEDULE_YAMLS,
    ids=lambda p: p.parents[1].name,
)
def test_max_publishes_per_day_is_one(yaml_path: Path) -> None:
    """The doc rule is non-negotiable: 1 reel per channel per day."""
    data = yaml.safe_load(yaml_path.read_text())
    cap = data.get("max_publishes_per_day")
    assert cap == 1, (
        f"{yaml_path}: max_publishes_per_day must be 1, got {cap}. "
        f"R-10 + R-09 reinforce the 1-per-day invariant from both sides."
    )
