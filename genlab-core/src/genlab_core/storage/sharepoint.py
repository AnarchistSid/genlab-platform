"""SharePoint storage backend — wraps GraphTableProxy for the StorageBackend protocol.

This is the compatibility layer that lets BacklogClient route calls through
the StorageBackend interface while still using the existing GraphTableProxy
under the hood for SharePoint Lists.
"""

from __future__ import annotations

import logging
from typing import Any

from genlab_core.http.graph_proxy import GraphTableProxy

logger = logging.getLogger(__name__)


class SharePointBackend:
    """StorageBackend implementation backed by SharePoint Lists via GraphTableProxy.

    Each logical table maps to a GraphTableProxy instance that was already
    initialised by BacklogClient.__init__.
    """

    def __init__(self, proxies: dict[str, GraphTableProxy]) -> None:
        """Initialise with a mapping of table_name -> GraphTableProxy.

        Args:
            proxies: Dict mapping canonical table names (e.g. "Stories") to
                their GraphTableProxy instances.
        """
        self._proxies = proxies

    def _proxy(self, table: str) -> GraphTableProxy:
        proxy = self._proxies.get(table)
        if proxy is None:
            raise ValueError(
                f"SharePointBackend: no proxy configured for table '{table}'. "
                f"Available: {sorted(self._proxies.keys())}"
            )
        return proxy

    def find(
        self,
        table: str,
        *,
        formula: str | None = None,
        niche_id: str | None = None,
        max_records: int | None = None,
    ) -> list[dict[str, Any]]:
        from genlab_core.http.backlog_client import _inject_niche_filter

        formula = _inject_niche_filter(formula, niche_id)
        return self._proxy(table).all(formula=formula, max_records=max_records)

    def get(self, table: str, record_id: str) -> dict[str, Any]:
        return self._proxy(table).get(record_id)

    def create(
        self,
        table: str,
        fields: dict[str, Any],
        *,
        typecast: bool = False,
    ) -> dict[str, Any]:
        return self._proxy(table).create(fields, typecast=typecast)

    def update(
        self,
        table: str,
        record_id: str,
        fields: dict[str, Any],
        *,
        typecast: bool = False,
    ) -> dict[str, Any]:
        return self._proxy(table).update(record_id, fields, typecast=typecast)

    def delete(self, table: str, record_id: str) -> None:
        self._proxy(table).delete(record_id)

    def batch_create(
        self,
        table: str,
        records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return self._proxy(table).batch_create(records)

    def batch_update(
        self,
        table: str,
        records: list[dict[str, Any]],
        **kwargs,
    ) -> list[dict[str, Any]]:
        return self._proxy(table).batch_update(records, **kwargs)

    def upload_attachment(
        self,
        table: str,
        record_id: str,
        field_name: str,
        file_path: str,
    ) -> dict[str, Any] | None:
        return self._proxy(table).upload_attachment(record_id, field_name, file_path)
