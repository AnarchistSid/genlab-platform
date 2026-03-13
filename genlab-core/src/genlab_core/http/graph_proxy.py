"""Microsoft Graph Lists table proxy — generic CRUD over SharePoint Lists.

Provides a standard record interface ({id, fields, createdTime}) on top of
the Microsoft Graph SDK for SharePoint Lists. Handles column name
translation (display ↔ internal), OData filter generation, pagination,
and field preparation.

The formula-to-OData translator supports the 8 legacy formula patterns
used across the codebase so existing callers don't need to learn OData.

Usage:
    from genlab_core.http.graph_proxy import GraphTableProxy
    proxy = GraphTableProxy(graph_client, site_id, list_id, "Stories")
    records = proxy.all(formula="{status}='INTAKE'", max_records=10)
"""
from __future__ import annotations

import logging
import re
import time as _time_mod
from pathlib import Path
from typing import Any, Dict, List, Optional

from genlab_core.http.async_bridge import run_async
from genlab_core.utils.text_sanitizer import sanitize_fields_for_graph_api

logger = logging.getLogger(__name__)

# Module-level column map cache — eliminates repeated API calls.
# Entries include a monotonic timestamp; entries older than TTL are refreshed.
_column_map_cache: Dict[str, tuple] = {}
_COLUMN_MAP_TTL = 3600  # 1 hour


# ── Legacy formula → OData $filter translator ────────────────────────


def _esc(value: str) -> str:
    """Escape single quotes in formula values."""
    return value.replace("'", "\\'") if value else ""


def formula_to_odata(formula: Optional[str]) -> Optional[str]:
    """Translate legacy formula syntax to OData $filter.

    Handles the 8 patterns used across the codebase:
      1. {field}='value'           → fields/field eq 'value'
      2. AND({f1}='v1', {f2}='v2') → fields/f1 eq 'v1' and fields/f2 eq 'v2'
      3. OR({f1}='v1', {f2}='v2')  → (fields/f1 eq 'v1' or fields/f2 eq 'v2')
      4. {enabled}=TRUE()          → fields/enabled eq 1
      5. AND(OR(...), {x}='y')     → nested combinations
      6. {date}>='2026-02-27'      → fields/date ge '2026-02-27'
      7. FIND('x', ARRAYJOIN({link})) → contains() filter
      8. {field}=BLANK()           → fields/field eq null
    """
    if not formula:
        return None

    formula = formula.strip()
    if not formula:
        return None

    # FIND(value, ARRAYJOIN({field}))
    find_match = re.match(
        r"FIND\('([^']*)',\s*ARRAYJOIN\(\{([^}]+)\}\)\)", formula
    )
    if find_match:
        val, field = find_match.groups()
        return f"contains(fields/{field}_text, '{_esc(val)}')"

    # FIND('value', {field})
    find_bare_match = re.match(
        r"FIND\('([^']*)',\s*\{([^}]+)\}\)", formula
    )
    if find_bare_match:
        val, field = find_bare_match.groups()
        return f"contains(fields/{field}, '{_esc(val)}')"

    return _translate_expr(formula)


