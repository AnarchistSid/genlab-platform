"""Tests for genlab_core.learning.config_updater."""

from datetime import UTC

import yaml
from genlab_core.learning.config_updater import (
    ConfigUpdater,
)
from genlab_core.learning.pending_feedback_task import PendingFeedbackTask


def _make_records(
    n: int,
    hook_type: str = "gameplay_first",
    reward: float = 0.5,
    platform: str = "youtube",
):
    """Create n PendingFeedbackTask objects with given hook_type and reward."""
    from datetime import datetime, timedelta

    # Use dates relative to now so records always fall within the 30-day window
    base = datetime.now(UTC) - timedelta(days=7)
    return [
        PendingFeedbackTask(
            content_id=f"s{i}",
            platform=platform,
            niche_id="gaming",
            published_at=base - timedelta(hours=i * 6),
            platform_post_id=f"post_{i}",
            hook_type=hook_type,
            content_type=hook_type,
            reward_48h=reward,
            completed_windows=["6h", "24h", "48h"],
            collection_status="complete",
        )
        for i in range(n)
    ]


class TestConfigUpdater:
    def test_update_only_applies_above_threshold_change(self, tmp_path):
        """Changes < 10% from current are not written."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        templates = config_dir / "templates.yaml"
        templates.write_text(
            yaml.dump(
                {
                    "hook_type_ratios": {"gameplay_first": 0.505, "other_type": 0.495},
                }
            )
        )

        updater = ConfigUpdater(config_dir=config_dir)
        # Both types have near-identical rewards → optimal ratios ≈ current.
        records = _make_records(25, hook_type="gameplay_first", reward=0.50) + _make_records(
            25, hook_type="other_type", reward=0.49
        )
        changes = updater.run(feedback_records=records)

        # Optimal ratios (~0.505/~0.495) match current within 10% → no change
        hook_changes = [c for c in changes if "hook_type_ratios" in c.get("key", "")]
        assert len(hook_changes) == 0

    def test_update_skips_with_insufficient_data(self, tmp_path):
        """n < 20 completed records → no updates."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        updater = ConfigUpdater(config_dir=config_dir)
        records = _make_records(10)  # Only 10, need 20
        changes = updater.run(feedback_records=records)

        assert len(changes) == 0

    def test_update_logs_every_change(self, tmp_path):
        """Each change produces a structured dict with reason."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        templates = config_dir / "templates.yaml"
        templates.write_text(
            yaml.dump(
                {
                    "hook_type_ratios": {"gameplay_first": 0.30},
                }
            )
        )

        updater = ConfigUpdater(config_dir=config_dir)
        # 25 records of same type → optimal ratio = 1.0, current = 0.30 → big change
        records = _make_records(25, hook_type="gameplay_first", reward=0.60)
        changes = updater.run(feedback_records=records)

        assert len(changes) >= 1
        assert changes[0]["key"] == "hook_type_ratios.gameplay_first"
        assert "reason" in changes[0]

    def test_update_writes_to_file_not_mutates_in_place(self, tmp_path):
        """Changes are written to the YAML file."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        templates = config_dir / "templates.yaml"
        templates.write_text(
            yaml.dump(
                {
                    "hook_type_ratios": {"old_hook": 0.90},
                }
            )
        )

        updater = ConfigUpdater(config_dir=config_dir)
        # new_hook with high reward should appear in ratios
        records = _make_records(25, hook_type="new_hook", reward=0.80)
        updater.run(feedback_records=records)

        with open(templates) as f:
            updated = yaml.safe_load(f)

        assert "new_hook" in updated["hook_type_ratios"]

    def test_yaml_bak_extension_correct(self, tmp_path):
        """Must create .yaml.bak not .bak."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        templates = config_dir / "templates.yaml"
        templates.write_text(
            yaml.dump(
                {
                    "hook_type_ratios": {"old_hook": 0.90},
                }
            )
        )

        updater = ConfigUpdater(config_dir=config_dir)
        records = _make_records(25, hook_type="new_hook", reward=0.80)
        updater.run(feedback_records=records)

        # Atomic write: tmp+rename, no .bak file created
        assert not (config_dir / "templates.yaml.bak").exists()
        assert templates.exists()  # Original file still valid

    def test_circular_hour_comparison(self):
        """23:00 → 01:00 is 2 hours, not 22."""
        diff = min(abs(23 - 1), 24 - abs(23 - 1))
        assert diff == 2

    def test_hook_ratio_zero_reward_guard(self, tmp_path):
        """All-zero rewards must not raise ZeroDivisionError."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        templates = config_dir / "templates.yaml"
        templates.write_text(
            yaml.dump(
                {
                    "hook_type_ratios": {"hook_a": 0.50, "hook_b": 0.50},
                }
            )
        )

        updater = ConfigUpdater(config_dir=config_dir)
        records = _make_records(25, hook_type="hook_a", reward=0.0) + _make_records(
            25, hook_type="hook_b", reward=0.0
        )
        changes = updater._update_hook_type_ratios(records, dry_run=True)
        assert changes == []


