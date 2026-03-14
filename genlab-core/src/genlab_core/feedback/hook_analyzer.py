"""Hook performance data query and readiness reporter.

Queries PendingFeedback for hook training data and reports whether
the 200-example threshold for XGBoost classifier training has been met.

Usage:
    python -m genlab_core.feedback.hook_analyzer
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

MIN_EXAMPLES = 200
POSTS_PER_DAY = 15  # 1 post/day × 5 channels × 3 platforms


def get_hook_training_data(backlog_client: Any) -> list[dict]:
    """Returns PendingFeedback items that have hook + reward data.

    Requirements for a valid training example:
    - hook_text populated (non-empty)
    - reward_48h collected (non-null)

    Optionally:
    - reels_skip_rate (6h IG Reels metric) for direct hook quality signal
    """
    proxy = getattr(backlog_client, "pending_feedback", None)
    if proxy is None:
        logger.warning("[hook_analyzer] No pending_feedback proxy on BacklogClient")
        return []

    try:
        # Fetch completed items (all windows collected)
        items = proxy.all(formula="{Status}='complete'")
    except Exception as exc:
        logger.warning("[hook_analyzer] Query failed: %s", exc)
        return []

    training_data = []
    for item in items:
        fields = item.get("fields", item)
        hook_text = (fields.get("HookText") or "").strip()
        reward_raw = fields.get("Reward48h")

        if not hook_text:
            continue
        if reward_raw is None:
            continue

        try:
            reward = float(reward_raw)
        except (TypeError, ValueError):
            continue

        training_data.append({
            "hook_text": hook_text,
            "hook_type": fields.get("HookType", ""),
            "hook_length": int(fields.get("HookLength", 0) or 0),
            "platform": fields.get("Platform", ""),
            "niche_id": fields.get("NicheId", ""),
            "content_type": fields.get("PostContentType", ""),
            "reward_48h": reward,
            "reels_skip_rate": fields.get("ReelsSkipRate"),
        })

    return training_data


def print_hook_readiness_report(backlog_client: Any) -> None:
    """Print hook classifier training readiness report."""
    data = get_hook_training_data(backlog_client)
    count = len(data)
    remaining = max(0, MIN_EXAMPLES - count)
    days_est = remaining / max(POSTS_PER_DAY, 1)

    print("Hook classifier training readiness")
    print(f"  Examples collected: {count}/{MIN_EXAMPLES}")
    print(f"  Remaining:         {remaining}")
    print(f"  Estimated days:    {days_est:.0f}")
    print()

    if not data:
        print("  No data yet. Ensure PendingFeedback list exists and")
        print("  publish_all_platforms.py is recording hook fields.")
        return

    # Sort by reward for top/bottom hooks
    by_reward = sorted(data, key=lambda x: x["reward_48h"], reverse=True)
    print("Top 5 hooks (highest reward):")
    for d in by_reward[:5]:
        print(f"  [{d['reward_48h']:.3f}] {d['hook_text'][:80]}")

    print("\nBottom 5 hooks (lowest reward):")
    for d in by_reward[-5:]:
        print(f"  [{d['reward_48h']:.3f}] {d['hook_text'][:80]}")

    # Skip rate analysis (IG Reels only)
    with_skip = [d for d in data if d.get("reels_skip_rate") is not None]
    if with_skip:
        by_skip = sorted(with_skip, key=lambda x: x["reels_skip_rate"])
        print(f"\nIG Reels skip rate data: {len(with_skip)} examples")
        print("Best hooks (lowest skip rate):")
        for d in by_skip[:5]:
            print(f"  [{d['reels_skip_rate']:.2f}] {d['hook_text'][:80]}")


if __name__ == "__main__":
    from genlab_core.http.backlog_client import BacklogClient
    client = BacklogClient()
    print_hook_readiness_report(client)
