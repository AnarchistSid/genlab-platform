"""Microsoft Lists client for content workflow management.

Provides typed CRUD operations for all shared tables:
  Stories, Blueprints, Templates, Assets, Sources,
  Publishing_Analytics, Analytics, AB_Tests.

Both BlackboxBrief and CriticalRush use the same SharePoint Lists,
differentiating content via the niche_id column.

Requires AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET,
and SHAREPOINT_SITE_ID in .env.

Config path resolution (in order):
  1. config_path parameter to __init__
  2. BACKLOG_CONFIG_PATH env var
  3. Auto-detect: walk up from caller's working directory
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from genlab_core.http.ab_test_store import ABTestStore
from genlab_core.http.analytics_store import AnalyticsStore
from genlab_core.http.asset_store import AssetStore
from genlab_core.http.blueprint_store import BlueprintStore
from genlab_core.http.circuit_breaker import SHAREPOINT_CB
from genlab_core.http.engagement_store import EngagementStore
from genlab_core.http.graph_proxy import GraphTableProxy, _esc
from genlab_core.http.source_store import SourceStore
from genlab_core.http.story_store import StoryStore
from genlab_core.http.template_store import TemplateStore

logger = logging.getLogger(__name__)

# Canonical blueprint status progression — used by ScheduleGuardedProxy and
# BacklogClient for demotion detection.  Keep this list in pipeline order.
#
# R-81 phantom-`SCHEDULED` prune (2026-06-12): ``SCHEDULED`` removed from
# this list. No live code writes ``status="SCHEDULED"`` — being "scheduled"
# is encoded as a non-null ``scheduled_for`` timestamp on a VISUAL_READY
# blueprint, not as a discrete status. The dashboard's virtual-SCHEDULED
# query filter (``blueprints.py:603``) translates SCHEDULED → VISUAL_READY
# + ``scheduled_for IS NOT NULL`` for the read side; the write side has
# no caller. R-80's explicit-transition model (``_is_forbidden_for_scheduled``)
# now enforces the schedule guard; STATUS_ORDER + ``_is_demotion`` are
# the legacy linear-rank approach kept for back-compat on writes that
# don't involve SCHEDULED. The forward-looking pin in ``tests/test_http.py``
# asserts SCHEDULED stays out of this list.
STATUS_ORDER: list[str] = [
    "INTAKE",
    "VALIDATED",
    "INTEL_READY",
    "RESEARCHED",
    "DRAFTED",
    "VISUAL_READY",
    "PUBLISHED",
    "ANALYZED",
    "ARCHIVED",
]

# R-80: the schedule guard can't use a linear STATUS_ORDER — operational states
# (PUBLISHING/PUBLISH_FAILED) aren't rankable, and some "forward" moves are still
# destructive (VISUAL_READY→ARCHIVED cleanup). An explicit model instead:
# a scheduled post is "pending" (its slot is live) until it publishes; moving a
# still-pending scheduled post into a "destructive" status discards its queued
# work and is blocked. Publish progress (→PUBLISHING/→PUBLISHED) and recovery
# (PUBLISHING→VISUAL_READY, PUBLISH_FAILED→VISUAL_READY) are NOT destructive, so
# they're allowed; post-publish lifecycle (PUBLISHED→ANALYZED→ARCHIVED) is
# allowed because the source is no longer pending.
# R-81 (2026-06-12): ``SCHEDULED`` removed alongside the STATUS_ORDER
# prune. Production blueprints carry ``status=VISUAL_READY`` +
# ``scheduled_for IS NOT NULL`` to represent "scheduled" — no row
# carries ``status=SCHEDULED``. Defensive coverage for the (verified
# empty) historical SCHEDULED-row population is therefore unnecessary;
# the prune simplifies the vocabulary the schedule guard reasons over.
_PENDING_STATUSES: frozenset[str] = frozenset({"DRAFTED", "VISUAL_READY", "PUBLISHING"})
_DESTRUCTIVE_STATUSES: frozenset[str] = frozenset(
    {"ARCHIVED", "INTAKE", "VALIDATED", "INTEL_READY", "RESEARCHED", "DRAFTED"}
)

# Shared error tuple for backlog operations
try:
    from kiota_abstractions.api_error import APIError as GraphAPIError
    from requests.exceptions import RequestException

    BACKLOG_OP_ERRORS = (
        RequestException,
        TimeoutError,
        OSError,
        RuntimeError,
        ValueError,
        KeyError,
        TypeError,
        GraphAPIError,
    )
except ImportError:
    BACKLOG_OP_ERRORS = (
        TimeoutError,
        OSError,
        RuntimeError,
        ValueError,
        KeyError,
        TypeError,
    )


# ── Schedule Guard ───────────────────────────────────────────────────


class ScheduledPostProtectionError(Exception):
    """Raised when an operation would damage a scheduled post."""


_schedule_guard_tls = threading.local()


@contextmanager
def allow_scheduled_updates():
    """Context manager to temporarily allow destructive ops on scheduled posts."""
    _schedule_guard_tls.active = True
    try:
        yield
    finally:
        _schedule_guard_tls.active = False


def _schedule_guard_forced() -> bool:
    return getattr(_schedule_guard_tls, "active", False)


class ScheduleGuardedProxy:
    """Transparent wrapper around GraphTableProxy for the Blueprints table.

    Blocks status demotions, deletions, and field clearing on scheduled posts.
    """

    _STATUS_ORDER = STATUS_ORDER

    _GUARDED_FIELDS = frozenset({"status", "visual_paths", "scheduled_for"})

    def __init__(self, proxy: GraphTableProxy):
        object.__setattr__(self, "_proxy", proxy)

    def __getattr__(self, name: str):
        return getattr(self._proxy, name)

    def update(
        self,
        record_id: str,
        fields: dict[str, Any],
        typecast: bool = False,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        if not force and not _schedule_guard_forced():
            self._guard_update(record_id, fields)
        return self._proxy.update(record_id, fields, typecast)

    def delete(self, record_id: str, *, force: bool = False) -> None:
        if not force and not _schedule_guard_forced():
            self._guard_delete(record_id)
        self._proxy.delete(record_id)

    def batch_update(
        self,
        records: list[dict[str, Any]],
        *,
        force: bool = False,
        **kwargs,
    ) -> list[dict[str, Any]]:
        if not force and not _schedule_guard_forced():
            for rec in records:
                rec_id = rec.get("id", "")
                rec_fields = rec.get("fields", {})
                if rec_id:
                    self._guard_update(str(rec_id), rec_fields)
        return self._proxy.batch_update(records, **kwargs)

    def _guard_update(self, record_id: str, fields: dict[str, Any]) -> None:
        touched = self._GUARDED_FIELDS & set(fields.keys())
        if not touched:
            return

        # Fail-open on a fetch error: a guard that can't read the current row
        # must not block a legitimate publish write (it would dark the channel).
        try:
            record = self._proxy.get(str(record_id))
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "[schedule-guard] could not fetch %s — allowing update: %s", record_id, exc
            )
            return
        if not record:
            return
        rec_fields = record.get("fields", record)
        scheduled_for = rec_fields.get("scheduled_for")
        if not scheduled_for:
            return

        # 2026-06-14: operator-intent reject (status→ARCHIVED + clearing
        # scheduled_for in the SAME update) is an explicit atomic
        # "unschedule and archive" action. Distinct from partial-write
        # cases (only one of the two) which remain blocked. Without this
        # bypass, every reject from the dashboard fails because the
        # pipeline pre-sets ``scheduled_for`` at PushToBacklog time on
        # EVERY new VISUAL_READY blueprint (PR #191 pre-set hint
        # pattern) — so VISUAL_READY blueprints all look "scheduled"
        # to the guard even before the operator commits to a slot.
        archiving = fields.get("status") == "ARCHIVED"
        clearing_schedule = "scheduled_for" in fields and self._is_empty(fields["scheduled_for"])
        if archiving and clearing_schedule:
            logger.info(
                "[schedule-guard] operator-intent reject (status→ARCHIVED + "
                "scheduled_for cleared in same update) for rec=%s; allowing",
                record_id,
            )
            return

        new_status = fields.get("status")
        if new_status:
            old_status = rec_fields.get("status", "")
            if self._is_forbidden_for_scheduled(old_status, new_status):
                raise ScheduledPostProtectionError(
                    f"Cannot move scheduled blueprint rec={record_id} "
                    f"from {old_status} → {new_status} — would discard its queued "
                    f"slot (scheduled_for={scheduled_for}). To reject + unschedule "
                    f"atomically, set scheduled_for=None in the same update."
                )

        if "visual_paths" in fields and self._is_empty(fields["visual_paths"]):
            raise ScheduledPostProtectionError(
                f"Cannot clear visual_paths on scheduled blueprint rec={record_id}."
            )

        if "scheduled_for" in fields and self._is_empty(fields["scheduled_for"]):
            raise ScheduledPostProtectionError(
                f"Cannot clear scheduled_for on blueprint rec={record_id}."
            )

    def _guard_delete(self, record_id: str) -> None:
        record = self._proxy.get(str(record_id))
        rec_fields = record.get("fields", record)
        if rec_fields.get("scheduled_for"):
            raise ScheduledPostProtectionError(
                f"Cannot delete scheduled blueprint rec={record_id}."
            )

    @classmethod
    def _is_demotion(cls, old_status: str, new_status: str) -> bool:
        try:
            return cls._STATUS_ORDER.index(new_status) < cls._STATUS_ORDER.index(old_status)
        except ValueError:
            return False

    @staticmethod
    def _is_forbidden_for_scheduled(old_status: str, new_status: str) -> bool:
        """Whether moving a SCHEDULED post old→new discards its queued slot (R-80).

        Explicit, not a linear rank: a still-pending scheduled post must not be
        archived or regressed (that loses its slot), but publish progress and
        recovery transitions are fine. Once published, the source is no longer
        pending, so normal post-publish lifecycle (→ANALYZED/→ARCHIVED) is allowed.
        """
        if old_status == new_status:
            return False
        return old_status in _PENDING_STATUSES and new_status in _DESTRUCTIVE_STATUSES

    @staticmethod
    def _is_empty(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str) and value.strip() in ("", "[]", "null"):
            return True
        return False


# ── Niche filter helper ──────────────────────────────────────────────


# SR-D (PR #520, 2026-06-24): observability + opt-in strict mode for
# the niche-filter injection. Documented gap from SYSTEM-RESEARCH §9
# (SR-D): when ``niche_id`` is None, the filter silently returns the
# unfiltered formula → multi-tenant queries return cross-tenant rows.
# Single-tenant phase: harmless. Tenant #2 onboarding: critical leak.
#
# Three-step migration (this PR ships steps 1 + 2):
#
#   1. **WARNING log** on every None call. Operators audit logs to
#      identify legacy call sites that need the kwarg.
#   2. **Counter** to track frequency (process-local; persists across
#      requests in a single worker). Lets operators measure migration
#      progress before flipping the strict-mode env var.
#   3. **Opt-in strict mode** via ``GENLAB_REQUIRE_NICHE_FILTER=1``
#      env var. When set, raises ValueError instead of returning the
#      unfiltered formula. Default off — preserves legacy behaviour
#      until the call-site audit lands. Once 0 None-calls measured for
#      ≥1 week, flip on in production.
#
# After step 3 + green prod for tenant #1, removing this opt-in entirely
# (always-strict) closes SR-D permanently.
_SR_D_NONE_CALL_COUNT: int = 0


def _sr_d_none_call_count() -> int:
    """Test helper: read the SR-D fallthrough counter. Pinned by the
    PR #520 regression tests."""
    return _SR_D_NONE_CALL_COUNT