def _translate_expr(expr: str) -> str:
    """Recursively translate a single expression."""
    expr = expr.strip()

    # DATESTR({field})='YYYY-MM-DD'
    datestr_match = re.match(
        r"^DATESTR\(\{([^}]+)\}\)\s*=\s*'(\d{4}-\d{2}-\d{2})'$", expr
    )
    if datestr_match:
        field, date_str = datestr_match.groups()
        from datetime import date, timedelta
        d = date.fromisoformat(date_str)
        next_day = (d + timedelta(days=1)).isoformat()
        return (
            f"fields/{field} ge '{date_str}T00:00:00Z'"
            f" and fields/{field} lt '{next_day}T00:00:00Z'"
        )

    # AND(...)
    and_match = re.match(r"^AND\((.+)\)$", expr, re.DOTALL)
    if and_match:
        inner = and_match.group(1)
        parts = _split_top_level(inner)
        translated = [_translate_expr(p) for p in parts]
        return " and ".join(translated)

    # OR(...)
    or_match = re.match(r"^OR\((.+)\)$", expr, re.DOTALL)
    if or_match:
        inner = or_match.group(1)
        parts = _split_top_level(inner)
        translated = [_translate_expr(p) for p in parts]
        return "(" + " or ".join(translated) + ")"

    # {field}=BLANK()
    blank_match = re.match(r"^\{([^}]+)\}\s*=\s*BLANK\(\)$", expr)
    if blank_match:
        field = blank_match.group(1)
        return f"fields/{field} eq null"

    # {field}=TRUE()
    true_match = re.match(r"^\{([^}]+)\}\s*=\s*TRUE\(\)$", expr)
    if true_match:
        field = true_match.group(1)
        return f"fields/{field} eq 1"

    # {field}=FALSE()
    false_match = re.match(r"^\{([^}]+)\}\s*=\s*FALSE\(\)$", expr)
    if false_match:
        field = false_match.group(1)
        return f"fields/{field} eq 0"

    # {field} op 'value' — allow escaped single quotes inside (e.g., it\'s)
    cmp_match = re.match(
        r"^\{([^}]+)\}\s*(=|!=|>=|<=|>|<)\s*'((?:[^'\\]|\\.)*)'$", expr
    ) or re.match(
        r"^\{([^}]+)\}\s*(=|!=|>=|<=|>|<)\s*([^',]+)$", expr
    )
    if cmp_match:
        field, op, value = cmp_match.groups()
        odata_ops = {
            "=": "eq", "!=": "ne", ">=": "ge",
            "<=": "le", ">": "gt", "<": "lt",
        }
        odata_op = odata_ops.get(op, "eq")

        # Numeric values don't need quotes in OData
        try:
            float(value)
            return f"fields/{field} {odata_op} {value}"
        except ValueError:
            pass

        return f"fields/{field} {odata_op} '{_esc(value)}'"

    return expr


def _split_top_level(s: str) -> List[str]:
    """Split a comma-separated string respecting nested parentheses."""
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
    if current:
        parts.append("".join(current).strip())
    return [p for p in parts if p]


# ── GraphTableProxy ──────────────────────────────────────────────────


