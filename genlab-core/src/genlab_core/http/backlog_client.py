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
from genlab_core.http.circuit_breaker import SHAREPOINT_CB
from genlab_core.http.engagement_store import EngagementStore
from genlab_core.http.graph_proxy import GraphTableProxy, _esc
from genlab_core.http.source_store import SourceStore
from genlab_core.http.template_store import TemplateStore

logger = logging.getLogger(__name__)

# Canonical blueprint status progression — used by ScheduleGuardedProxy and
# BacklogClient for demotion detection.  Keep this list in pipeline order.
STATUS_ORDER: list[str] = [
    "INTAKE",
    "VALIDATED",
    "INTEL_READY",
    "RESEARCHED",
    "DRAFTED",
    "VISUAL_READY",
    "SCHEDULED",
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
_PENDING_STATUSES: frozenset[str] = frozenset(
    {"DRAFTED", "VISUAL_READY", "SCHEDULED", "PUBLISHING"}
)
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

        new_status = fields.get("status")
        if new_status:
            old_status = rec_fields.get("status", "")
            if self._is_forbidden_for_scheduled(old_status, new_status):
                raise ScheduledPostProtectionError(
                    f"Cannot move scheduled blueprint rec={record_id} "
                    f"from {old_status} → {new_status} — would discard its queued "
                    f"slot (scheduled_for={scheduled_for})."
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

    Returns original formula if niche_id is None.
    """
    if not niche_id:
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
        self._engagement = EngagementStore(self.pending_engagement)

        # AB_Tests surface (Tier 2, audit S-2 slice 2.3).
        # Multi-table backend pattern (like AnalyticsStore) but
        # also captures the ``self.ab_tests`` proxy ref for the
        # "is configured" truthy gate.
        self._ab_tests = ABTestStore(
            ab_tests=self.ab_tests,
            backend=self._backend,
        )

        # Sources + Templates surfaces (Tier 2, audit S-2 slice 2.4).
        # Simplest pattern in the family: backend-only — both lists
        # are unconditionally present in ``lists_config.yaml``, so
        # there's no truthy 'configured' gate to mirror.
        self._sources = SourceStore(backend=self._backend)
        self._templates = TemplateStore(backend=self._backend)

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
        scores = story.get("scores", {})
        fields = {
            "story_id": story["story_id"],
            "title": story["title"],
            "url": story["url"],
            "source": self._resolve_source(story),
            "published_at": story.get("published_at"),
            "summary": story.get("summary", ""),
            "why_it_matters": story.get("why_it_matters", ""),
            "priority": story.get("priority", scores.get("priority", 0.5)),
            "status": "INTAKE",
            "themes": story.get("themes", []),
            "authority_score": scores.get("authority", 0.0),
            "recency_score": scores.get("recency", 0.0),
            "novelty_score": scores.get("novelty", 0.0),
        }
        if story.get("niche_id"):
            fields["niche_id"] = story["niche_id"]
        record = self._sp_call(
            self._backend("Stories").create,
            "Stories",
            fields,
        )
        return record["id"]

    def find_story_by_story_id(self, story_id: str, *, niche_id: str | None = None) -> dict | None:
        formula = f"{{story_id}}='{_esc(story_id)}'"
        records = self._sp_call(
            self._backend("Stories").find,
            "Stories",
            formula=formula,
            niche_id=niche_id,
            max_records=1,
        )
        return records[0] if records else None

    def update_story_status(
        self, story_id: str, status: str, *, niche_id: str | None = None, **kwargs
    ):
        story = self.find_story_by_story_id(story_id, niche_id=niche_id)
        if not story:
            raise ValueError(f"Story {story_id} not found")
        self._sp_call(
            self._backend("Stories").update,
            "Stories",
            story["id"],
            {"status": status, **kwargs},
        )

    def batch_create_stories(self, stories: list[dict]) -> list[str]:
        records = []
        for story in stories:
            scores = story.get("scores", {})
            records.append(
                {
                    "story_id": story["story_id"],
                    "title": story["title"],
                    "url": story["url"],
                    "source": self._resolve_source(story),
                    "published_at": story.get("published_at"),
                    "summary": story.get("summary", ""),
                    "priority": story.get("priority", scores.get("priority", 0.5)),
                    "status": "INTAKE",
                    "themes": story.get("themes", []),
                    "authority_score": scores.get("authority", 0.0),
                    "recency_score": scores.get("recency", 0.0),
                    "novelty_score": scores.get("novelty", 0.0),
                }
            )
        created = self._backend("Stories").batch_create("Stories", records)
        return [r["id"] for r in created]

    # ===== BLUEPRINTS =====

    def create_blueprint(
        self,
        blueprint: dict,
        story_record: dict | None = None,
        template_record: dict | None = None,
    ) -> str:
        story = story_record or self.find_story_by_story_id(blueprint["story_id"])
        if not story:
            raise ValueError(f"Story {blueprint['story_id']} not found")

        template_record_id = None
        if blueprint.get("template_id"):
            template = template_record or self.find_template_by_template_id(
                blueprint["template_id"]
            )
            if template:
                template_record_id = template["id"]

        validation = blueprint.get("validation_status", {})

        fields = {
            "candidate_id": blueprint["candidate_id"],
            "story": [story["id"]],
            "template": [template_record_id] if template_record_id else [],
            "template_id": blueprint.get("template_id", ""),
            "template_name": blueprint.get("template_name", ""),
            "topic": blueprint.get("topic", ""),
            "angle": blueprint.get("angle", ""),
            "format": blueprint.get("format"),
            "hook": blueprint.get("hook", ""),
            "structure": blueprint.get("structure_text", "")
            or "\n".join(blueprint.get("structure", [])),
            "cta": blueprint.get("cta", ""),
            "priority_score": blueprint.get("priority_score", 0.5),
            "status": "INTEL_READY",
            "why_this_will_work": blueprint.get("why_this_will_work", ""),
            "validation_constraints_passed": validation.get("constraints_passed", False),
            "validation_claims_passed": validation.get("claims_passed", False),
            "validation_risk_acceptable": validation.get("risk_acceptable", False),
        }

        if blueprint.get("niche_id"):
            fields["niche_id"] = blueprint["niche_id"]

        # clip_url: persisted by push_to_backlog so renderer can download the clip
        if blueprint.get("clip_url"):
            fields["clip_url"] = blueprint["clip_url"]

        # Optional performance fields (caller enriches blueprint dict before
        # passing it — no cross-project import needed).
        for key in ("topic_category", "hook_formula", "published_hour", "published_day"):
            val = blueprint.get(key)
            if val:
                fields[key] = val

        try:
            record = self._backend("Blueprints").create(
                "Blueprints",
                fields,
                typecast=True,
            )
        except Exception as e:
            err_str = str(e)
            if (
                "UNKNOWN_FIELD_NAME" in err_str
                or "columnNotFound" in err_str
                or "not recognized" in err_str
            ):
                for f in (
                    "template_id",
                    "template_name",
                    "topic_category",
                    "hook_formula",
                    "published_hour",
                    "published_day",
                    "clip_url",
                ):
                    fields.pop(f, None)
                record = self._backend("Blueprints").create("Blueprints", fields)
            else:
                raise
        return record["id"]

    def find_blueprint_by_candidate_id(
        self, candidate_id: str, *, niche_id: str | None = None
    ) -> dict | None:
        formula = f"{{candidate_id}}='{_esc(candidate_id)}'"
        records = self._sp_call(
            self._backend("Blueprints").find,
            "Blueprints",
            formula=formula,
            niche_id=niche_id,
            max_records=1,
        )
        return records[0] if records else None

    def update_blueprint_status(
        self,
        candidate_id: str,
        status: str,
        *,
        niche_id: str | None = None,
        force: bool = False,
        **kwargs,
    ):
        blueprint = self.find_blueprint_by_candidate_id(candidate_id, niche_id=niche_id)
        if not blueprint:
            raise ValueError(f"Blueprint {candidate_id} not found")
        if not force:
            self.assert_not_scheduled(blueprint, status)
        self._backend("Blueprints").update(
            "Blueprints",
            blueprint["id"],
            {"status": status, **kwargs},
            typecast=True,
        )

    def get_blueprints_safe_to_cleanup(
        self, status: str, *, niche_id: str | None = None, max_priority: float = 1.0
    ) -> list[dict]:
        all_bps = self.get_blueprints_by_status(status, niche_id=niche_id)
        safe = []
        for bp in all_bps:
            f = bp.get("fields", bp)
            if f.get("scheduled_for"):
                continue
            score = float(f.get("priority_score", 0) or 0)
            if score <= max_priority:
                safe.append(bp)
        return safe

    def get_blueprints_by_status(self, status: str, *, niche_id: str | None = None) -> list[dict]:
        formula = f"{{status}}='{_esc(status)}'"
        return self._sp_call(
            self._backend("Blueprints").find,
            "Blueprints",
            formula=formula,
            niche_id=niche_id,
        )

    def batch_create_blueprints(self, blueprints: list[dict]) -> list[str]:
        story_cache: dict[str, dict | None] = {}
        template_cache: dict[str, dict | None] = {}
        for bp in blueprints:
            sid = bp["story_id"]
            if sid not in story_cache:
                story_cache[sid] = self.find_story_by_story_id(sid)
            tid = bp.get("template_id")
            if tid and tid not in template_cache:
                template_cache[tid] = self.find_template_by_template_id(tid)

        records = []
        for bp in blueprints:
            story = story_cache.get(bp["story_id"])
            if not story:
                logger.warning("Story %s not found, skipping blueprint", bp["story_id"])
                continue

            template_record_id = None
            if bp.get("template_id"):
                template = template_cache.get(bp["template_id"])
                if template:
                    template_record_id = template["id"]

            records.append(
                {
                    "candidate_id": bp["candidate_id"],
                    "story": [story["id"]],
                    "template": [template_record_id] if template_record_id else [],
                    "topic": bp.get("topic", ""),
                    "angle": bp.get("angle", ""),
                    "format": bp.get("format"),
                    "hook": bp.get("hook", ""),
                    "structure": "\n".join(bp.get("structure", [])),
                    "cta": bp.get("cta", ""),
                    "priority_score": bp.get("priority_score", 0.5),
                    "status": "INTEL_READY",
                    "why_this_will_work": bp.get("why_this_will_work", ""),
                }
            )

        created = self._backend("Blueprints").batch_create("Blueprints", records)
        return [r["id"] for r in created]

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
        fields = {
            "asset_id": asset["asset_id"],
            "type": asset["type"],
            "url": asset.get("source_url", asset.get("url", "")),
            "status": asset.get("status", "GENERATING"),
            "source_type": self._normalize_asset_source_type(
                asset.get("source"), asset.get("type", "")
            ),
            "alt_text": asset.get("alt_text", ""),
            "tool_used": asset.get("tool_used"),
            "generation_params": asset.get("generation_params", ""),
            "error_log": asset.get("error_log", asset.get("error", "")),
        }

        quality_fields = {}
        if asset.get("quality_tier"):
            quality_fields["quality_tier"] = asset["quality_tier"]
        if asset.get("width"):
            quality_fields["width"] = asset["width"]
        if asset.get("height"):
            quality_fields["height"] = asset["height"]

        if asset.get("story_id"):
            story = self.find_story_by_story_id(asset["story_id"])
            if story:
                fields["story"] = [story["id"]]

        if asset.get("blueprint_id"):
            blueprint = self.find_blueprint_by_candidate_id(asset["blueprint_id"])
            if blueprint:
                fields["blueprint"] = [blueprint["id"]]

        source_url = asset.get("source_url", asset.get("url", ""))
        if source_url and asset.get("status") == "READY":
            fields["file"] = [{"url": source_url}]

        be = self._backend("Assets")
        if quality_fields:
            try:
                record = be.create(
                    "Assets",
                    {**fields, **quality_fields},
                    typecast=True,
                )
                return record["id"]
            except Exception as e:
                if "UNKNOWN_FIELD_NAME" not in str(e) and "columnNotFound" not in str(e):
                    raise

        record = be.create("Assets", fields, typecast=True)
        return record["id"]

    def find_asset_by_asset_id(self, asset_id: str, *, niche_id: str | None = None) -> dict | None:
        formula = f"{{asset_id}}='{_esc(asset_id)}'"
        records = self._backend("Assets").find(
            "Assets",
            formula=formula,
            niche_id=niche_id,
            max_records=1,
        )
        return records[0] if records else None

    def find_asset_by_url(self, url: str, *, niche_id: str | None = None) -> dict | None:
        escaped = url.replace("'", "\\'")
        formula = f"{{url}}='{escaped}'"
        records = self._backend("Assets").find(
            "Assets",
            formula=formula,
            niche_id=niche_id,
            max_records=1,
        )
        return records[0] if records else None

    def find_assets_by_story_id(self, story_id: str, *, niche_id: str | None = None) -> list[dict]:
        story = self.find_story_by_story_id(story_id, niche_id=niche_id)
        if not story:
            return []
        formula = f"{{story_link}}='{_esc(story['id'])}'"
        return self._backend("Assets").find(
            "Assets",
            formula=formula,
            niche_id=niche_id,
        )

    def update_asset_status(
        self, asset_id: str, status: str, *, niche_id: str | None = None, **kwargs
    ):
        formula = f"{{asset_id}}='{_esc(asset_id)}'"
        records = self._backend("Assets").find(
            "Assets",
            formula=formula,
            niche_id=niche_id,
            max_records=1,
        )
        if not records:
            raise ValueError(f"Asset {asset_id} not found")
        self._backend("Assets").update(
            "Assets",
            records[0]["id"],
            {"status": status, **kwargs},
        )

    def batch_create_assets(self, assets: list[dict]) -> list[str]:
        created_ids = []
        for asset in assets:
            existing = self.find_asset_by_asset_id(asset["asset_id"])
            if existing:
                continue
            try:
                record_id = self.create_asset(asset)
                created_ids.append(record_id)
            except Exception as exc:
                logger.error("Failed to create asset %s: %s", asset["asset_id"], exc)
        return created_ids

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
    ) -> None:
        """Delegates to :class:`EngagementStore.update_engagement_status`."""
        self._engagement.update_engagement_status(
            item_id=item_id, status=status, reply_text=reply_text, error_msg=error_msg
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
