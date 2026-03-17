"""SharePointBackend — wraps existing GraphTableProxy instances.

This is the default backend that preserves all existing behavior.
BacklogClient's GraphTableProxy objects are passed in at construction time.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SharePointBackend:
    """Storage backend backed by Microsoft SharePoint Lists via Graph API.

    Wraps the existing GraphTableProxy objects so they satisfy the
    StorageBackend protocol without changing any existing code.
    """

    def __init__(self, proxies: dict) -> None:
        """Initialize with a mapping of table name -> GraphTableProxy.

        Args:
            proxies: Dict mapping table names (e.g. "blueprints") to
                     GraphTableProxy instances.
        """
        self._proxies = proxies

    def _proxy(self, table: str):
        """Return the proxy for a given table, raising KeyError if missing."""
        proxy = self._proxies.get(table)
        if not proxy:
            raise KeyError(f"No SharePoint proxy configured for table '{table}'")
        return proxy

    def create(self, table: str, record: Dict[str, Any]) -> str:
        """Create a record via GraphTableProxy.create(). Returns the record ID."""
        result = self._proxy(table).create(record)
        return result.get("id", "")

    def get(self, table: str, record_id: str) -> Optional[Dict[str, Any]]:
        """Get a single record by ID. Returns None if the proxy raises."""
        try:
            return self._proxy(table).get(record_id)
        except Exception:
            logger.debug("SharePointBackend.get(%s, %s) raised, returning None", table, record_id)
            return None

    def find(
        self,
        table: str,
        *,
        formula: str = "",
        niche_id: str = "",
    ) -> List[Dict[str, Any]]:
        """Find records using the legacy formula syntax.

        If niche_id is provided, appends a niche_id filter using the same
        _inject_niche_filter helper used by BacklogClient.
        """
        effective_formula = formula
        if niche_id:
            effective_formula = _inject_niche_filter(formula or None, niche_id)
        return self._proxy(table).all(formula=effective_formula or None)

    def update(
        self,
        table: str,
        record_id: str,
        fields: Dict[str, Any],
    ) -> None:
        """Update fields on an existing record via GraphTableProxy.update()."""
        self._proxy(table).update(record_id, fields)

    def delete(self, table: str, record_id: str) -> None:
        """Delete a record by ID via GraphTableProxy.delete()."""
        self._proxy(table).delete(record_id)

    def batch_create(
        self,
        table: str,
        records: List[Dict[str, Any]],
    ) -> List[str]:
        """Create multiple records. Returns list of new record IDs."""
        results = self._proxy(table).batch_create(records)
        return [r.get("id", "") for r in results]


def _inject_niche_filter(
    formula: str | None,
    niche_id: str | None,
    niche_field: str = "niche_id",
) -> str | None:
    """Append niche_id filter to existing formula.

    Duplicated here to avoid circular import with backlog_client.
    Uses the legacy formula syntax that GraphTableProxy.all() understands.
    """
    if not niche_id:
        return formula
    niche_clause = f"{{{niche_field}}}='{niche_id}'"
    if not formula:
        return niche_clause
    return f"AND({formula}, {niche_clause})"