class GraphTableProxy:
    """Microsoft Graph Lists table proxy with standard record interface.

    Every consumer that calls client.blueprints.all(formula=...) or
    client.stories.get(record_id) works unchanged.
    """

    def __init__(
        self,
        client,  # GraphServiceClient
        site_id: str,
        list_id: str,
        list_name: str = "",
    ):
        self._client = client
        self._site_id = site_id
        self._list_id = list_id
        self._list_name = list_name
        self._display_to_internal: Dict[str, str] = {}
        self._internal_to_display: Dict[str, str] = {}
        self._load_column_map()

    def _load_column_map(self) -> None:
        """Load column display->internal name mapping from Graph."""
        cached = _column_map_cache.get(self._list_id)
        if cached and len(cached) >= 3:
            d2i, i2d, ts = cached
            if (_time_mod.monotonic() - ts) < _COLUMN_MAP_TTL:
                self._display_to_internal = dict(d2i)
                self._internal_to_display = dict(i2d)
                return

        async def _fetch():
            cols = await (
                self._client.sites.by_site_id(self._site_id)
                .lists.by_list_id(self._list_id)
                .columns.get()
            )
            return cols

        try:
            cols_response = run_async(_fetch())
            for col in (cols_response.value or []):
                display = col.display_name or ""
                internal = col.name or ""
                if display and internal:
                    self._display_to_internal[display] = internal
                    self._internal_to_display[internal] = display
            _column_map_cache[self._list_id] = (
                dict(self._display_to_internal),
                dict(self._internal_to_display),
                _time_mod.monotonic(),
            )
        except Exception as exc:
            logger.warning(
                "Failed to load column map for %s: %s", self._list_name, exc,
            )

    def _to_internal_name(self, display_name: str) -> str:
        if display_name in self._internal_to_display:
            return display_name
        return self._display_to_internal.get(display_name, display_name)

    def _to_display_name(self, internal_name: str) -> str:
        return self._internal_to_display.get(internal_name, internal_name)

    def _translate_filter_names(self, odata_filter: str) -> str:
        """Replace display names with internal names in OData filter strings."""
        def _replace_field(match):
            field_name = match.group(1)
            internal = self._display_to_internal.get(field_name, field_name)
            return f"fields/{internal}"
        return re.sub(r"fields/(\w+)", _replace_field, odata_filter)

    # SharePoint metadata fields to strip from API responses
    _METADATA_FIELDS = frozenset({
        "@odata.etag", "_UIVersionString", "Attachments",
        "AppAuthorLookupId", "AppEditorLookupId",
        "AuthorLookupId", "EditorLookupId",
        "Content Type", "ContentType", "ContentTypeId",
        "ComplianceAssetId", "GUID", "OData__ColorTag",
        "OData__UIVersionString", "Edit",
        "_ComplianceFlags", "_ComplianceTag",
        "_ComplianceTagWrittenTime", "_ComplianceTagUserId",
        "_IsRecord",
        "Created", "Modified", "Version",
        "Folder Child Count", "Item Child Count",
        "Label applied by", "Label setting",
        "Retention label", "Retention label Applied",
    })

    _FIELD_ALIASES = {
        "hook": "hook_text",
    }

    def _to_record(self, item) -> Dict[str, Any]:
        """Convert a Graph ListItem to standard record shape."""
        raw_fields = {}
        if hasattr(item, "fields") and item.fields:
            ad = getattr(item.fields, "additional_data", {}) or {}
            raw_fields = dict(ad)

        fields = {}
        for key, value in raw_fields.items():
            display = self._to_display_name(key)
            if display in self._METADATA_FIELDS or display.startswith("@odata."):
                continue
            fields[display] = value

        for old_name, new_name in self._FIELD_ALIASES.items():
            if old_name in fields and new_name not in fields:
                fields[new_name] = fields.pop(old_name)

        link_fields = {}
        for key in list(fields.keys()):
            if key.endswith("_link"):
                base_key = key[:-5]
                val = fields.pop(key, "")
                link_fields[base_key] = [str(val)] if val else []
        fields.update(link_fields)

        created = ""
        if hasattr(item, "created_date_time") and item.created_date_time:
            created = item.created_date_time.isoformat()

        return {
            "id": str(item.id),
            "fields": fields,
            "createdTime": created,
        }

    # ── CRUD methods ─────────────────────────────────────────────────

    def all(
        self,
        formula: Optional[str] = None,
        max_records: Optional[int] = None,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        return run_async(self._all_async(formula, max_records))

    async def _all_async(
        self,
        formula: Optional[str],
        max_records: Optional[int],
    ) -> List[Dict[str, Any]]:
        odata_filter = formula_to_odata(formula)
        original_formula = formula
        if odata_filter:
            odata_filter = self._translate_filter_names(odata_filter)

        items_req = (
            self._client.sites.by_site_id(self._site_id)
            .lists.by_list_id(self._list_id)
            .items
        )

        from msgraph.generated.sites.item.lists.item.items.items_request_builder import (
            ItemsRequestBuilder,
        )

        query = ItemsRequestBuilder.ItemsRequestBuilderGetQueryParameters(
            expand=["fields"],
        )
        if odata_filter:
            query.filter = odata_filter
        if max_records:
            query.top = max_records

        config = ItemsRequestBuilder.ItemsRequestBuilderGetRequestConfiguration(
            query_parameters=query,
        )
        config.headers.add("Prefer", "HonorNonIndexedQueriesWarningMayFailRandomly")

        try:
            result = await items_req.get(request_configuration=config)
        except Exception as exc:
            if original_formula:
                logger.warning(
                    "OData filter failed for %s, falling back to client-side: %s (%s)",
                    self._list_name, odata_filter, exc,
                )
                return await self._all_with_client_filter(original_formula, max_records)
            logger.error(
                "Graph list query failed (%s filter=%s): %s",
                self._list_name, odata_filter, exc,
            )
            return []

        if not result or not result.value:
            if not odata_filter:
                logger.warning(
                    "SAFETY: Unfiltered query for %s returned 0 records.",
                    self._list_name,
                )
            return []

        records = [self._to_record(item) for item in result.value]

        next_link = getattr(result, "odata_next_link", None)
        while next_link and (max_records is None or len(records) < max_records):
            try:
                page_config = ItemsRequestBuilder.ItemsRequestBuilderGetRequestConfiguration()
                page_config.headers.add(
                    "Prefer", "HonorNonIndexedQueriesWarningMayFailRandomly",
                )
                result = await items_req.with_url(next_link).get(
                    request_configuration=page_config,
                )
                if result and result.value:
                    records.extend(self._to_record(item) for item in result.value)
                    next_link = getattr(result, "odata_next_link", None)
                else:
                    break
            except Exception as exc:
                logger.warning(
                    "Pagination failed for %s (got %d records): %s",
                    self._list_name, len(records), exc,
                )
                break

        if max_records:
            records = records[:max_records]

        return records

    async def _all_with_client_filter(
        self,
        formula: str,
        max_records: Optional[int],
    ) -> List[Dict[str, Any]]:
        """Fetch all records and filter client-side (fallback)."""
        all_records = await self._all_async(formula=None, max_records=None)

        conditions = []

        for match in re.finditer(
            r"DATESTR\(\{([^}]+)\}\)\s*=\s*'(\d{4}-\d{2}-\d{2})'",
            formula,
        ):
            field, date_str = match.groups()
            conditions.append((field, ("__datestr__", date_str)))

        for match in re.finditer(
            r"\{([^}]+)\}\s*=\s*(?:'([^']*)'|\"([^\"]*)\"|TRUE\(\)|FALSE\(\))",
            formula,
        ):
            field = match.group(1)
            if match.group(2) is not None:
                value = match.group(2)
            elif match.group(3) is not None:
                value = match.group(3)
            elif "TRUE()" in match.group(0):
                value = True
            else:
                value = False
            conditions.append((field, value))

        if not conditions:
            logger.warning(
                "SAFETY: Could not parse formula for client-side filter: %s — "
                "returning EMPTY to prevent unfiltered results.",
                formula,
            )
            return []

        filtered = []
        for record in all_records:
            fields = record.get("fields", {})
            match = True
            for field_name, expected_value in conditions:
                actual = fields.get(field_name)
                if isinstance(expected_value, tuple) and expected_value[0] == "__datestr__":
                    if not actual or not str(actual).startswith(expected_value[1]):
                        match = False
                        break
                elif isinstance(expected_value, bool):
                    if actual != expected_value:
                        match = False
                        break
                else:
                    if str(actual) != str(expected_value):
                        match = False
                        break
            if match:
                filtered.append(record)

        if max_records:
            filtered = filtered[:max_records]

        return filtered

    def get(self, record_id: str) -> Dict[str, Any]:
        return run_async(self._get_async(record_id))

    async def _get_async(self, record_id: str) -> Dict[str, Any]:
        from msgraph.generated.sites.item.lists.item.items.item.list_item_item_request_builder import (
            ListItemItemRequestBuilder,
        )

        query = ListItemItemRequestBuilder.ListItemItemRequestBuilderGetQueryParameters(
            expand=["fields"],
        )
        config = ListItemItemRequestBuilder.ListItemItemRequestBuilderGetRequestConfiguration(
            query_parameters=query,
        )

        item = await (
            self._client.sites.by_site_id(self._site_id)
            .lists.by_list_id(self._list_id)
            .items.by_list_item_id(record_id)
            .get(request_configuration=config)
        )
        return self._to_record(item)

    def create(self, fields: Dict[str, Any], typecast: bool = False) -> Dict[str, Any]:
        return run_async(self._create_async(fields))

    async def _create_async(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        from msgraph.generated.models.field_value_set import FieldValueSet
        from msgraph.generated.models.list_item import ListItem

        clean_fields = self._prepare_fields(fields)
        body = ListItem(fields=FieldValueSet(additional_data=clean_fields))
        item = await (
            self._client.sites.by_site_id(self._site_id)
            .lists.by_list_id(self._list_id)
            .items.post(body)
        )
        return await self._get_async(str(item.id))

    def update(
        self, record_id: str, fields: Dict[str, Any], typecast: bool = False
    ) -> Dict[str, Any]:
        return run_async(self._update_async(record_id, fields))

    async def _update_async(
        self, record_id: str, fields: Dict[str, Any]
    ) -> Dict[str, Any]:
        from msgraph.generated.models.field_value_set import FieldValueSet

        clean_fields = self._prepare_fields(fields)
        body = FieldValueSet(additional_data=clean_fields)
        await (
            self._client.sites.by_site_id(self._site_id)
            .lists.by_list_id(self._list_id)
            .items.by_list_item_id(record_id)
            .fields.patch(body)
        )
        return await self._get_async(record_id)

    def delete(self, record_id: str) -> None:
        run_async(self._delete_async(record_id))

    async def _delete_async(self, record_id: str) -> None:
        await (
            self._client.sites.by_site_id(self._site_id)
            .lists.by_list_id(self._list_id)
            .items.by_list_item_id(record_id)
            .delete()
        )

    def batch_create(
        self, records: List[Dict[str, Any]], **kwargs
    ) -> List[Dict[str, Any]]:
        return run_async(self._batch_create_async(records))

    async def _batch_create_async(
        self, records: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        results = []
        for fields in records:
            try:
                record = await self._create_async(fields)
                results.append(record)
            except Exception as exc:
                logger.error("Batch create item failed: %s", exc)
        return results

    def batch_update(
        self, records: List[Dict[str, Any]], **kwargs
    ) -> List[Dict[str, Any]]:
        return run_async(self._batch_update_async(records))

    async def _batch_update_async(
        self, records: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        results = []
        for rec in records:
            record_id = rec.get("id", "")
            fields = rec.get("fields", {})
            if not record_id:
                continue
            try:
                updated = await self._update_async(str(record_id), fields)
                results.append(updated)
            except Exception as exc:
                logger.error("Batch update item %s failed: %s", record_id, exc)
        return results

    def upload_attachment(
        self, record_id: str, field_name: str, file_path: str
    ) -> Optional[Dict]:
        return run_async(self._upload_async(record_id, field_name, file_path))

    async def _upload_async(
        self, record_id: str, field_name: str, file_path: str
    ) -> Optional[Dict]:
        path = Path(file_path)
        if not path.exists():
            logger.warning("File not found for upload: %s", path)
            return None

        try:
            with open(path, "rb") as f:
                content = f.read()

            await (
                self._client.sites.by_site_id(self._site_id)
                .lists.by_list_id(self._list_id)
                .items.by_list_item_id(record_id)
                .drive_item.content.put(content)
            )
            return {"filename": path.name, "size": len(content)}
        except Exception as exc:
            logger.warning("Attachment upload error for %s: %s", path.name, exc)
            return None

    # Sentinel to explicitly clear a field
    CLEAR = object()

    def _prepare_fields(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        """Convert record field shapes to Graph-compatible fields."""
        clean = {}
        for key, value in fields.items():
            if value is self.CLEAR:
                clean[key] = None
                continue
            if value is None:
                continue

            if isinstance(value, list) and key in (
                "story", "template", "blueprint", "blueprint_link",
            ):
                if value:
                    clean[f"{key}_link"] = str(value[0])
                continue

            if isinstance(value, list) and key == "file":
                if value and isinstance(value[0], dict):
                    clean["file"] = value[0].get("url", "")
                continue

            if isinstance(value, list):
                clean[key] = ", ".join(str(v) for v in value)
                continue

            if isinstance(value, bool):
                clean[key] = value
                continue

            clean[key] = value

        reverse_aliases = {v: k for k, v in self._FIELD_ALIASES.items()}
        de_aliased = {}
        for key, value in clean.items():
            de_aliased[reverse_aliases.get(key, key)] = value

        mapped = {}
        for key, value in de_aliased.items():
            internal = self._to_internal_name(key)
            mapped[internal] = value

        # Sanitize all string values for Graph API compatibility
        # (strips non-BMP emoji, replaces curly quotes with ASCII)
        mapped = sanitize_fields_for_graph_api(mapped)

        return mapped
