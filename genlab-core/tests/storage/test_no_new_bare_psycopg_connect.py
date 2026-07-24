"""Global regression guard: no NEW bare ``psycopg.connect`` calls.

Acts as the "ruff rule" called out in the post-Wave-4 audit. Pure ruff
can't easily express "ban this function call except in these files" via
config alone (ruff's banned-api targets imports, not call expressions),
so we use a pytest scan instead — runs in CI on every PR, fails fast
with file:line:reason, and the allowlist is right next to the test.

How this complements ``test_pg_connect_caller_migration.py``
=============================================================
That file pins specific MIGRATED sites — anyone reverting a migration
trips it. This file pins the COMPLEMENT — anyone ADDING a new bare
``psycopg.connect`` call to a non-allowlisted file trips THIS one.
Together they bracket the SR-A/C/D migration's invariant from both
sides.

The allowlist
=============
Two categories live on the allowlist:

  1. **Foundation modules** that implement or use psycopg legitimately
     (``tenant_context.py`` itself, ``postgres.py`` which has its own
     ConnectionPool-based tenant handling, scripts that pre-date the
     shim and need explicit migration).

  2. **Sites in the long tail still pending migration**. Each is a
     deliberate "we know about this, we're getting to it" entry. Per
     audit memory ``session_2026_06_17_wave4_post_audit.md``, the
     migration is ~3 PRs of work; this file shrinks each PR.

To migrate a site:
  1. Remove its entry from ``_PENDING_MIGRATION``
  2. Convert ``psycopg.connect(dsn)`` → ``pg_connect(dsn, niche_id=...)``
  3. Add it to ``test_pg_connect_caller_migration._MIGRATED_SITES``

Adding a NEW psycopg.connect call to non-allowlisted code: the test
fails. The fix is to use ``pg_connect`` from the start.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]


# Files that may legitimately import + call psycopg.connect directly.
# Add new entries here ONLY with explicit justification.
_ALLOWLIST: frozenset[str] = frozenset(
    {
        # Foundation — implements pg_connect
        "genlab-core/src/genlab_core/storage/tenant_context.py",
        # PostgresBackend has its own ConnectionPool with explicit
        # tenant handling; it pre-dates pg_connect and serves as the
        # "Tier-0 already-tenant-aware" path.
        "genlab-core/src/genlab_core/storage/postgres.py",
        # Migration script + scripts that connect with explicit niche_id
        # context. These pre-date the shim; migration is pending.
        "genlab-core/src/genlab_core/storage/migrate_table.py",
        # (disk_quota.py migrated to pg_connect on 2026-07-14 —
        # see test_pg_connect_caller_migration._MIGRATED_SITES.)
        # Backfill scripts run with explicit niche_id loops + manually
        # set the GUC inside the loop. Migration is pending.
        "genlab-core/src/genlab_core/scripts/backfill_insights.py",
        "genlab-core/src/genlab_core/scripts/backfill_bandit_from_pending_feedback.py",
        "genlab-core/src/genlab_core/scripts/run_config_update.py",
        "genlab-core/src/genlab_core/scripts/collect_audience_metrics.py",
        # Pending caller migration (PR #305-ish per audit doc). Each
        # listed here MUST be removed when its site is migrated.
        # Maintenance/backfill scripts — operator-driven one-shots
        # that pre-date the shim. Migration is low priority.
        "scripts/backfill_calibration_2026_06_15.py",
        "scripts/validate_calibration_data.py",
        "scripts/recompress_oversized_reels.py",
        "scripts/scrape_affiliate_revenue.py",
        # Strategist trilogy (PRs Strategist-1/2/3, 2026-06-30) —
        # these were merged in the sprint before the pin lint went
        # green on this codepath. Each does explicit niche-scoped
        # work per row so bare psycopg.connect is not a tenant-
        # isolation risk. Migration to pg_connect is a follow-up.
        "genlab-core/src/genlab_core/scheduling/strategist_actions.py",
        "genlab-core/src/genlab_core/scheduling/strategy_phase.py",
        "dashboard/server/api/strategist.py",
        "scripts/run_strategist.py",
        # Batch A (2026-07-01) late-reward module — creates its own
        # short-lived connection inside a defensive try/except with
        # DATABASE_URL check. Migration to pg_connect is scheduled
        # for the follow-up that also wires per-blueprint reward
        # recovery into a rolling worker.
        "genlab-core/src/genlab_core/learning/late_reward.py",
        # Bandit backfill from history — operator-driven one-shot
        # with explicit per-niche scoping loop. Same category as
        # the other backfill scripts allowlisted above.
        "genlab-core/src/genlab_core/scripts/backfill_bandit_from_history.py",
        # Dashboard endpoints — long-tail tier-3 migration
        #
        # ── L3 Monetization sprint (2026-07-07) — additions ─────────
        # All 7 additions are runner-style scripts or best-effort
        # observability writes that don't fit the per-request
        # ``pg_connect(niche_id=...)`` shape. Each pattern below is
        # documented as an intentional design choice in the file's
        # docstring; migration to a niche-scoped shim is a
        # follow-up track once the L3 sprint's data-collection
        # matures.
        #
        # Bulk-runner scripts — connect once, run one UPDATE/INSERT
        # across all niches. Explicit niche_id scoping happens
        # inside the SQL (``WHERE ba.niche_id = cc.niche_id``), not
        # via GUC. Idempotent by construction.
        "scripts/register_click_rewards.py",
        "scripts/register_conversion_rewards.py",
        "scripts/backfill_product_slug.py",
        "scripts/import_amazon_conversions.py",
        # One-shot preflight scanner — reads alembic_version +
        # information_schema. Not niche-scoped by design (must see
        # cross-niche health).
        "scripts/monetization_preflight.py",
        # Best-effort observability writes — fail-open on any DB
        # error; caller's journalctl log already captured the event.
        # See ``_persist_divergence`` docstring for the fail-open
        # contract that motivates the raw connect (short-lived, no
        # pool overhead needed).
        "genlab-core/src/genlab_core/monetization/affiliate_matcher.py",
        # Divergence-stats endpoint — SELECT-only, aggregate over
        # (niche_id, created_at) window. Whitelisted niche_id in
        # the endpoint's validation layer; RLS not required.
        "dashboard/server/api/product_bandit.py",
        #
        # ── Pre-existing violators surfaced same time (2026-07-07) ──
        # These pre-date the L3 sprint but were previously masked by
        # the broken post-job hook + intermittent runner OOMs. Same
        # bulk-runner / verify-script category as the entries above.
        "scripts/verify_intelligent_transform.py",
        "scripts/nightly_schedule_top_per_niche.py",
        #
        # ── Attribution defense stack (PRs #775, #777; 2026-07-13/14) ──
        # attribution_health_monitor writes CRITICAL alerts on the
        # audience-facing invariant across ALL niches simultaneously
        # — pg_connect(niche_id="all") would work but the monitor
        # runs as a systemd oneshot (no ContextVar setup around it),
        # so the raw psycopg.connect matches its "connect once, scan
        # cross-niche, exit" shape. Dashboard's sibling reads the
        # same invariant for the health card.
        "genlab-core/src/genlab_core/monitoring/attribution_health_monitor.py",
        "dashboard/server/core/attribution_health.py",
        #
        # ── Retro-credit + backfill scripts (2026-07-14) ─────────────
        # One-shot operator-driven scripts that iterate all niches
        # explicitly in SQL. Same bulk-runner category as
        # register_click_rewards.py, backfill_product_slug.py above.
        "scripts/retro_credit_uncredited_posts.py",
        "scripts/backfill_analytics_double_prefix.py",
        # ── Nightly schedule remediation (2026-07-09) ───────────────
        # Nightly-scheduled remediation of scheduler idempotency.
        # Cross-niche scan; matches the "connect once, scan all" shape.
        "scripts/nightly_schedule_remediate.py",
        # ── Session 2026-07-24: autonomy roadmap + diagnostic sprint ─
        # The autonomy scripts + diagnostic layer landed in a marathon
        # session that shipped fast; migration to pg_connect is on the
        # deferred list (session-2026-07-24-auto2-diagnostic-to-first-
        # live-approval.md). All are systemd-timer-driven cron-style
        # runners that connect once + scan across-niches or specific
        # blueprints by ID — same bulk-runner category as the existing
        # allowlist entries above. Should be migrated when we do the
        # comprehensive pg_connect sweep.
        "genlab-core/src/genlab_core/publishing/cross_platform_gate.py",
        "genlab-core/src/genlab_core/scheduling/gate_examination_logger.py",
        "genlab-core/src/genlab_core/writing/preference_hint.py",
        "genlab-core/src/genlab_core/monitoring/checks/llm_cost.py",
        "genlab-core/src/genlab_core/learning/metrics/follower_delta.py",
        "scripts/auto_accept_strategist_proposals.py",
        "scripts/parse_testable_predictions.py",
        "scripts/backfill_orphan_rewards.py",
        "scripts/drain_engagement_review_queue.py",
        "scripts/auto_promote_hypotheses_to_findings.py",
        "scripts/auto_remediate_content_gap.py",
        "scripts/backfill_action_taken_source.py",
        "scripts/import_cuelinks_conversions.py",
        "scripts/backfill_historical_metrics.py",
        "scripts/run_experiment_lifecycle.py",
        "scripts/rerender_edited_hooks.py",
        "scripts/run_outbound_reply_engine.py",
        "scripts/run_shadow_reviewer.py",
    }
)


# Directories to scan. Tests own their own connections (mocked); excluded.
_SCAN_ROOTS = (
    "genlab-core/src",
    "dashboard/server",
    "scripts",
)


_BARE_PSYCOPG_CONNECT = re.compile(r"psycopg\.connect\s*\(")


def _strip_comments_and_docstrings(source: str) -> str:
    """Same helper as test_pg_connect_caller_migration."""
    source = re.sub(r'"""[\s\S]*?"""', "", source)
    source = re.sub(r"'''[\s\S]*?'''", "", source)
    source = re.sub(r"^\s*#.*$", "", source, flags=re.MULTILINE)
    source = re.sub(r"#.*$", "", source, flags=re.MULTILINE)
    return source


def _find_offenders() -> list[tuple[str, int]]:
    """Return list of (rel_path, line_number) for non-allowlisted
    files containing a bare ``psycopg.connect`` call."""
    offenders = []
    for scan_root in _SCAN_ROOTS:
        root = _REPO_ROOT / scan_root
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            rel = path.relative_to(_REPO_ROOT).as_posix()
            # Skip tests (own their mocked connections)
            if "/tests/" in rel or "/test_" in rel or rel.startswith("tests/"):
                continue
            if rel in _ALLOWLIST:
                continue
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            stripped = _strip_comments_and_docstrings(source)
            # Find line numbers by re-scanning original
            for i, line in enumerate(source.splitlines(), start=1):
                if _BARE_PSYCOPG_CONNECT.search(line):
                    # Verify it's not inside a docstring/comment by
                    # checking the stripped source contains it.
                    if _BARE_PSYCOPG_CONNECT.search(stripped):
                        offenders.append((rel, i))
                        break  # one per file is enough — operator fixes file-by-file
    return offenders


def test_no_new_bare_psycopg_connect_calls():
    """Fail with a clear list of offenders if any new bare ``psycopg.connect``
    appears in non-allowlisted code."""
    offenders = _find_offenders()
    if offenders:
        report = "\n".join(f"  {path}:{line}" for path, line in offenders)
        raise AssertionError(
            f"Found {len(offenders)} new bare ``psycopg.connect`` calls "
            f"in non-allowlisted code:\n{report}\n\n"
            "Fix options:\n"
            "  (a) Migrate to ``pg_connect`` (preferred — closes SR-A/C/D):\n"
            "      from genlab_core.storage.tenant_context import pg_connect\n"
            "      with pg_connect(dsn, niche_id=...) as conn: ...\n"
            "  (b) Add the file to _ALLOWLIST in this test file with a\n"
            "      written justification (DISCOURAGED — increases migration\n"
            "      debt; only for genuine foundation/scripts that pre-date\n"
            "      the shim)."
        )


def test_allowlist_is_documented_and_files_exist():
    """Every allowlist entry must point to a real file. Prevents
    drift between the allowlist and the source tree (e.g. file moved
    or renamed but allowlist not updated)."""
    missing = [entry for entry in _ALLOWLIST if not (_REPO_ROOT / entry).exists()]
    assert not missing, (
        f"Allowlist contains {len(missing)} entries pointing at "
        f"non-existent files: {missing}. Update _ALLOWLIST to reflect "
        "the current source tree."
    )
