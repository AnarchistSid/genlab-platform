"""Microsoft Lists client for content workflow management.

Provides typed CRUD operations for all shared tables:
  Stories, Blueprints, Templates, Assets, Sources,
  Publishing_Analytics, Analytics, AB_Tests.

Both Content Scraper and CriticalRush use the same SharePoint Lists,
differentiating content via the niche_id column.

Requires AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET,
and SHAREPOINT_SITE_ID in .env.

Config path resolution (in order):
  1. config_path parameter to __init__
  2. BACKLOG_CONFIG_PATH env var
  3. Auto-detect: walk up from caller's working directory
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from genlab_core.http.circuit_breaker import SHAREPOINT_CB, CircuitOpenError
from genlab_core.http.graph_proxy import GraphTableProxy, _esc

logger = logging.getLogger(__name__)

# Shared error tuple for backlog operations
try:
    from kiota_abstractions.api_error import APIError as GraphAPIError
    from requests.exceptions import RequestException
    BACKLOG_OP_ERRORS = (
        RequestException, TimeoutError, OSError, RuntimeError,
        ValueError, KeyError, TypeError, GraphAPIError,
    )
except ImportError:
    BACKLOG_OP_ERRORS = (
        TimeoutError, OSError, RuntimeError, ValueError, KeyError, TypeError,
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

    _STATUS_ORDER = [
        "INTAKE", "VALIDATED", "INTEL_READY", "RESEARCHED",
        "DRAFTED", "VISUAL_READY", "SCHEDULED", "PUBLISHED",
        "ANALYZED", "ARCHIVED",
    ]

    _GUARDED_FIELDS = frozenset({"status", "visual_paths", "scheduled_for"})

    def __init__(self, proxy: GraphTableProxy):
        object.__setattr__(self, "_proxy", proxy)

    def __getattr__(self, name: str):
        return getattr(self._proxy, name)

    def update(
        self,
        record_id: str,
        fields: Dict[str, Any],
        typecast: bool = False,
        *,
        force: bool = False,
    ) -> Dict[str, Any]:
        if not force and not _schedule_guard_forced():
            self._guard_update(record_id, fields)
        return self._proxy.update(record_id, fields, typecast)

    def delete(self, record_id: str, *, force: bool = False) -> None:
        if not force and not _schedule_guard_forced():
            self._guard_delete(record_id)
        self._proxy.delete(record_id)

    def batch_update(
        self,
        records: List[Dict[str, Any]],
        *,
        force: bool = False,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        if not force and not _schedule_guard_forced():
            for rec in records:
                rec_id = rec.get("id", "")
                rec_fields = rec.get("fields", {})
                if rec_id:
                    self._guard_update(str(rec_id), rec_fields)
        return self._proxy.batch_update(records, **kwargs)

    def _guard_update(self, record_id: str, fields: Dict[str, Any]) -> None:
        touched = self._GUARDED_FIELDS & set(fields.keys())
        if not touched:
            return

        record = self._proxy.get(str(record_id))
        rec_fields = record.get("fields", record)
        scheduled_for = rec_fields.get("scheduled_for")
        if not scheduled_for:
            return

        new_status = fields.get("status")
        if new_status:
            old_status = rec_fields.get("status", "")
            if self._is_demotion(old_status, new_status):
                raise ScheduledPostProtectionError(
                    f"Cannot demote scheduled blueprint rec={record_id} "
                    f"from {old_status} → {new_status} "
                    f"(scheduled_for={scheduled_for})."
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
        "og_image", "og_video", "schema_org", "hero_image",
        "video_embed", "stock_search", "generated",
    }

    _STATUS_ORDER = [
        "INTAKE", "VALIDATED", "INTEL_READY", "RESEARCHED",
        "DRAFTED", "VISUAL_READY", "SCHEDULED", "PUBLISHED",
        "ANALYZED", "ARCHIVED",
    ]

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
        from azure.identity import ClientSecretCredential
        from msgraph import GraphServiceClient

        from genlab_core.settings import settings

        tenant = (tenant_id or settings.azure_tenant_id or "").strip()
        client_id = (client_id or settings.azure_client_id or "").strip()
        secret = (client_secret or settings.azure_client_secret or "").strip()
        self._site_id = (site_id or settings.sharepoint_site_id or "").strip()

        if not all([tenant, client_id, secret, self._site_id]):
            raise ValueError(
                "AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, "
                "and SHAREPOINT_SITE_ID must be set in .env"
            )

        cred = ClientSecretCredential(tenant, client_id, secret)
        self._graph = GraphServiceClient(
            cred, scopes=["https://graph.microsoft.com/.default"]
        )

        # Resolve config path
        if config_path is not None:
            config_path = Path(config_path)
        else:
            env_path = os.getenv("BACKLOG_CONFIG_PATH", "")
            if env_path:
                config_path = Path(env_path)
            else:
                # Auto-detect: look for config/lists_config.yaml in CWD parents
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

        try:
            self.ab_tests = _proxy("AB_Tests")
        except (ValueError, KeyError):
            self.ab_tests = None

        try:
            self.audience_snapshots = _proxy("Audience_Snapshots")
        except (ValueError, KeyError):
            self.audience_snapshots = None

        try:
            self.pending_engagement = _proxy("PendingEngagement")
        except (ValueError, KeyError):
            self.pending_engagement = None

        try:
            self.pending_feedback = _proxy("PendingFeedback")
        except (ValueError, KeyError):
            self.pending_feedback = None

        try:
            self.bandit_arms = _proxy("BanditArms")
        except (ValueError, KeyError):
            self.bandit_arms = None

        try:
            self.content_memory = _proxy("Content_Memory")
        except (ValueError, KeyError):
            self.content_memory = None

    # ── Circuit breaker helper ───────────────────────────────────────

    @staticmethod
    def _sp_call(fn, *args, **kwargs):
        """Execute a SharePoint/Graph API call through the circuit breaker.

        Falls back to direct call if the circuit is open and we have
        no alternative — the caller will see the CircuitOpenError.
        """
        return SHAREPOINT_CB.call(fn, *args, **kwargs)

    # ── Private helpers ──────────────────────────────────────────────

    def _resolve_source(self, story: Dict) -> str:
        if story.get("source") and story["source"] != "Other":
            return story["source"]
        domain = story.get("domain", "")
        for key, name in self.DOMAIN_SOURCE_MAP.items():
            if key in domain:
                return name
        return "Other"

    def _normalize_asset_source_type(
        self, raw_source: Optional[str], asset_type: str
    ) -> str:
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

    def assert_not_scheduled(self, blueprint: Dict, new_status: str) -> None:
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

    def create_story(self, story: Dict) -> str:
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
        record = self._sp_call(self.stories.create, fields)
        return record["id"]

    def find_story_by_story_id(self, story_id: str, *, niche_id: str | None = None) -> Optional[Dict]:
        formula = f"{{story_id}}='{_esc(story_id)}'"
        formula = _inject_niche_filter(formula, niche_id)
        records = self._sp_call(self.stories.all, formula=formula, max_records=1)
        return records[0] if records else None

    def update_story_status(self, story_id: str, status: str, *, niche_id: str | None = None, **kwargs):
        story = self.find_story_by_story_id(story_id, niche_id=niche_id)
        if not story:
            raise ValueError(f"Story {story_id} not found")
        self._sp_call(self.stories.update, story["id"], {"status": status, **kwargs})

    def batch_create_stories(self, stories: List[Dict]) -> List[str]:
        records = []
        for story in stories:
            scores = story.get("scores", {})
            records.append({
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
            })
        created = self.stories.batch_create(records)
        return [r["id"] for r in created]

    # ===== BLUEPRINTS =====

    def create_blueprint(
        self, blueprint: Dict, story_record: Optional[Dict] = None,
        template_record: Optional[Dict] = None,
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
            record = self.blueprints.create(fields, typecast=True)
        except Exception as e:
            err_str = str(e)
            if "UNKNOWN_FIELD_NAME" in err_str or "columnNotFound" in err_str or "not recognized" in err_str:
                for f in (
                    "template_id", "template_name", "topic_category",
                    "hook_formula", "published_hour", "published_day",
                    "clip_url",
                ):
                    fields.pop(f, None)
                record = self.blueprints.create(fields)
            else:
                raise
        return record["id"]

    def find_blueprint_by_candidate_id(self, candidate_id: str, *, niche_id: str | None = None) -> Optional[Dict]:
        formula = f"{{candidate_id}}='{_esc(candidate_id)}'"
        formula = _inject_niche_filter(formula, niche_id)
        records = self._sp_call(self.blueprints.all, formula=formula, max_records=1)
        return records[0] if records else None

    def update_blueprint_status(
        self, candidate_id: str, status: str, *, niche_id: str | None = None, force: bool = False, **kwargs
    ):
        blueprint = self.find_blueprint_by_candidate_id(candidate_id, niche_id=niche_id)
        if not blueprint:
            raise ValueError(f"Blueprint {candidate_id} not found")
        if not force:
            self.assert_not_scheduled(blueprint, status)
        self.blueprints.update(
            blueprint["id"], {"status": status, **kwargs}, typecast=True
        )

    def get_blueprints_safe_to_cleanup(
        self, status: str, *, niche_id: str | None = None, max_priority: float = 1.0
    ) -> List[Dict]:
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

    def get_blueprints_by_status(self, status: str, *, niche_id: str | None = None) -> List[Dict]:
        formula = f"{{status}}='{_esc(status)}'"
        formula = _inject_niche_filter(formula, niche_id)
        return self._sp_call(self.blueprints.all, formula=formula)

    def batch_create_blueprints(self, blueprints: List[Dict]) -> List[str]:
        story_cache: Dict[str, Optional[Dict]] = {}
        template_cache: Dict[str, Optional[Dict]] = {}
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

            records.append({
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
            })

        created = self.blueprints.batch_create(records)
        return [r["id"] for r in created]

    # ===== TEMPLATES =====

    def create_template(self, template: Dict) -> str:
        constraints = template.get("constraints", {})
        record = self.templates.create(
            {
                "template_id": template["template_id"],
                "name": template["name"],
                "format": template["format"],
                "best_for": template.get("best_for", []),
                "max_slides": constraints.get("max_slides"),
                "max_words_per_slide_title": constraints.get("max_words_per_slide_title"),
                "max_words_per_slide_body": constraints.get("max_words_per_slide_body"),
                "max_reel_seconds": constraints.get("max_reel_seconds"),
                "structure": "\n".join(template.get("structure", [])),
                "default_cta": template.get("default_cta", ""),
                "pattern_refs": ", ".join(template.get("pattern_refs", [])),
                "status": "active",
            },
            typecast=True,
        )
        return record["id"]

    def find_template_by_template_id(self, template_id: str) -> Optional[Dict]:
        records = self.templates.all(
            formula=f"{{template_id}}='{_esc(template_id)}'", max_records=1
        )
        return records[0] if records else None

    def get_active_templates(self, *, niche_id: str | None = None) -> List[Dict]:
        formula = "{status}='active'"
        formula = _inject_niche_filter(formula, niche_id)
        return self.templates.all(formula=formula)

    # ===== ASSETS =====

    def create_asset(self, asset: Dict) -> str:
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

        if quality_fields:
            try:
                record = self.assets.create({**fields, **quality_fields}, typecast=True)
                return record["id"]
            except Exception as e:
                if "UNKNOWN_FIELD_NAME" not in str(e) and "columnNotFound" not in str(e):
                    raise

        record = self.assets.create(fields, typecast=True)
        return record["id"]

    def find_asset_by_asset_id(self, asset_id: str, *, niche_id: str | None = None) -> Optional[Dict]:
        formula = f"{{asset_id}}='{_esc(asset_id)}'"
        formula = _inject_niche_filter(formula, niche_id)
        records = self.assets.all(formula=formula, max_records=1)
        return records[0] if records else None

    def find_asset_by_url(self, url: str, *, niche_id: str | None = None) -> Optional[Dict]:
        escaped = url.replace("'", "\\'")
        formula = f"{{url}}='{escaped}'"
        formula = _inject_niche_filter(formula, niche_id)
        records = self.assets.all(formula=formula, max_records=1)
        return records[0] if records else None

    def find_assets_by_story_id(self, story_id: str, *, niche_id: str | None = None) -> List[Dict]:
        story = self.find_story_by_story_id(story_id, niche_id=niche_id)
        if not story:
            return []
        formula = f"{{story_link}}='{_esc(story['id'])}'"
        formula = _inject_niche_filter(formula, niche_id)
        return self.assets.all(formula=formula)

    def update_asset_status(self, asset_id: str, status: str, *, niche_id: str | None = None, **kwargs):
        formula = f"{{asset_id}}='{_esc(asset_id)}'"
        formula = _inject_niche_filter(formula, niche_id)
        records = self.assets.all(formula=formula, max_records=1)
        if not records:
            raise ValueError(f"Asset {asset_id} not found")
        self.assets.update(records[0]["id"], {"status": status, **kwargs})

    def batch_create_assets(self, assets: List[Dict]) -> List[str]:
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

    def create_source(self, source: Dict) -> str:
        record = self.sources.create({
            "source_id": source["source_id"],
            "domain": source["domain"],
            "name": source.get("name", source["domain"]),
            "url": source["url"],
            "type": source["type"],
            "priority": source.get("priority", 1.0),
            "enabled": source.get("enabled", True),
            "authority_score": source.get("authority_score", 0.5),
        })
        return record["id"]

    def update_source_fetch_status(self, source_id: str, status: str):
        records = self.sources.all(
            formula=f"{{source_id}}='{_esc(source_id)}'", max_records=1
        )
        if not records:
            return
        self.sources.update(
            records[0]["id"],
            {
                "last_fetch_at": datetime.now(timezone.utc).isoformat(),
                "last_fetch_status": status,
            },
        )

    def get_enabled_sources(self, *, niche_id: str | None = None) -> List[Dict]:
        formula = "{enabled}=TRUE()"
        formula = _inject_niche_filter(formula, niche_id)
        return self.sources.all(formula=formula)

    # ===== ATTACHMENTS =====

    def upload_attachment(
        self,
        record_id: str,
        field_name: str,
        file_path: Path,
        content_type: str = "image/png",
    ) -> Optional[Dict]:
        file_path = Path(file_path)
        if not file_path.exists():
            logger.warning("File not found for upload: %s", file_path)
            return None
        try:
            result = self.blueprints.upload_attachment(
                record_id, field_name, str(file_path)
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
    ) -> Optional[str]:
        raw = f"{candidate_id}:{platform}"
        analytics_id = hashlib.sha256(raw.encode()).hexdigest()

        fields = {
            "analytics_id": analytics_id,
            "candidate_id": candidate_id,
            "platform": platform,
            "status": status,
        }

        if post_id:
            fields["post_id"] = post_id
        if platform_format:
            fields["platform_format"] = platform_format
        if time_to_publish_seconds > 0:
            fields["time_to_publish_seconds"] = round(time_to_publish_seconds, 1)
        if error_message:
            fields["error_message"] = error_message[:2000]
        if file_size_bytes > 0:
            fields["file_size_bytes"] = file_size_bytes
        if status == "SUCCESS":
            fields["published_at"] = datetime.now(timezone.utc).isoformat()
        if blueprint_record_id:
            fields["blueprint_link"] = str(blueprint_record_id)
        if niche_id:
            fields["niche_id"] = niche_id

        try:
            existing = self._sp_call(
                self.publishing_analytics.all,
                formula=f"{{analytics_id}}='{_esc(analytics_id)}'",
                max_records=1,
            )
            if existing:
                self._sp_call(
                    self.publishing_analytics.update,
                    existing[0]["id"], fields, typecast=True,
                )
                return existing[0]["id"]
            else:
                record = self._sp_call(
                    self.publishing_analytics.create, fields, typecast=True,
                )
                return record["id"]
        except CircuitOpenError:
            logger.warning(
                "Publishing analytics log skipped — SharePoint circuit open"
            )
            return None
        except Exception as exc:
            logger.warning("Publishing analytics log failed: %s", exc)
            return None

    def get_publishing_analytics(
        self,
        platform: Optional[str] = None,
        status: Optional[str] = None,
        *,
        niche_id: str | None = None,
        limit: int = 100,
    ) -> List[Dict]:
        parts = []
        if platform:
            parts.append(f"{{platform}}='{_esc(platform)}'")
        if status:
            parts.append(f"{{status}}='{_esc(status)}'")
        formula = f"AND({', '.join(parts)})" if parts else ""
        formula = _inject_niche_filter(formula or None, niche_id)
        return self._sp_call(
            self.publishing_analytics.all, formula=formula, max_records=limit,
        )

    # ===== ANALYTICS =====

    def upsert_analytics(
        self,
        post_id: str,
        platform: str,
        insights: Dict,
        blueprint_record_id: str = "",
        candidate_id: str = "",
        published_at: str = "",
        content_format: str = "",
        fetch_window: str = "",
        story_title: str = "",
        viral_score: Optional[float] = None,
        niche_id: str = "",
    ) -> Optional[str]:
        composite_id = f"{platform}:{post_id}"

        reach = insights.get("reach", 0) or insights.get("impressions", 0) or 0
        engagement = insights.get("engagement", 0) or 0
        likes = insights.get("likes", 0) or 0
        comments = insights.get("comments", 0) or 0
        saves = insights.get("saves", 0) or insights.get("saved", 0) or 0
        shares = insights.get("shares", 0) or insights.get("retweets", 0) or 0
        plays = insights.get("plays", 0) or insights.get("views", 0) or 0
        impressions = insights.get("impressions", 0) or reach

        engagement_rate = round(engagement / max(reach, 1), 4)
        save_rate = round(saves / max(reach, 1), 4)
        share_rate = round(shares / max(reach, 1), 4)
        play_rate = round(plays / max(reach, 1), 4)

        # Virality score: caller can pass a pre-computed config-driven
        # score; otherwise fall back to a generic weighted formula.
        if viral_score is not None:
            viral_score = round(viral_score, 4)
        else:
            viral_score = round(
                engagement_rate * 0.25 + share_rate * 0.40 + save_rate * 0.35, 4
            )

        fields = {
            "post_id": composite_id,
            "platform": platform,
            "impressions": impressions,
            "reach": reach,
            "engagement": engagement,
            "likes": likes,
            "comments": comments,
            "saved": saves,
            "shares": shares,
            "plays": plays,
            "viral_score": viral_score,
            "engagement_rate": engagement_rate,
            "save_rate": save_rate,
            "share_rate": share_rate,
            "play_rate": play_rate,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        if blueprint_record_id:
            fields["blueprint_link"] = str(blueprint_record_id)
        if candidate_id:
            fields["candidate_id"] = candidate_id
        if published_at:
            fields["published_at"] = published_at
        if content_format:
            fields["format"] = content_format
        if fetch_window:
            fields["fetch_window"] = fetch_window
        if story_title:
            fields["story_title"] = story_title
        if niche_id:
            fields["niche_id"] = niche_id

        _OPTIONAL_FIELDS = {
            "platform", "candidate_id", "published_at",
            "format", "fetch_window", "story_title", "niche_id",
        }

        try:
            existing = self.analytics.all(
                formula=f"{{post_id}}='{_esc(composite_id)}'",
                max_records=1,
            )
            if existing:
                try:
                    self.analytics.update(existing[0]["id"], fields, typecast=True)
                except Exception as e:
                    if "UNKNOWN_FIELD_NAME" in str(e) or "columnNotFound" in str(e):
                        for f_name in _OPTIONAL_FIELDS:
                            fields.pop(f_name, None)
                        self.analytics.update(existing[0]["id"], fields, typecast=True)
                    else:
                        raise
                return existing[0]["id"]
            else:
                try:
                    record = self.analytics.create(fields, typecast=True)
                except Exception as e:
                    if "UNKNOWN_FIELD_NAME" in str(e) or "columnNotFound" in str(e):
                        for f_name in _OPTIONAL_FIELDS:
                            fields.pop(f_name, None)
                        record = self.analytics.create(fields, typecast=True)
                    else:
                        raise
                return record["id"]
        except Exception as exc:
            logger.warning("Analytics upsert failed for %s: %s", composite_id, exc)
            return None

    # ===== A/B TESTING =====

    def create_ab_test(self, test: Dict) -> Optional[str]:
        if not self.ab_tests:
            logger.warning("AB_Tests table not configured")
            return None
        try:
            record = self.ab_tests.create(test, typecast=True)
            return record["id"]
        except Exception as exc:
            logger.warning("Failed to create AB test: %s", exc)
            return None

    def get_ab_tests(self, status: Optional[str] = None, *, niche_id: str | None = None) -> List[Dict]:
        if not self.ab_tests:
            return []
        formula = f"{{status}}='{_esc(status)}'" if status else None
        formula = _inject_niche_filter(formula, niche_id)
        return self.ab_tests.all(formula=formula, max_records=50)

    def update_ab_test(self, test_id: str, fields: Dict):
        if not self.ab_tests:
            return
        records = self.ab_tests.all(
            formula=f"{{test_id}}='{_esc(test_id)}'", max_records=1
        )
        if records:
            self.ab_tests.update(records[0]["id"], fields, typecast=True)

    # ===== ENGAGEMENT (Sprint 23 — observe-only) =====

    def write_pending_engagement(self, event: dict) -> str | None:
        """Record an incoming comment event for monitoring.

        Event dict should have: comment_id, platform, post_id, text,
        author_name, created_at (ISO string), niche_id.
        Status defaults to 'pending'.

        Returns the SharePoint item ID on success, None on failure.
        """
        if not self.pending_engagement:
            logger.warning("[engagement] PendingEngagement table not configured")
            return None
        fields = {
            "Title": event.get("comment_id", ""),
            "Platform": event.get("platform", ""),
            "PostId": event.get("post_id", ""),
            "CommentText": (event.get("text") or "")[:2000],
            "AuthorName": event.get("author_name", ""),
            "CreatedAt": event.get("created_at", ""),
            "NicheId": event.get("niche_id", ""),
            "Status": "pending",
        }
        try:
            result = self.pending_engagement.create(fields)
            return str(result.get("id", "")) or None
        except Exception as e:
            logger.warning("[engagement] write_pending_engagement failed: %s", e)
            return None

    def list_pending_engagement(
        self,
        niche_id: str | None = None,
        status: str = "pending",
        limit: int = 50,
    ) -> list[dict]:
        """Retrieve pending comment events for monitoring/processing."""
        if not self.pending_engagement:
            logger.warning("[engagement] PendingEngagement table not configured")
            return []
        formula = f"{{Status}}='{_esc(status)}'"
        if niche_id:
            formula = f"AND({{Status}}='{_esc(status)}', {{NicheId}}='{_esc(niche_id)}')"
        try:
            return self.pending_engagement.all(
                formula=formula,
                max_records=limit,
            )
        except Exception as e:
            logger.warning("[engagement] list_pending_engagement failed: %s", e)
            return []

    def update_engagement_status(
        self,
        item_id: str,
        status: str,
        reply_text: str = "",
        error_msg: str = "",
    ) -> None:
        """Update the status of a pending engagement item.

        Args:
            item_id: SharePoint list item ID
            status: New status (replied, liked, skipped, failed, rate_limited)
            reply_text: The reply that was posted (if status=replied)
            error_msg: Error message (if status=failed)
        """
        if not self.pending_engagement:
            logger.warning("BacklogClient: pending_engagement proxy not configured — skipping status update")
            return

        VALID_STATUSES = {"replied", "liked", "skipped", "failed", "rate_limited", "pending"}
        if status not in VALID_STATUSES:
            logger.warning("BacklogClient: invalid engagement status '%s'", status)
            return

        fields: dict[str, str] = {
            "Status": status,
            "ProcessedAt": datetime.now(timezone.utc).isoformat(),
        }
        if reply_text:
            fields["ReplyText"] = reply_text[:2000]
        if error_msg:
            fields["ErrorMessage"] = error_msg[:500]

        try:
            self.pending_engagement.update(item_id, fields)
            logger.info("BacklogClient: engagement %s → %s", item_id, status)
        except Exception as e:
            logger.warning("BacklogClient: failed to update engagement %s: %s", item_id, e)

    # ===== NICHE REGISTRY =====

    def list_niches(self) -> List[Dict]:
        """Return all registered niches from YAML registry.

        Falls back to hardcoded registry if file not found.
        """
        import yaml

        # Try to find niches_registry.yaml
        for search_dir in [Path.cwd()] + list(Path.cwd().parents):
            candidate = search_dir / "configs" / "niches_registry.yaml"
            if candidate.exists():
                with open(candidate, "r") as f:
                    data = yaml.safe_load(f) or {}
                return data.get("niches", [])

        # Hardcoded fallback
        return [
            {"id": "ai_creators", "display_name": "Blackbox Brief", "status": "active"},
            {"id": "gaming", "display_name": "CriticalRush", "status": "active"},
            {"id": "sports", "display_name": "ClutchWire", "status": "mvp"},
            {"id": "movies", "display_name": "SpliceReel", "status": "mvp"},
            {"id": "anime", "display_name": "FrameDrift", "status": "mvp"},
        ]

    # ===== UTILITY =====

    def health_check(self) -> bool:
        try:
            self.stories.all(max_records=1)
            return True
        except Exception as e:
            err_str = str(e)
            from genlab_core.settings import settings
            secret = settings.azure_client_secret or ""
            if secret and secret in err_str:
                err_str = err_str.replace(secret, "***REDACTED***")
            logger.error("Microsoft Lists health check failed: %s", err_str)
            return False