def _sr_d_reset_counter_for_tests() -> None:
    """Test helper: reset between test cases."""
    global _SR_D_NONE_CALL_COUNT
    _SR_D_NONE_CALL_COUNT = 0


def _inject_niche_filter(
    formula: str | None,
    niche_id: str | None,
    niche_field: str = "niche_id",
) -> str | None:
    """Append niche_id filter to existing formula.

    Uses the legacy formula syntax that GraphTableProxy.all() understands:
      {niche_id}='gaming'

    If formula already exists, wraps in AND():
      AND({status}='INTAKE', {niche_id}='gaming')

    When ``niche_id`` is None or empty:
      * Default (legacy): returns the unfiltered formula + emits a
        WARNING log line. Increments ``_SR_D_NONE_CALL_COUNT`` for
        operator visibility.
      * Strict (``GENLAB_REQUIRE_NICHE_FILTER=1``): raises ValueError.

    The strict-mode env var is the SR-D mechanism — see module-level
    comment above for the 3-step migration plan.
    """
    if not niche_id:
        global _SR_D_NONE_CALL_COUNT
        _SR_D_NONE_CALL_COUNT += 1
        if os.environ.get("GENLAB_REQUIRE_NICHE_FILTER", "").strip().lower() in (
            "1",
            "true",
            "yes",
        ):
            raise ValueError(
                "SR-D: _inject_niche_filter called without niche_id when "
                "GENLAB_REQUIRE_NICHE_FILTER is enabled. Cross-tenant leak "
                "blocked. Caller must pass niche_id= explicitly. "
                "See SYSTEM-RESEARCH.md §9 SR-D + PR #520 docstring."
            )
        # Log at WARNING so the call site shows up in operator audits.
        # stacklevel=2 surfaces the caller's location in the formatter,
        # making the audit grep trivial.
        logger.warning(
            "[SR-D] _inject_niche_filter called without niche_id "
            "(formula=%r) — cross-tenant leak risk. Caller must pass "
            "niche_id=. Set GENLAB_REQUIRE_NICHE_FILTER=1 to enforce.",
            formula[:100] if isinstance(formula, str) else formula,
            stacklevel=2,
        )
        return formula
    niche_clause = f"{{{niche_field}}}='{_esc(niche_id)}'"
    if not formula:
        return niche_clause
    return f"AND({formula}, {niche_clause})"


