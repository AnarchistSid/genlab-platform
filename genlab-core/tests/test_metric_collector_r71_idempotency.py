"""R-71: contract-test net — ``process_pending_task`` idempotency.

The audit (R-71) called for "a contract-test net" on the highest-
risk god-files. ``metric_collector.py`` is named (26 commits, the
most-active recently). Its existing 77 tests pin individual
behaviors well, but the **cross-call invariant** the source's own
docstring claims is unexercised:

    Idempotency:
      The pending_feedback state machine in process_pending_task
      guarantees a single bandit_updater fire per (task_id, window).

That's a property-level claim — it says nothing about a single
invocation; it says something about ALL invocations over a task's
lifetime. Today's ``test_returns_false_when_no_window`` mocks
``next_collection_window=None`` directly; it doesn't exercise the
actual handoff between the store and the collector across calls.

This file adds a contract test using a real-shape fake store that
tracks which windows have been collected. Calling
``process_pending_task`` repeatedly on the same task drives the
state machine to completion. The invariant: ``bandit_updater``
is called **exactly once** per (task_id, "48h"), regardless of
how many times the caller invokes ``process_pending_task``.

Why this matters operationally
------------------------------
The pending-feedback queue uses Dramatiq retries. A worker that
crashes after ``bandit_updater`` fires but before
``store.update_window`` commits will be re-dispatched. Without
idempotency on the bandit side, the same reward lands twice — the
arm's posterior drifts toward a count-corrupted Beta. Bug-shape
match to the 2026-05-20 bandit-saturation forensics. Catching
a regression of this invariant at CI time is precisely the
"contract-test net" R-71 asked for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from genlab_core.learning.metric_collector import process_pending_task
from genlab_core.learning.pending_feedback_store import (
    PendingFeedbackTask,
)
from genlab_core.learning.reward_shaper import RewardShaper

_NICHE_FLOORS = {  # mirrors the floors inside metric_collector
    "gaming": 30,
    "ai_creators": 20,
    "sports": 25,
    "movies": 20,
    "anime": 15,
}


# ── Fake store: a real-shape PendingFeedbackStore in memory ────────────


@dataclass
class _FakeStore:
    """Real-shape stand-in for ``PendingFeedbackStore`` that drives the
    state machine the same way the live store does.

    A ``next_collection_window`` returns the first window NOT in the
    task's ``completed_windows``; ``update_window`` appends to that
    set. That's all ``process_pending_task`` relies on for
    idempotency.
    """

    _windows: tuple[str, ...] = ("6h", "24h", "48h", "168h")
    update_calls: list = field(default_factory=list)

    def next_collection_window(
        self, task: PendingFeedbackTask, now: datetime | None = None
    ) -> str | None:
        completed = set(task.completed_windows or [])
        for w in self._windows:
            if w not in completed:
                return w
        return None

    def update_window(self, task: PendingFeedbackTask, window: str, reward_48h=None) -> None:
        self.update_calls.append((task.platform_post_id, window, reward_48h))
        # Mirror what the live store does — mark the window as
        # collected so the next ``next_collection_window`` advances.
        completed = list(task.completed_windows or [])
        if window not in completed:
            completed.append(window)
        task.completed_windows = completed

    def mark_bandit_processed(self, task: PendingFeedbackTask) -> None:
        """Stub for the live store method (added 2026-06-14). The R-71
        idempotency test doesn't care about the stamp contract; it
        just needs the method to exist so
        ``process_pending_task``'s ``store.mark_bandit_processed(...)``
        call doesn't raise AttributeError. The stamp contract is
        pinned separately in test_mark_bandit_processed.py."""
        pass

    # Live store has more methods but ``process_pending_task`` only
    # touches these three, so we keep the fake minimal. Anything else
    # would never be called by code under test.


def _task_at_48h_ready() -> PendingFeedbackTask:
    """Build a task with 6h/24h already collected so the next call
    will land in the 48h window."""
    return PendingFeedbackTask(
        content_id="r71_idempotency_001",
        platform="youtube",
        niche_id="gaming",
        published_at=datetime.now(UTC) - timedelta(hours=50),
        platform_post_id="post_r71",
        content_type="clip",
        hook_type="controversy",
        bandit_arm="clip__youtube",
        collection_status="awaiting_48h",
        completed_windows=["6h", "24h"],
    )


def _shaper(reward: float = 0.72) -> RewardShaper:
    s = MagicMock(spec=RewardShaper)
    s.compute_reward.return_value = reward
    return s


# ── The contract test ──────────────────────────────────────────────────


@patch("genlab_core.learning.metric_collector.fetch_platform_metrics")
def test_r71_bandit_updater_fires_at_most_once_per_task_48h_window(
    mock_fetch,
) -> None:
    """R-71 contract: across N invocations on the same task,
    ``bandit_updater`` fires AT MOST ONCE for the (task, "48h") pair.

    The state machine drives this via ``next_collection_window``
    returning a "48h" window only when it's not yet in
    ``completed_windows``. After ``update_window("48h", ...)``
    commits, subsequent calls land in "168h" then None.

    Pre-R-71, no test exercised this across calls — the closest
    existing test mocked the store directly, which can't catch a
    regression where the bandit fires inside the 168h branch or
    the early-stop branch.
    """
    mock_fetch.return_value = {"views": 12_000, "likes": 800}

    task = _task_at_48h_ready()
    store = _FakeStore()
    shaper = _shaper(reward=0.65)
    updater = MagicMock()

    # Call process_pending_task many times. The state machine
    # advances through 48h → 168h → None.
    results = [process_pending_task(task, store, shaper, bandit_updater=updater) for _ in range(5)]

    # Calls 1 and 2 return True (48h then 168h). Subsequent calls return
    # False (no window due). Without naming exact indices we just check
    # the shape: at least one True then a False suffix.
    assert results[0] is True, "first call must process the 48h window"
    assert results[-1] is False, "final call must be a no-op (no window due)"

    # The hard contract pin:
    assert updater.call_count == 1, (
        f"R-71 regression: bandit_updater fired {updater.call_count} times "
        "across 5 invocations on the same task. The (task_id, '48h') "
        "single-fire guarantee in process_pending_task's docstring is "
        "broken — a Dramatiq retry storm would now double-count rewards "
        "into the bandit posterior."
    )
    # And the args of that single call are the right ones.
    args = updater.call_args.args
    assert args[0] == "gaming"  # niche_id
    assert args[1] == "clip__youtube"  # bandit_arm
    assert args[2] == "youtube"  # platform
    assert args[3] == 0.65  # reward (from shaper)


@patch("genlab_core.learning.metric_collector.fetch_platform_metrics")
def test_r71_no_bandit_fire_at_168h_window(mock_fetch) -> None:
    """R-71 pin: the 48h-only contract means a task whose ONLY
    remaining window is 168h must NOT fire the bandit. This is
    the same shape of bug as the 2026-05-20 forensics where a
    code path quietly added a second fire-point off the 48h
    rails."""
    mock_fetch.return_value = {"views": 50_000, "likes": 3000}

    task = PendingFeedbackTask(
        content_id="r71_168h_only",
        platform="youtube",
        niche_id="gaming",
        published_at=datetime.now(UTC) - timedelta(hours=200),
        platform_post_id="post_168h",
        content_type="clip",
        hook_type="controversy",
        bandit_arm="clip__youtube",
        collection_status="awaiting_168h",
        completed_windows=["6h", "24h", "48h"],
    )
    store = _FakeStore()
    shaper = _shaper(reward=0.9)
    updater = MagicMock()

    result = process_pending_task(task, store, shaper, bandit_updater=updater)

    assert result is True, "168h window must still be collected (metrics path)"
    assert updater.call_count == 0, (
        "R-71 regression: bandit_updater fired at the 168h window. The "
        "48h reward is the single source of bandit truth — a 168h fire "
        "would double-count against the same (task, arm) pair."
    )


@patch("genlab_core.learning.metric_collector.fetch_platform_metrics")
def test_r71_no_bandit_fire_when_early_stopped_at_6h(mock_fetch) -> None:
    """R-71 pin: a 6h early-stop must NOT fire the bandit. The Bug F
    fix from the 2026-05-16 audit was exactly this — sending
    reward=0.05 at 6h hit the adaptive threshold floor and
    incremented α, treating a flop as a success. Catching a
    regression here is the contract-test net the audit asked for."""
    # Very low views → trips early-stop floor.
    mock_fetch.return_value = {"views": 5, "likes": 0}

    task = PendingFeedbackTask(
        content_id="r71_early_stop",
        platform="youtube",
        niche_id="gaming",  # floor=30
        published_at=datetime.now(UTC) - timedelta(hours=7),
        platform_post_id="post_es",
        content_type="clip",
        hook_type="controversy",
        bandit_arm="clip__youtube",
        collection_status="awaiting_6h",
        completed_windows=[],
    )
    store = _FakeStore()
    shaper = _shaper(reward=0.05)  # what the buggy path used to send
    updater = MagicMock()

    process_pending_task(task, store, shaper, bandit_updater=updater)

    assert updater.call_count == 0, (
        "R-71 regression: bandit_updater fired at the 6h early-stop "
        "path. This was Bug F (2026-05-16 audit) — a flop's reward=0.05 "
        "hit the adaptive-threshold floor and incremented α, treating "
        "a flop as a success. The pin guards against the regression."
    )
