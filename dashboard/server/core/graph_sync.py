"""Synchronous Microsoft Graph REST client for SharePoint Lists.

Bypasses the async MS Graph SDK which deadlocks under gunicorn's eventlet
worker (un-greened RLocks + asyncio conflict).  Uses ``requests`` directly —
eventlet monkey-patches ``requests`` properly.

Every dashboard endpoint that needs SharePoint data should call
``get_sync_client()`` instead of ``BacklogClient()``.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Any

import requests as _requests
import yaml

logger = logging.getLogger(__name__)

# ── Token cache (thread-safe) ───────────────────────────────────────

_token_lock = threading.Lock()
_cached_token: str | None = None
_token_expires_at: float = 0.0  # monotonic timestamp


def _get_token() -> str:
    """Get a valid Bearer token, refreshing if expired."""
    global _cached_token, _token_expires_at

    with _token_lock:
        if _cached_token and time.monotonic() < _token_expires_at:
            return _cached_token

        from azure.identity import ClientSecretCredential
        from genlab_core.settings import settings

        tenant = (settings.azure_tenant_id or "").strip()
        client_id = (settings.azure_client_id or "").strip()
        secret = (settings.azure_client_secret or "").strip()

        if not all([tenant, client_id, secret]):
            raise RuntimeError("Azure credentials not configured")

        cred = ClientSecretCredential(tenant, client_id, secret)
        token_obj = cred.get_token("https://graph.microsoft.com/.default")

        _cached_token = token_obj.token
        # Refresh 5 minutes before actual expiry
        _token_expires_at = time.monotonic() + max(token_obj.expires_on - time.time() - 300, 60)

        return _cached_token


# ── Config loading ───────────────────────────────────────────────────

_config_cache: dict | None = None


def _load_config() -> dict:
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    config_path = os.getenv("BACKLOG_CONFIG_PATH", "")
    if not config_path:
        raise RuntimeError("BACKLOG_CONFIG_PATH not set")
    with open(config_path) as f:
        _config_cache = yaml.safe_load(f) or {}
    return _config_cache


def _get_site_id() -> str:
    from genlab_core.settings import settings

    site_id = (settings.sharepoint_site_id or "").strip()
    if not site_id:
        raise RuntimeError("SHAREPOINT_SITE_ID not configured")
    return site_id


# ── Metadata stripping (mirrors publishing_queue._clean_fields) ──────

_METADATA_KEYS = frozenset(
    {
        "@odata.etag",
        "_UIVersionString",
        "Attachments",
        "AppAuthorLookupId",
        "AppEditorLookupId",
        "AuthorLookupId",
        "EditorLookupId",
        "Content Type",
        "ContentType",
        "ContentTypeId",
        "ComplianceAssetId",
        "GUID",
        "OData__ColorTag",
        "OData__UIVersionString",
        "Edit",
        "_ComplianceFlags",
        "_ComplianceTag",
        "_ComplianceTagWrittenTime",
        "_ComplianceTagUserId",
        "_IsRecord",
        "Created",
        "Modified",
        "Version",
        "Folder Child Count",
        "Item Child Count",
        "FolderChildCount",
        "ItemChildCount",
        "Label applied by",
        "Label setting",
        "Retention label",
        "Retention label Applied",
        "id",
    }
)


_FIELD_ALIASES = {
    "hook": "hook_text",
    "title0": "title",  # SharePoint internal name for Title column
    "Title": "title",
}


def _clean_fields(raw_fields: dict[str, Any]) -> dict[str, Any]:
    fields = {
        k: v
        for k, v in raw_fields.items()
        if k not in _METADATA_KEYS and not k.startswith("@odata.")
    }
    for old_name, new_name in _FIELD_ALIASES.items():
        if old_name in fields and new_name not in fields:
            fields[new_name] = fields.pop(old_name)
    return fields


# ── Formula → OData translation (minimal, dashboard-only patterns) ───


def _formula_to_odata(formula: str | None) -> str | None:
    """Translate formula syntax to OData $filter.

    Handles the patterns used in dashboard endpoints:
      {field}='value'           → fields/field eq 'value'
      AND(...)                  → ... and ...
      OR(...)                   → (... or ...)
      {field}=BLANK()           → fields/field eq null
    """
    if not formula:
        return None
    formula = formula.strip()
    if not formula:
        return None
    return _translate_expr(formula)


def _translate_expr(expr: str) -> str:
    expr = expr.strip()

    # AND(...)
    and_match = re.match(r"^AND\((.+)\)$", expr, re.DOTALL)
    if and_match:
        inner = _split_top_level(and_match.group(1))
        parts = [_translate_expr(p) for p in inner]
        return " and ".join(parts)

    # OR(...)
    or_match = re.match(r"^OR\((.+)\)$", expr, re.DOTALL)
    if or_match:
        inner = _split_top_level(or_match.group(1))
        parts = [_translate_expr(p) for p in inner]
        return "(" + " or ".join(parts) + ")"

    # {field}=BLANK()
    blank_match = re.match(r"^\{([^}]+)\}=BLANK\(\)$", expr)
    if blank_match:
        return f"fields/{blank_match.group(1)} eq null"

    # {field}='value'
    eq_match = re.match(r"^\{([^}]+)\}='([^']*)'$", expr)
    if eq_match:
        field, value = eq_match.groups()
        return f"fields/{field} eq '{value}'"

    # {field}>='value'
    gte_match = re.match(r"^\{([^}]+)\}>='([^']*)'$", expr)
    if gte_match:
        field, value = gte_match.groups()
        return f"fields/{field} ge '{value}'"

    return expr


def _split_top_level(s: str) -> list[str]:
    """Split on commas not nested inside parentheses."""
    parts = []
    depth = 0
    current = []
    for ch in s:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    remainder = "".join(current).strip()
    if remainder:
        parts.append(remainder)
    return parts


# ── SyncListProxy — drop-in for GraphTableProxy ─────────────────────


class SyncListProxy:
    """Synchronous SharePoint List accessor using Graph REST API + requests."""

    def __init__(self, site_id: str, list_id: str, list_name: str = ""):
        self._site_id = site_id
        self._list_id = list_id
        self._list_name = list_name

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {_get_token()}",
            "Prefer": "HonorNonIndexedQueriesWarningMayFailRandomly",
        }

    def _base_url(self) -> str:
        return f"https://graph.microsoft.com/v1.0/sites/{self._site_id}/lists/{self._list_id}/items"

    def all(
        self,
        formula: str | None = None,
        max_records: int | None = None,
        **kwargs,
    ) -> list[dict[str, Any]]:
        """Fetch all records, with optional formula filter."""
        odata_filter = _formula_to_odata(formula)
        used_filter = bool(odata_filter)
        original_formula = formula

        url = self._base_url()
        headers = self._headers()
        params: dict[str, Any] = {"$expand": "fields", "$top": 200}
        if odata_filter:
            params["$filter"] = odata_filter

        all_records: list[dict[str, Any]] = []

        while True:
            # Retry transient failures (429/500/503) up to 2 times
            resp = None
            for _attempt in range(3):
                resp = _requests.get(url, headers=headers, params=params, timeout=30)
                if resp.status_code in (429, 500, 503):
                    wait = 1.0
                    if resp.status_code == 429:
                        retry_after = resp.headers.get("Retry-After")
                        if retry_after:
                            try:
                                wait = min(float(retry_after), 30.0)
                            except (ValueError, TypeError):
                                pass
                    logger.warning(
                        "[%s] Transient %d on attempt %d, retrying in %.1fs",
                        self._list_name,
                        resp.status_code,
                        _attempt + 1,
                        wait,
                    )
                    time.sleep(wait)
                    continue
                break  # Non-transient status or 200 — stop retrying

            if resp is None or resp.status_code != 200:
                if resp is not None and "$filter" in params:
                    logger.warning(
                        "[%s] OData filter failed (%d), falling back to unfiltered: %s",
                        self._list_name,
                        resp.status_code,
                        resp.text[:200],
                    )
                    params.pop("$filter")
                    used_filter = False
                    continue
                logger.error(
                    "[%s] Graph API error %d: %s",
                    self._list_name,
                    resp.status_code if resp is not None else -1,
                    resp.text[:200] if resp is not None else "no response",
                )
                break

            data = resp.json()
            for item in data.get("value", []):
                raw_fields = item.get("fields", {})
                fields = _clean_fields(raw_fields)
                all_records.append(
                    {
                        "id": str(item.get("id", "")),
                        "fields": fields,
                    }
                )

            next_link = data.get("@odata.nextLink")
            if next_link:
                url = next_link
                params = {}
            else:
                break

        # Client-side filter if OData was dropped
        if not used_filter and original_formula:
            all_records = _client_side_filter(all_records, original_formula)

        if max_records:
            all_records = all_records[:max_records]

        logger.debug("[%s] Fetched %d records (sync)", self._list_name, len(all_records))
        return all_records

    def get(self, record_id: str) -> dict[str, Any]:
        """Fetch a single record by ID."""
        url = f"{self._base_url()}/{record_id}"
        headers = self._headers()
        params = {"$expand": "fields"}
        resp = _requests.get(url, headers=headers, params=params, timeout=15)
        if resp.status_code != 200:
            raise RuntimeError(f"Graph GET failed ({resp.status_code}): {resp.text[:200]}")
        item = resp.json()
        return {
            "id": str(item.get("id", "")),
            "fields": _clean_fields(item.get("fields", {})),
        }

    def update(
        self, record_id: str, fields: dict[str, Any], typecast: bool = False
    ) -> dict[str, Any]:
        """Update fields on a record."""
        url = (
            f"https://graph.microsoft.com/v1.0/sites/{self._site_id}"
            f"/lists/{self._list_id}/items/{record_id}/fields"
        )
        headers = {
            "Authorization": f"Bearer {_get_token()}",
            "Content-Type": "application/json",
        }
        resp = _requests.patch(url, headers=headers, json=fields, timeout=30)
        if resp.status_code not in (200, 204):
            raise RuntimeError(f"Graph PATCH failed ({resp.status_code}): {resp.text[:200]}")
        return self.get(record_id)

    def create(self, fields: dict[str, Any], typecast: bool = False) -> dict[str, Any]:
        """Create a new record."""
        url = self._base_url()
        headers = {
            "Authorization": f"Bearer {_get_token()}",
            "Content-Type": "application/json",
        }
        body = {"fields": fields}
        resp = _requests.post(url, headers=headers, json=body, timeout=30)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Graph POST failed ({resp.status_code}): {resp.text[:200]}")
        item = resp.json()
        return {
            "id": str(item.get("id", "")),
            "fields": _clean_fields(item.get("fields", {})),
        }


def _match_conditions(fields: dict[str, Any], conditions: list[tuple]) -> bool:
    """Return True if fields satisfy ALL conditions (AND semantics)."""
    for field_name, expected_value in conditions:
        actual = fields.get(field_name)
        if expected_value is None:
            if actual is not None and actual != "":
                return False
        elif str(actual or "") != expected_value:
            return False
    return True


def _parse_conditions(text: str) -> list[tuple]:
    """Extract (field, value) pairs from a formula fragment."""
    conditions: list[tuple] = []
    for match in re.finditer(r"\{([^}]+)\}\s*=\s*'([^']*)'", text):
        conditions.append((match.group(1), match.group(2)))
    for match in re.finditer(r"\{([^}]+)\}\s*=\s*BLANK\(\)", text):
        conditions.append((match.group(1), None))
    return conditions


def _client_side_filter(records: list[dict[str, Any]], formula: str) -> list[dict[str, Any]]:
    """Apply formula filter client-side when OData fails.

    Supports AND (default) and OR(...) wrapper semantics.
    """
    # Detect OR(...) wrapper
    or_match = re.match(r"^\s*OR\((.+)\)\s*$", formula, re.DOTALL)
    if or_match:
        inner = or_match.group(1)
        # Split on top-level commas (respecting nested parens)
        branches: list[list[tuple]] = []
        depth = 0
        current = ""
        for ch in inner:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if ch == "," and depth == 0:
                conds = _parse_conditions(current)
                if conds:
                    branches.append(conds)
                current = ""
            else:
                current += ch
        # Last segment
        conds = _parse_conditions(current)
        if conds:
            branches.append(conds)

        if not branches:
            logger.warning(
                "Could not parse OR formula for client-side filter: %s — returning all",
                formula,
            )
            return records

        filtered = []
        for record in records:
            fields = record.get("fields", {})
            if any(_match_conditions(fields, branch) for branch in branches):
                filtered.append(record)
        return filtered

    # AND semantics (original path)
    conditions = _parse_conditions(formula)

    if not conditions:
        logger.warning(
            "Could not parse formula for client-side filter: %s — returning all",
            formula,
        )
        return records

    filtered = []
    for record in records:
        fields = record.get("fields", {})
        if _match_conditions(fields, conditions):
            filtered.append(record)

    return filtered


# ── SyncBacklogClient — drop-in for BacklogClient ───────────────────


class SyncBacklogClient:
    """Thin wrapper around genlab-core BacklogClient.

    Previously a full re-implementation for eventlet safety. Now that we use
    psycopg3 (fully synchronous), we delegate entirely to BacklogClient which
    handles table name mapping, promoted columns, and connection pooling.
    """

    def __init__(self):
        use_postgres = os.getenv("GENLAB_USE_POSTGRES", "").lower() == "true"

        if use_postgres:
            from genlab_core.http.backlog_client import BacklogClient

            self._delegate = BacklogClient()
            # Mirror all table proxy attributes from the canonical client
            for attr in [
                "stories",
                "blueprints",
                "analytics",
                "publishing_analytics",
                "templates",
                "sources",
                "assets",
                "pending_engagement",
                "pending_feedback",
                "bandit_arms",
                "content_memory",
                "monetisation_progress",
                "ab_tests",
            ]:
                proxy = getattr(self._delegate, attr, None)
                if proxy is not None:
                    setattr(self, attr, proxy)
                else:
                    logger.warning(
                        "[SyncBacklogClient] BacklogClient missing attribute %r — skipping proxy mirror",
                        attr,
                    )
            self._pg = getattr(self._delegate, "_pg", None)
            logger.info("[SyncBacklogClient] Using BacklogClient (Postgres)")
        else:
            self._site_id = _get_site_id()
            config = _load_config()
            self.stories = SyncListProxy(
                self._site_id,
                config.get("Stories", {}).get("list_id", ""),
                "Stories",
            )
            self.blueprints = SyncListProxy(
                self._site_id,
                config.get("Blueprints", {}).get("list_id", ""),
                "Blueprints",
            )
            self.analytics = SyncListProxy(
                self._site_id,
                config.get("Analytics", {}).get("list_id", ""),
                "Analytics",
            )
            self.publishing_analytics = SyncListProxy(
                self._site_id,
                config.get("Publishing_Analytics", {}).get("list_id", ""),
                "Publishing_Analytics",
            )
            self.templates = SyncListProxy(
                self._site_id,
                config.get("Templates", {}).get("list_id", ""),
                "Templates",
            )
            self.sources = SyncListProxy(
                self._site_id,
                config.get("Sources", {}).get("list_id", ""),
                "Sources",
            )
            self.assets = SyncListProxy(
                self._site_id,
                config.get("Assets", {}).get("list_id", ""),
                "Assets",
            )
            self.pending_engagement = SyncListProxy(
                self._site_id,
                config.get("PendingEngagement", {}).get(
                    "list_id", "5d03ac7f-d0d6-4291-93e6-c61e310e92f3"
                ),
                "PendingEngagement",
            )
            self.monetisation_progress = SyncListProxy(
                self._site_id,
                config.get("MonetisationProgress", {}).get(
                    "list_id", "1a464f5e-fc95-4597-84ab-3fe6e7f4274a"
                ),
                "MonetisationProgress",
            )
            logger.info("[SyncBacklogClient] Using SharePoint backend")

    def get_blueprints_by_status(self, status: str, *, niche_id: str | None = None) -> list[dict]:
        formula = f"{{status}}='{status}'"
        if niche_id:
            formula = f"AND({{status}}='{status}',{{niche_id}}='{niche_id}')"
        return self.blueprints.all(formula=formula)


_sync_client: SyncBacklogClient | None = None
_sync_client_lock = threading.Lock()


def get_sync_client() -> SyncBacklogClient:
    """Get an eventlet-safe SharePoint client for the dashboard."""
    global _sync_client
    if _sync_client is None:
        with _sync_client_lock:
            if _sync_client is None:
                _sync_client = SyncBacklogClient()
    return _sync_client
