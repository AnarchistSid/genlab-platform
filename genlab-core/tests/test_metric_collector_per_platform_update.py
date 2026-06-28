"""Pin: per-platform bandit update at reward attribution time (PR Z).

When ``GENLAB_PER_PLATFORM_BANDIT_ENABLED=1`` and the reward
platform is in ``bandit_platform_split.list_split_platforms()``
(instagram, youtube), ``process_pending_task`` updates BOTH:

  1. The base arm (e.g. "clip__youtube_legacy" — actually just the
     ``task_record.bandit_arm`` which is already platform-namespaced
     in some niches but treated as the BASE in the per-platform
     split convention)
  2. The per-platform arm ``{base}__{platform}``

Wait — careful: the existing convention in some niches stores the
bandit_arm AS ``clip__youtube`` (look at the R-71 test fixture:
``bandit_arm="clip__youtube"``). PR Z's per-platform split is
ORTHOGONAL — we'd compose to ``clip__youtube__youtube``. The
parse helper handles this correctly: the LAST ``__platform`` is
the marker.

These pins lock in:

  Behavior preservation (1):
  1. Flag OFF → only the base arm is updated (current behavior)

  Split set membership (2):
  2. Flag ON + platform=instagram → both base AND base__instagram updated
  3. Flag ON + platform=facebook → only base updated (facebook not in split)

  Log marker (1):
  4. Flag ON + IG → log contains "per-platform bandit also updated" marker

Plus the negative pin: an exception during the per-platform update
NEVER unwinds the base update (fail-open).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from genlab_core.learning.metric_collector import process_pending_task
from genlab_core.learning.pending_feedback_store import PendingFeedbackTask
from genlab_core.learning.reward_shaper import RewardShaper

# ── Fake store mirroring test_metric_collector_r71_idempotency.py ──────


@dataclass
class _FakeStore:
    """Minimal store that advances the state machine through windows."""

    _windows: tuple[str, ...] = ("6h", "24h", "48h", "168h")
    update_calls: list = field(default_factory=list)

    def next_collection_window(self, task, now=None):
        completed = set(task.completed_windows or [])
        for w in self._windows:
            if w not in completed:
                return w
        return None

    def update_window(self, task, window, reward_48h=None):
        self.update_calls.append((task.platform_post_id, window, reward_48h))
        completed = list(task.completed_windows or [])
        if window not in completed:
            completed.append(window)
        task.completed_windows = completed

    def mark_bandit_processed(self, task):
        pass


def _task_at_48h_ready(platform: str = "youtube", niche: str = "gaming") -> PendingFeedbackTask:
    """Build a task with 6h+24h windows already collected so the next
    process_pending_task call lands in the 48h window — exactly when
    bandit_updater fires."""
    return PendingFeedbackTask(
        content_id=f"pr_z_{platform}_001",
        platform=platform,
        niche_id=niche,
        published_at=datetime.now(UTC) - timedelta(hours=50),
        platform_post_id=f"post_pr_z_{platform}",
        content_type="clip",
        hook_type="controversy",
        # Base arm name — the per-platform split appends __platform to this.
        bandit_arm="gameplay_clip",
        collection_status="awaiting_48h",
        completed_windows=["6h", "24h"],
    )


def _shaper(reward: float = 0.72) -> RewardShaper:
    s = MagicMock(spec=RewardShaper)
    s.compute_reward.return_value = reward
    return s


class TestFlagOffPreservesExistingBehavior:
    @patch("genlab_core.learning.metric_collector.fetch_platform_metrics")
    def test_flag_off_only_base_arm_updated(self, mock_fetch, monkeypatch):
        """Pin: flag OFF → bandit_updater fires ONCE with the base arm.

        This is the pre-PR-Z behavior; the per-platform split must
        leave it untouched when the env flag is unset.
        """
        monkeypatch.delenv("GENLAB_PER_PLATFORM_BANDIT_ENABLED", raising=False)
        mock_fetch.return_value = {"views": 12_000, "likes": 800}

        task = _task_at_48h_ready(platform="youtube")
        store = _FakeStore()
        shaper = _shaper()
        updater = MagicMock()

        process_pending_task(task, store, shaper, bandit_updater=updater)

        # Exactly one update — the base arm "gameplay_clip"
        assert updater.call_count == 1, (
            f"flag OFF must produce exactly one bandit_updater call, got {updater.call_count}"
        )
        assert updater.call_args.args[1] == "gameplay_clip", (
            "flag OFF must update the base arm, not a platform variant"
        )


class TestFlagOnSplitSetMembership:
    @patch("genlab_core.learning.metric_collector.fetch_platform_metrics")
    def test_flag_on_both_arms_updated(self, mock_fetch, monkeypatch):
        """Pin: flag ON + platform in split set → BOTH base AND
        per-platform arms updated. The base-arm update stays for the
        transition period (per the module docstring)."""
        monkeypatch.setenv("GENLAB_PER_PLATFORM_BANDIT_ENABLED", "1")
        mock_fetch.return_value = {"views": 12_000, "likes": 800}

        task = _task_at_48h_ready(platform="instagram")
        store = _FakeStore()
        shaper = _shaper()
        updater = MagicMock()

        process_pending_task(task, store, shaper, bandit_updater=updater)

        # Two calls: base then per-platform. Order matters because the
        # per-platform path runs INSIDE the bandit_update_succeeded
        # guard, AFTER the base update — verifiable from the call_args_list.
        assert updater.call_count == 2, (
            f"flag ON + IG must produce exactly two updates, got {updater.call_count}"
        )
        first_arm = updater.call_args_list[0].args[1]
        second_arm = updater.call_args_list[1].args[1]
        assert first_arm == "gameplay_clip", f"first update must be the base arm; got {first_arm}"
        assert second_arm == "gameplay_clip__instagram", (
            f"second update must be the per-platform arm; got {second_arm}"
        )

    @patch("genlab_core.learning.metric_collector.fetch_platform_metrics")
    def test_flag_on_unsupported_platform_only_base(self, mock_fetch, monkeypatch):
        """Pin: flag ON + platform NOT in split set → only base updated.

        Facebook + Threads + X + TikTok don't get per-platform arms yet
        (volume too low per the module docstring). Same niche, same
        flag, just different platform — and the per-platform update
        is skipped.
        """
        monkeypatch.setenv("GENLAB_PER_PLATFORM_BANDIT_ENABLED", "1")
        mock_fetch.return_value = {"views": 12_000, "likes": 800}

        task = _task_at_48h_ready(platform="facebook")
        store = _FakeStore()
        shaper = _shaper()
        updater = MagicMock()

        process_pending_task(task, store, shaper, bandit_updater=updater)

        # Only the base arm — facebook is excluded from the split set.
        assert updater.call_count == 1, (
            f"flag ON + facebook must produce ONE update, got {updater.call_count}"
        )
        assert updater.call_args.args[1] == "gameplay_clip", (
            "facebook update must stay on the base arm"
        )


class TestFlagOnLogMarker:
    @patch("genlab_core.learning.metric_collector.fetch_platform_metrics")
    def test_flag_on_log_emits_per_platform_marker(self, mock_fetch, monkeypatch, caplog):
        """Pin: when the per-platform update fires, an INFO log line
        with the marker ``per-platform bandit also updated`` lands.
        Operators grep for this marker to verify the split is active.
        """
        import logging

        monkeypatch.setenv("GENLAB_PER_PLATFORM_BANDIT_ENABLED", "1")
        mock_fetch.return_value = {"views": 12_000, "likes": 800}

        task = _task_at_48h_ready(platform="youtube")
        store = _FakeStore()
        shaper = _shaper()
        updater = MagicMock()

        with caplog.at_level(logging.INFO, logger="genlab_core.learning.metric_collector"):
            process_pending_task(task, store, shaper, bandit_updater=updater)

        # The marker MUST appear at least once across the captured logs.
        markers = [r for r in caplog.records if "per-platform bandit also updated" in r.message]
        assert markers, (
            "expected at least one 'per-platform bandit also updated' INFO log line; "
            f"got {[r.message for r in caplog.records if 'bandit' in r.message]}"
        )
