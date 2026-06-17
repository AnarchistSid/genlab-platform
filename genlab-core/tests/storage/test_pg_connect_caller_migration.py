"""Migrated-caller pins: these 3 hot-path sites must route through pg_connect.

Why these tests exist
=====================
The 34 direct-psycopg sites in this codebase are being migrated to the
SR-A/C/D ``pg_connect`` shim incrementally. Each migrated site needs
a regression guard so a future "tidy-up" PR doesn't accidentally
revert it back to bare ``psycopg.connect``.

The check is greppable: each migrated module must import
``pg_connect`` (or use it via local symbol). We pin via source-file
inspection rather than runtime patches because:

  * Runtime patches require constructing the full call's input
    state, which varies per site
  * Source-file inspection is a single grep + survives refactors that
    move imports around

When migrating a new site, add it to ``_MIGRATED_SITES`` below.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]

# (file_path_relative, module_name_for_message). Each entry was migrated
# in PR ``feat/sr-acd-tier1-migration``. Future tier-2/3 migrations
# append here.
_MIGRATED_SITES = (
    # Tier-1 (PR #303, 2026-06-17 evening)
    ("genlab-core/src/genlab_core/intelligence/cost_persist.py", "cost_persist"),
    ("genlab-core/src/genlab_core/observability/dashboard_events.py", "dashboard_events"),
    (
        "genlab-core/src/genlab_core/scheduling/calibration_logger.py",
        "calibration_logger",
    ),
    # Tier-2 (this PR, 2026-06-17 late evening)
    (
        "genlab-core/src/genlab_core/intelligence/frequency_optimizer.py",
        "frequency_optimizer",
    ),
    (
        "genlab-core/src/genlab_core/intelligence/lifecycle_tracker.py",
        "lifecycle_tracker",
    ),
    (
        "genlab-core/src/genlab_core/learning/hook_classifier.py",
        "hook_classifier",
    ),
    (
        "genlab-core/src/genlab_core/learning/percentile_targets.py",
        "percentile_targets",
    ),
    (
        "genlab-core/src/genlab_core/scheduling/optimal_time_learner.py",
        "optimal_time_learner",
    ),
    (
        "genlab-core/src/genlab_core/engagement/token_health.py",
        "token_health",
    ),
    (
        "genlab-core/src/genlab_core/pipeline/stages/push_to_backlog.py",
        "push_to_backlog",
    ),
    # Tier-3 (this PR, 2026-06-17 night)
    (
        "genlab-core/src/genlab_core/monetization/ab_test_attribution.py",
        "ab_test_attribution",
    ),
    (
        "genlab-core/src/genlab_core/monetization/affiliate_matcher.py",
        "affiliate_matcher",
    ),
    (
        "genlab-core/src/genlab_core/monetization/embedding_matcher.py",
        "embedding_matcher",
    ),
    (
        "genlab-core/src/genlab_core/monetization/geo_link_resolver.py",
        "geo_link_resolver",
    ),
    (
        "genlab-core/src/genlab_core/monetization/proxy_revenue_aggregator.py",
        "proxy_revenue_aggregator",
    ),
    (
        "genlab-core/src/genlab_core/monetization/revenue_tracker.py",
        "revenue_tracker",
    ),
    (
        "genlab-core/src/genlab_core/monitoring/fb_survival_check.py",
        "fb_survival_check",
    ),
    (
        "genlab-core/src/genlab_core/learning/hook_training_data.py",
        "hook_training_data",
    ),
    (
        "genlab-core/src/genlab_core/media/trending_video_fetcher.py",
        "trending_video_fetcher",
    ),
    # Tier-4 (this PR, 2026-06-17 night)
    ("dashboard/server/api/alerts.py", "api_alerts"),
    ("dashboard/server/api/analytics.py", "api_analytics"),
    ("dashboard/server/api/audience.py", "api_audience"),
    ("dashboard/server/api/costs.py", "api_costs"),
    ("dashboard/server/api/events.py", "api_events"),
    ("dashboard/server/api/learning.py", "api_learning"),
    ("dashboard/server/api/links.py", "api_links"),
    ("dashboard/server/api/metrics.py", "api_metrics"),
    ("dashboard/server/api/overview.py", "api_overview"),
    ("dashboard/server/api/revenue.py", "api_revenue"),
    ("dashboard/server/api/sources.py", "api_sources"),
    ("dashboard/server/core/email_sender.py", "email_sender"),
    ("dashboard/server/core/publishing_queue.py", "publishing_queue"),
    ("dashboard/server/core/monetisation_progress_pg.py", "monetisation_progress_pg"),
    ("scripts/notify_critical_alerts.py", "notify_critical_alerts"),
    ("scripts/check_rls_policy_drift.py", "check_rls_policy_drift"),
    ("scripts/archive_stale_visual_paths.py", "archive_stale_visual_paths"),
    # Tier-5 (this PR, 2026-06-17 night)
    ("genlab-core/src/genlab_core/scheduling/auto_approver.py", "auto_approver"),
    ("genlab-core/src/genlab_core/learning/preference_collector.py", "preference_collector"),
    ("genlab-core/src/genlab_core/learning/bandit_residual.py", "bandit_residual"),
    ("genlab-core/src/genlab_core/learning/metric_collector.py", "metric_collector"),
    ("genlab-core/src/genlab_core/monitoring/health_monitor.py", "health_monitor"),
    ("genlab-core/src/genlab_core/pipeline/shared_ingestion.py", "shared_ingestion"),
)


# Pattern matching either:
#   from genlab_core.storage.tenant_context import pg_connect
#   from genlab_core.storage.tenant_context import (..., pg_connect, ...)
_IMPORT_PATTERN = re.compile(
    r"from\s+genlab_core\.storage\.tenant_context\s+import.*pg_connect",
    re.DOTALL,
)


# Pattern matching bare psycopg.connect — should NOT appear in migrated
# files (other than perhaps inside a docstring / comment, which we
# strip below).
_BARE_PSYCOPG_CONNECT = re.compile(r"psycopg\.connect\s*\(")


def _strip_comments_and_docstrings(source: str) -> str:
    """Remove triple-quoted strings + leading-# lines so the regex
    doesn't match references inside docstrings."""
    # Strip triple-quoted strings (both ''' and """).
    source = re.sub(r'"""[\s\S]*?"""', "", source)
    source = re.sub(r"'''[\s\S]*?'''", "", source)
    # Strip # comments.
    source = re.sub(r"^\s*#.*$", "", source, flags=re.MULTILINE)
    source = re.sub(r"#.*$", "", source, flags=re.MULTILINE)
    return source