# ── BacklogClient ────────────────────────────────────────────────────


class BacklogClient:
    """Microsoft Lists backend for content workflow management.

    Args:
        config_path: Path to lists_config.yaml. If None, uses
            BACKLOG_CONFIG_PATH env var or auto-detects from CWD.
    """

    DOMAIN_SOURCE_MAP = {
        "openai.com": "OpenAI",
        "anthropic.com": "Anthropic",
        "blog.google": "Google",
        "techcrunch.com": "TechCrunch",
        "theverge.com": "The Verge",
        "wired.com": "Wired",
        "arstechnica.com": "Ars Technica",
        "news.ycombinator.com": "Hacker News",
        "marktechpost.com": "MarkTechPost",
        "the-decoder.com": "The Decoder",
        "venturebeat.com": "VentureBeat",
    }

    ASSET_SOURCE_TYPE_ALLOWED = {
        "og_image",
        "og_video",
        "schema_org",
        "hero_image",
        "video_embed",
        "stock_search",
        "generated",
    }

    _STATUS_ORDER = STATUS_ORDER

    def __init__(
        self,
        config_path: Path | str | None = None,
        *,
        tenant_id: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        site_id: str | None = None,
    ):
        import yaml

        _use_postgres = os.getenv("GENLAB_USE_POSTGRES", "").lower() == "true"
        _dsn = os.getenv("DATABASE_URL", "")

        # ── Postgres-only path: skip Graph SDK entirely ──────────
        if _use_postgres and _dsn:
            from genlab_core.storage.postgres import PostgresBackend, PostgresTableProxy

            self._graph = None
            self._site_id = ""
            _pg = PostgresBackend(dsn=_dsn)
            self._pg = _pg

            ALL_TABLES = [
                "Stories",
                "Blueprints",
                "Templates",
                "Assets",
                "Sources",
                "Publishing_Analytics",
                "Analytics",
                "AB_Tests",
                "Audience_Snapshots",
                "PendingEngagement",
                "PendingFeedback",
                "BanditArms",
                "Content_Memory",
                "MonetisationProgress",
            ]

            # Map CamelCase list names to actual PostgreSQL table names.
            # Lists like "PendingFeedback" become "pendingfeedback" via .lower()
            # but the actual DB table is "pending_feedback" (snake_case).
            _SQL_TABLE_MAP = {
                "pendingfeedback": "pending_feedback",
                "pendingengagement": "pending_engagement",
                "banditarms": "bandit_arms",
            }

            for table in ALL_TABLES:
                attr = table.lower()
                # Normalize attribute names to match existing API
                attr_map = {
                    "publishing_analytics": "publishing_analytics",
                    "ab_tests": "ab_tests",
                    "audience_snapshots": "audience_snapshots",
                    "pendingengagement": "pending_engagement",
                    "pendingfeedback": "pending_feedback",
                    "banditarms": "bandit_arms",
                    "content_memory": "content_memory",
                    "monetisationprogress": "monetisation_progress",
                }
                attr = attr_map.get(attr, attr)
                sql_table = _SQL_TABLE_MAP.get(table.lower(), table.lower())
                setattr(self, attr, PostgresTableProxy(_pg, sql_table))

            # R-41: "scheduled posts are sacred" was enforced only on the legacy
            # SharePoint path; it went DORMANT when Postgres became primary
            # (Sprint 65) because this branch returns before the wrap below. Wrap
            # the Postgres Blueprints proxy so the guard runs in production.
            self.blueprints = ScheduleGuardedProxy(self.blueprints)

            self._sp_proxies = {
                t: getattr(self, attr_map.get(t.lower(), t.lower()), None) for t in ALL_TABLES
            }
            self._backend_cache = {"postgres": _pg}

            # CRITICAL: build the Tier 2 stores on this path too. Skipping
            # this was the root cause of the 22-day "0 published / $2 burned"
            # outage — push_to_backlog's delegators (find_story_by_story_id,
            # create_story, etc.) all proxy to self._stories.* which was
            # AttributeError until this PR. See _construct_tier2_stores
            # docstring.
            self._construct_tier2_stores()

            logger.info("[BacklogClient] Postgres-only mode — no SharePoint connection")
            return

        # ── SharePoint path (legacy / fallback) ──────────────────
        from azure.identity import ClientSecretCredential
        from msgraph import GraphServiceClient

        from genlab_core.settings import settings

        tenant = (tenant_id or settings.azure_tenant_id or "").strip()
        client_id_val = (client_id or settings.azure_client_id or "").strip()
        secret = (client_secret or settings.azure_client_secret or "").strip()
        self._site_id = (site_id or settings.sharepoint_site_id or "").strip()

        if not all([tenant, client_id_val, secret, self._site_id]):
            raise ValueError(
                "AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, "
                "and SHAREPOINT_SITE_ID must be set in .env "
                "(or set GENLAB_USE_POSTGRES=true + DATABASE_URL to skip SharePoint)"
            )

        cred = ClientSecretCredential(tenant, client_id_val, secret)
        self._graph = GraphServiceClient(cred, scopes=["https://graph.microsoft.com/.default"])

        # Resolve config path
        if config_path is not None:
            config_path = Path(config_path)
        else:
            env_path = os.getenv("BACKLOG_CONFIG_PATH", "")
            if env_path:
                config_path = Path(env_path)
            else:
                for parent in [Path.cwd()] + list(Path.cwd().parents):
                    candidate = parent / "config" / "lists_config.yaml"
                    if candidate.exists():
                        config_path = candidate
                        break

        if config_path is None or not config_path.exists():
            raise FileNotFoundError(
                f"List config not found (tried {config_path}). "
                "Set BACKLOG_CONFIG_PATH env var or pass config_path to BacklogClient()."
            )

        with open(config_path) as f:
            config = yaml.safe_load(f) or {}

        def _proxy(name: str) -> GraphTableProxy:
            cfg = config.get(name, {})
            list_id = cfg.get("list_id", "")
            if not list_id:
                raise ValueError(f"List ID for '{name}' not found in {config_path}")
            return GraphTableProxy(self._graph, self._site_id, list_id, name)

        self.stories = _proxy("Stories")
        self.blueprints = ScheduleGuardedProxy(_proxy("Blueprints"))
        self.templates = _proxy("Templates")
        self.assets = _proxy("Assets")
        self.sources = _proxy("Sources")
        self.publishing_analytics = _proxy("Publishing_Analytics")
        self.analytics = _proxy("Analytics")

        for name, attr_name in [
            ("AB_Tests", "ab_tests"),
            ("Audience_Snapshots", "audience_snapshots"),
            ("PendingEngagement", "pending_engagement"),
            ("PendingFeedback", "pending_feedback"),
            ("BanditArms", "bandit_arms"),
            ("Content_Memory", "content_memory"),
            ("MonetisationProgress", "monetisation_progress"),
        ]:
            try:
                setattr(self, attr_name, _proxy(name))
            except (ValueError, KeyError):
                setattr(self, attr_name, None)

        self._sp_proxies = {
            "Stories": self.stories,
            "Blueprints": self.blueprints,
            "Templates": self.templates,
            "Assets": self.assets,
            "Sources": self.sources,
            "Publishing_Analytics": self.publishing_analytics,
            "Analytics": self.analytics,
        }
        for name, attr in [
            ("AB_Tests", self.ab_tests),
            ("Audience_Snapshots", self.audience_snapshots),
            ("PendingEngagement", self.pending_engagement),
            ("PendingFeedback", self.pending_feedback),
            ("BanditArms", self.bandit_arms),
            ("Content_Memory", self.content_memory),
            ("MonetisationProgress", self.monetisation_progress),
        ]:
            if attr is not None:
                self._sp_proxies[name] = attr

        self._backend_cache: dict[str, Any] = {}

        # Per-client backend cache — avoids module-level singleton issues
        # when multiple BacklogClient instances exist (e.g. in tests).
        self._backend_cache: dict[str, Any] = {}

        self._construct_tier2_stores()

    def _construct_tier2_stores(self) -> None:
        """Build the Tier 2 store objects (StoryStore, BlueprintStore, …).

        Called on BOTH the SharePoint and Postgres init paths so the
        host-class delegators (``find_story_by_story_id``, ``create_story``,
        ``batch_create_stories``, ``update_story_status``, etc.) work
        regardless of backend.

        Historical bug (fix #2 of the autonomy roadmap): the Postgres-
        primary path (Sprint 65) early-returned BEFORE this block,
        leaving ``self._stories`` / ``self._blueprints`` etc. unset.
        Every delegator that hit ``self._stories.*`` raised
        ``AttributeError`` which a try/except in ``push_to_backlog``
        swallowed — pipelines ran fine through render, then silently
        created zero blueprints (the 22-day "0 published / $2 burned"
        outage of 2026-05-21 → 2026-06-12 traced back through this
        single gap).

        Order matters: Stories must be built before Assets + Blueprints
        because both take ``find_story`` as a callback. Templates must
        be built before Blueprints because it takes ``find_template``.

        Uses ``getattr(self, "pending_engagement", None)`` /
        ``getattr(self, "ab_tests", None)`` so the Postgres path (which
        sets them unconditionally) and the SharePoint path (which sets
        them to None when their lists aren't configured) both work.
        """
        # Analytics surface lives in its own module (Tier 2, audit S-2).
        # BacklogClient retains the public method names + signatures as
        # thin delegators below; the actual upsert logic + virality math
        # is in :class:`AnalyticsStore`.
        self._analytics = AnalyticsStore(sp_call=self._sp_call, backend=self._backend)

        # PendingEngagement surface (Tier 2, audit S-2 slice 2.2).
        # Single-proxy store — captures the already-built
        # ``self.pending_engagement`` reference (may be None if
        # the underlying SP list isn't configured; the store
        # early-returns + WARNs in every method on that branch).
        self._engagement = EngagementStore(getattr(self, "pending_engagement", None))

        # AB_Tests surface (Tier 2, audit S-2 slice 2.3).
        # Multi-table backend pattern (like AnalyticsStore) but
        # also captures the ``self.ab_tests`` proxy ref for the
        # "is configured" truthy gate.
        self._ab_tests = ABTestStore(
            ab_tests=getattr(self, "ab_tests", None),
            backend=self._backend,
        )

        # Sources + Templates surfaces (Tier 2, audit S-2 slice 2.4).
        # Simplest pattern in the family: backend-only — both lists
        # are unconditionally present in ``lists_config.yaml``, so
        # there's no truthy 'configured' gate to mirror.
        self._sources = SourceStore(backend=self._backend)
        self._templates = TemplateStore(backend=self._backend)

        # Stories surface (Tier 2, audit S-2 slice 2.5a).
        # Takes sp_call (circuit-breaker wrap), backend (live
        # lookup), and resolve_source (kept centralised on the
        # host class because it depends on DOMAIN_SOURCE_MAP).
        self._stories = StoryStore(
            sp_call=self._sp_call,
            backend=self._backend,
            resolve_source=self._resolve_source,
        )

        # Assets surface (Tier 2, audit S-2 slice 2.5b).
        # Most-callable-rich constructor in the family: needs
        # find_story (from StoryStore, must be built first),
        # find_blueprint (still on BacklogClient pending the
        # Blueprints extraction), and normalize_asset_source_type
        # (kept on host class because of ASSET_SOURCE_TYPE_ALLOWED).
        self._assets = AssetStore(
            backend=self._backend,
            find_story=self._stories.find_story_by_story_id,
            find_blueprint=self.find_blueprint_by_candidate_id,
            normalize_asset_source_type=self._normalize_asset_source_type,
        )

        # Blueprints surface — the heaviest extracted store.
        # Five injected callables: sp_call (circuit-breaker),
        # backend (live lookup), find_story + find_template (from
        # their respective stores), and assert_not_scheduled
        # (host-class helper that uses _is_demotion + _STATUS_ORDER).
        self._blueprints = BlueprintStore(
            sp_call=self._sp_call,
            backend=self._backend,
            find_story=self._stories.find_story_by_story_id,
            find_template=self._templates.find_template_by_template_id,
            assert_not_scheduled=self.assert_not_scheduled,
        )

    def close(self) -> None:
        """Close the underlying PostgreSQL connection pool."""
        pg = getattr(self, "_pg", None)
        if pg and hasattr(pg, "close"):
            pg.close()

    # ── Circuit breaker helper ───────────────────────────────────────

    @staticmethod
    def _sp_call(fn, *args, **kwargs):
        """Execute a SharePoint/Graph API call through the circuit breaker.

        Falls back to direct call if the circuit is open and we have
        no alternative — the caller will see the CircuitOpenError.
        """
        return SHAREPOINT_CB.call(fn, *args, **kwargs)

    # ── Storage backend helper ──────────────────────────────────────

    def _backend(self, table: str):
        """Return the StorageBackend for the given table.

        Routes to SharePoint or PostgreSQL based on config/storage_backends.yaml.
        Uses a per-client cache so multiple BacklogClient instances (e.g. in
        tests) each get their own backends tied to their own proxies.
        """
        from genlab_core.storage.factory import _load_config

        cache = getattr(self, "_backend_cache", None)
        if cache is None:
            cache = {}
            self._backend_cache = cache

        config = _load_config()
        engine = config.get(table, "sharepoint").lower()
        # GENLAB_USE_POSTGRES=true overrides YAML config
        if os.getenv("GENLAB_USE_POSTGRES", "").lower() == "true":
            engine = "postgres"

        if engine not in cache:
            if engine == "postgres":
                dsn = os.getenv("DATABASE_URL", "")
                if dsn:
                    from genlab_core.storage.postgres import PostgresBackend

                    cache[engine] = PostgresBackend(dsn=dsn)
                else:
                    engine = "sharepoint"  # fallback

            if engine == "sharepoint" and engine not in cache:
                from genlab_core.storage.sharepoint import SharePointBackend

                cache[engine] = SharePointBackend(self._sp_proxies)

        return cache[engine]

    # ── Private helpers ──────────────────────────────────────────────

    def _resolve_source(self, story: dict) -> str:
        if story.get("source") and story["source"] != "Other":
            return story["source"]
        domain = story.get("domain", "")
        for key, name in self.DOMAIN_SOURCE_MAP.items():
            if key in domain:
                return name
        return "Other"

    def _normalize_asset_source_type(self, raw_source: str | None, asset_type: str) -> str:
        raw = (raw_source or "").strip().lower()
        atype = (asset_type or "").strip().lower()

        if raw in self.ASSET_SOURCE_TYPE_ALLOWED:
            return raw
        if raw in {"text_url", "url_text", "url", "direct_url"}:
            return "video_embed" if atype == "video" else "hero_image"
        if raw in {"image", "thumbnail", "img_tag", "html_img", "scraped_image"}:
            return "hero_image"
        if raw in {"video", "reddit", "youtube", "tiktok", "instagram", "threads", "vimeo"}:
            return "video_embed"
        if raw in {"stock", "unsplash", "pexels"}:
            return "stock_search"
        if raw in {"generator", "generated_media"}:
            return "generated"
        return "video_embed" if atype == "video" else "hero_image"

    def _is_demotion(self, old_status: str, new_status: str) -> bool:
        try:
            return self._STATUS_ORDER.index(new_status) < self._STATUS_ORDER.index(old_status)
        except ValueError:
            return False

    def assert_not_scheduled(self, blueprint: dict, new_status: str) -> None:
        fields = blueprint.get("fields", blueprint)
        old_status = fields.get("status", "")
        if not self._is_demotion(old_status, new_status):
            return
        scheduled_for = fields.get("scheduled_for")
        if scheduled_for:
            raise ValueError(
                f"Refusing to demote blueprint "
                f"{fields.get('candidate_id', blueprint.get('id'))} "
                f"from {old_status} → {new_status}: scheduled for {scheduled_for}."
            )

    # ===== STORIES =====

    def create_story(self, story: dict) -> str:
        """Delegates to :class:`StoryStore.create_story`."""
        return self._stories.create_story(story)

    def find_story_by_story_id(
        self,
        story_id: str,
        *,
        niche_id: str | None = None,
    ) -> dict | None:
        """Delegates to :class:`StoryStore.find_story_by_story_id`."""
        return self._stories.find_story_by_story_id(story_id, niche_id=niche_id)

    def update_story_status(
        self,
        story_id: str,
        status: str,
        *,
        niche_id: str | None = None,
        **kwargs,
    ) -> None:
        """Delegates to :class:`StoryStore.update_story_status`."""
        self._stories.update_story_status(story_id, status, niche_id=niche_id, **kwargs)

    def batch_create_stories(self, stories: list[dict]) -> list[str]:
        """Delegates to :class:`StoryStore.batch_create_stories`."""
        return self._stories.batch_create_stories(stories)

    # ===== BLUEPRINTS =====

    def create_blueprint(
        self,
        blueprint: dict,
        story_record: dict | None = None,
        template_record: dict | None = None,
    ) -> str:
        """Delegates to :class:`BlueprintStore.create_blueprint`."""
        return self._blueprints.create_blueprint(
            blueprint, story_record=story_record, template_record=template_record
        )

    def find_blueprint_by_candidate_id(
        self,
        candidate_id: str,
        *,
        niche_id: str | None = None,
    ) -> dict | None:
        """Delegates to :class:`BlueprintStore.find_blueprint_by_candidate_id`."""
        return self._blueprints.find_blueprint_by_candidate_id(candidate_id, niche_id=niche_id)

    def update_blueprint_status(
        self,
        candidate_id: str,
        status: str,
        *,
        niche_id: str | None = None,
        force: bool = False,
        **kwargs,
    ) -> None:
        """Delegates to :class:`BlueprintStore.update_blueprint_status`."""
        self._blueprints.update_blueprint_status(
            candidate_id, status, niche_id=niche_id, force=force, **kwargs
        )

    def get_blueprints_safe_to_cleanup(
        self,
        status: str,
        *,
        niche_id: str | None = None,
        max_priority: float = 1.0,
    ) -> list[dict]:
        """Delegates to :class:`BlueprintStore.get_blueprints_safe_to_cleanup`."""
        return self._blueprints.get_blueprints_safe_to_cleanup(
            status, niche_id=niche_id, max_priority=max_priority
        )

    def get_blueprints_by_status(
        self,
        status: str,
        *,
        niche_id: str | None = None,
        max_records: int | None = None,
    ) -> list[dict]:
        """Delegates to :class:`BlueprintStore.get_blueprints_by_status`."""
        return self._blueprints.get_blueprints_by_status(
            status, niche_id=niche_id, max_records=max_records
        )

    def batch_create_blueprints(self, blueprints: list[dict]) -> list[str]:
        """Delegates to :class:`BlueprintStore.batch_create_blueprints`."""
        return self._blueprints.batch_create_blueprints(blueprints)

    # ===== TEMPLATES =====

    def create_template(self, template: dict) -> str:
        """Delegates to :class:`TemplateStore.create_template`."""
        return self._templates.create_template(template)

    def find_template_by_template_id(self, template_id: str) -> dict | None:
        """Delegates to :class:`TemplateStore.find_template_by_template_id`."""
        return self._templates.find_template_by_template_id(template_id)

    def get_active_templates(self, *, niche_id: str | None = None) -> list[dict]:
        """Delegates to :class:`TemplateStore.get_active_templates`."""
        return self._templates.get_active_templates(niche_id=niche_id)

    # ===== ASSETS =====

    def create_asset(self, asset: dict) -> str:
        """Delegates to :class:`AssetStore.create_asset`."""
        return self._assets.create_asset(asset)

    def find_asset_by_asset_id(
        self,
        asset_id: str,
        *,
        niche_id: str | None = None,
    ) -> dict | None:
        """Delegates to :class:`AssetStore.find_asset_by_asset_id`."""
        return self._assets.find_asset_by_asset_id(asset_id, niche_id=niche_id)

    def find_asset_by_url(
        self,
        url: str,
        *,
        niche_id: str | None = None,
    ) -> dict | None:
        """Delegates to :class:`AssetStore.find_asset_by_url`."""
        return self._assets.find_asset_by_url(url, niche_id=niche_id)

    def find_assets_by_story_id(
        self,
        story_id: str,
        *,
        niche_id: str | None = None,
    ) -> list[dict]:
        """Delegates to :class:`AssetStore.find_assets_by_story_id`."""
        return self._assets.find_assets_by_story_id(story_id, niche_id=niche_id)

    def update_asset_status(
        self,
        asset_id: str,
        status: str,
        *,
        niche_id: str | None = None,
        **kwargs,
    ) -> None:
        """Delegates to :class:`AssetStore.update_asset_status`."""
        self._assets.update_asset_status(asset_id, status, niche_id=niche_id, **kwargs)

    def batch_create_assets(self, assets: list[dict]) -> list[str]:
        """Delegates to :class:`AssetStore.batch_create_assets`."""
        return self._assets.batch_create_assets(assets)

    # ===== SOURCES =====

    def create_source(self, source: dict) -> str:
        """Delegates to :class:`SourceStore.create_source`."""
        return self._sources.create_source(source)

    def update_source_fetch_status(self, source_id: str, status: str) -> None:
        """Delegates to :class:`SourceStore.update_source_fetch_status`."""
        self._sources.update_source_fetch_status(source_id, status)

    def get_enabled_sources(self, *, niche_id: str | None = None) -> list[dict]:
        """Delegates to :class:`SourceStore.get_enabled_sources`."""
        return self._sources.get_enabled_sources(niche_id=niche_id)

    # ===== ATTACHMENTS =====

    def upload_attachment(
        self,
        record_id: str,
        field_name: str,
        file_path: Path,
        content_type: str = "image/png",
    ) -> dict | None:
        file_path = Path(file_path)
        if not file_path.exists():
            logger.warning("File not found for upload: %s", file_path)
            return None
        try:
            result = self._backend("Blueprints").upload_attachment(
                "Blueprints",
                record_id,
                field_name,
                str(file_path),
            )
            return result
        except Exception as exc:
            logger.warning("Attachment upload error for %s: %s", file_path.name, exc)
            return None

    # ===== PUBLISHING ANALYTICS =====

    def log_publish_result(
        self,
        candidate_id: str,
        platform: str,
        status: str,
        post_id: str = "",
        platform_format: str = "",
        time_to_publish_seconds: float = 0.0,
        error_message: str = "",
        file_size_bytes: int = 0,
        blueprint_record_id: str = "",
        niche_id: str = "",
    ) -> str | None:
        """Delegates to :class:`AnalyticsStore.log_publish_result`."""
        return self._analytics.log_publish_result(
            candidate_id=candidate_id,
            platform=platform,
            status=status,
            post_id=post_id,
            platform_format=platform_format,
            time_to_publish_seconds=time_to_publish_seconds,
            error_message=error_message,
            file_size_bytes=file_size_bytes,
            blueprint_record_id=blueprint_record_id,
            niche_id=niche_id,
        )

    def get_publishing_analytics(
        self,
        platform: str | None = None,
        status: str | None = None,
        *,
        niche_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Delegates to :class:`AnalyticsStore.get_publishing_analytics`."""
        return self._analytics.get_publishing_analytics(
            platform=platform, status=status, niche_id=niche_id, limit=limit
        )

    # ===== ANALYTICS =====

    def upsert_analytics(
        self,
        post_id: str,
        platform: str,
        insights: dict,
        blueprint_record_id: str = "",
        candidate_id: str = "",
        published_at: str = "",
        content_format: str = "",
        fetch_window: str = "",
        story_title: str = "",
        viral_score: float | None = None,
        niche_id: str = "",
    ) -> str | None:
        """Delegates to :class:`AnalyticsStore.upsert_analytics`."""
        return self._analytics.upsert_analytics(
            post_id=post_id,
            platform=platform,
            insights=insights,
            blueprint_record_id=blueprint_record_id,
            candidate_id=candidate_id,
            published_at=published_at,
            content_format=content_format,
            fetch_window=fetch_window,
            story_title=story_title,
            viral_score=viral_score,
            niche_id=niche_id,
        )

    # ===== A/B TESTING =====

    def create_ab_test(self, test: dict) -> str | None:
        """Delegates to :class:`ABTestStore.create_ab_test`."""
        return self._ab_tests.create_ab_test(test)

    def get_ab_tests(
        self,
        status: str | None = None,
        *,
        niche_id: str | None = None,
    ) -> list[dict]:
        """Delegates to :class:`ABTestStore.get_ab_tests`."""
        return self._ab_tests.get_ab_tests(status=status, niche_id=niche_id)

    def update_ab_test(self, test_id: str, fields: dict) -> None:
        """Delegates to :class:`ABTestStore.update_ab_test`."""
        self._ab_tests.update_ab_test(test_id, fields)

    # ===== ENGAGEMENT (Sprint 23 — observe-only) =====

    def write_pending_engagement(self, event: dict) -> str | None:
        """Delegates to :class:`EngagementStore.write_pending_engagement`."""
        return self._engagement.write_pending_engagement(event)

    def list_pending_engagement(
        self,
        niche_id: str | None = None,
        status: str = "pending",
        limit: int = 50,
    ) -> list[dict]:
        """Delegates to :class:`EngagementStore.list_pending_engagement`."""
        return self._engagement.list_pending_engagement(
            niche_id=niche_id, status=status, limit=limit
        )

    def update_engagement_status(
        self,
        item_id: str,
        status: str,
        reply_text: str = "",
        error_msg: str = "",
        *,
        niche_id: str | None = None,
    ) -> None:
        """Delegates to :class:`EngagementStore.update_engagement_status`.

        2026-07-14: added ``niche_id`` pass-through so tenant binding
        is preserved on the .update() call (backlog audit F3 RLS bypass).
        """
        self._engagement.update_engagement_status(
            item_id=item_id,
            status=status,
            reply_text=reply_text,
            error_msg=error_msg,
            niche_id=niche_id,
        )

    # ===== NICHE REGISTRY =====

    # ===== UTILITY =====

    def health_check(self) -> bool:
        try:
            self._backend("Stories").find("Stories", max_records=1)
            return True
        except Exception as e:
            err_str = str(e)
            from genlab_core.settings import settings

            secret = settings.azure_client_secret or ""
            if secret and secret in err_str:
                err_str = err_str.replace(secret, "***REDACTED***")
            logger.error("Microsoft Lists health check failed: %s", err_str)
            return False