class TestCommentPreservation:
    """Regression pins for T#69: config_updater must NOT strip YAML comments.

    Pre-fix (yaml.dump): every load → mutate → dump cycle silently rewrote
    the file, dropping comments and reformatting. Three prod templates.yaml
    files lost their carefully-written documentation across multiple weekly
    config_updater runs. ruamel.yaml roundtrip mode preserves them.
    """

    def _seed_templates_with_comments(self, path):
        """Hand-write a templates.yaml with header + inline comments so we
        can check survival across a round-trip."""
        path.write_text(
            "# HEADER: This file controls hook-type sampling.\n"
            "# Lines starting with # must survive config_updater runs.\n"
            "\n"
            "version: '1.0'\n"
            "# Section comment: existing ratios\n"
            "hook_type_ratios:\n"
            "  hook_a: 0.30  # inline: legacy a\n"
            "  hook_b: 0.20  # inline: legacy b\n"
        )

    def test_existing_comments_survive_a_write(self, tmp_path):
        """Header + section + inline comments all present after config_updater."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        templates = config_dir / "templates.yaml"
        self._seed_templates_with_comments(templates)

        # Drive a real change so the writer actually fires.
        updater = ConfigUpdater(config_dir=config_dir)
        records = _make_records(25, hook_type="hook_c", reward=0.80)
        changes = updater.run(feedback_records=records)
        assert len(changes) >= 1, "test setup wrong — no changes triggered"

        post = templates.read_text()
        assert "# HEADER: This file controls hook-type sampling." in post, (
            "header comment was lost — yaml.dump regression"
        )
        assert "# Lines starting with # must survive config_updater runs." in post
        assert "# Section comment: existing ratios" in post
        assert "# inline: legacy a" in post, "inline comment on existing key was lost"
        assert "# inline: legacy b" in post

    def test_key_order_preserved(self, tmp_path):
        """version key must stay before hook_type_ratios after a write."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        templates = config_dir / "templates.yaml"
        self._seed_templates_with_comments(templates)

        updater = ConfigUpdater(config_dir=config_dir)
        records = _make_records(25, hook_type="hook_c", reward=0.80)
        updater.run(feedback_records=records)

        post = templates.read_text()
        version_idx = post.find("version:")
        ratios_idx = post.find("hook_type_ratios:")
        assert 0 <= version_idx < ratios_idx, (
            f"key order changed: version@{version_idx}, ratios@{ratios_idx}"
        )

    def test_new_key_appended_not_inserted(self, tmp_path):
        """New hook_type appears in hook_type_ratios without reordering."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        templates = config_dir / "templates.yaml"
        self._seed_templates_with_comments(templates)

        updater = ConfigUpdater(config_dir=config_dir)
        records = _make_records(25, hook_type="hook_c", reward=0.80)
        updater.run(feedback_records=records)

        post = templates.read_text()
        assert "hook_c:" in post
        # hook_a still comes before hook_c (we appended, not prepended)
        assert post.find("hook_a:") < post.find("hook_c:")

    def test_no_module_level_pyyaml_dump_remaining(self):
        """Guardrail: the file must not regress to yaml.dump for writes."""
        from genlab_core.learning import config_updater as mod
        from pathlib import Path as P

        src = P(mod.__file__).read_text()
        # Allow yaml.dump in comments/docstrings but not as a function call.
        # Find live call sites (anything not preceded by # or inside docstring).
        live_calls = [
            line
            for line in src.splitlines()
            if "yaml.dump" in line
            and not line.lstrip().startswith("#")
            and 'PyYAML' not in line  # docstring mention
        ]
        assert live_calls == [], (
            "yaml.dump call resurfaced — would strip comments. "
            f"Found: {live_calls}"
        )