@pytest.mark.parametrize("rel_path,name", _MIGRATED_SITES)
def test_migrated_site_imports_pg_connect(rel_path: str, name: str):
    """The migrated file must import ``pg_connect`` (top-level or
    locally inside the function)."""
    path = _REPO_ROOT / rel_path
    assert path.exists(), f"missing migrated file: {rel_path}"
    source = path.read_text(encoding="utf-8")
    assert _IMPORT_PATTERN.search(source), (
        f"{name}: expected an import of pg_connect from "
        "genlab_core.storage.tenant_context. If you reverted the "
        "migration intentionally, remove this site from _MIGRATED_SITES."
    )


@pytest.mark.parametrize("rel_path,name", _MIGRATED_SITES)
def test_migrated_site_no_bare_psycopg_connect_call(rel_path: str, name: str):
    """No bare ``psycopg.connect(...)`` call should remain in the
    migrated file. The ``import psycopg`` line is fine (some sites
    keep it for the ImportError except clause), but the ``.connect``
    call must go through ``pg_connect``."""
    path = _REPO_ROOT / rel_path
    source_stripped = _strip_comments_and_docstrings(path.read_text(encoding="utf-8"))
    matches = _BARE_PSYCOPG_CONNECT.findall(source_stripped)
    assert not matches, (
        f"{name}: found bare ``psycopg.connect(`` call after comment "
        f"stripping. Migration must route via pg_connect. "
        f"Matches: {matches}"
    )
