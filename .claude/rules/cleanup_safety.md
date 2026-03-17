# Cleanup Safety Rules

## Scheduled Posts Are Sacred (Non-negotiable)

**NEVER demote, delete, or clear data on blueprints that have a `scheduled_for` date.**

A scheduled post is part of the active publish queue. Removing it creates gaps in the 1-post-per-day schedule and breaks the pipeline.

### Before Any Bulk Cleanup:
1. Query the target blueprints
2. Filter OUT any with `scheduled_for` set
3. Only then proceed with demotion/deletion on the remainder

### Code Safeguards:
- `BacklogClient.update_blueprint_status()` blocks demotions on scheduled posts (raises ValueError)
- `BacklogClient.get_blueprints_safe_to_cleanup()` pre-filters out scheduled posts
- Pass `force=True` ONLY if you have explicit user confirmation to demote scheduled posts

### What Counts as "Scheduled":
- Any blueprint with a non-empty `scheduled_for` field, regardless of status
- This includes posts that may look low-priority — they were scheduled deliberately

## Local File Cleanup (Safe)
- `.tmp/runs/`, `.tmp/cache/`, logs — always safe to prune
- `cleanup_artifacts.py` handles local files only and does not touch the backlog
- Keep at least 3 recent runs (CLEANUP_KEEP_RUNS=3)
