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

    def get(
        self,
        table: str,
        record_id: str,
        *,
        niche_id: str | None = None,  # noqa: ARG002 — accept-and-ignore (SR-A parity)
    ) -> dict[str, Any]:
        """SharePoint get — ``niche_id`` accepted for API parity.

        PR #532 (2026-06-24): same accept-and-ignore pattern as the
        create() kwarg shipped in PR #526 — backend-agnostic stores
        (BlueprintStore etc.) need to pass ``niche_id=`` uniformly
        on both backends without TypeError on the SharePoint side.
        """
        return self._proxy(table).get(record_id)

    def create(
        self,
        table: str,
        fields: dict[str, Any],
        *,
        typecast: bool = False,
        niche_id: str | None = None,  # noqa: ARG002 — accept-and-ignore (SR-C parity)
    ) -> dict[str, Any]:
        """SharePoint create — ``niche_id`` accepted for API parity.

        PR #526 (2026-06-24): SharePoint has no SR-C tenant-binding
        concept (multi-tenant SaaS is Postgres-only). The kwarg is
        declared so a backend-agnostic store (BlueprintStore, etc.)
        can pass ``niche_id=`` unconditionally without TypeError on
        the SharePoint path. The value is intentionally ignored —
        any tenant filtering on SharePoint happens via the existing
        SharePoint List ACLs, not via SET LOCAL.
        """
        return self._proxy(table).create(fields, typecast=typecast)

    def update(
        self,
        table: str,
        record_id: str,
        fields: dict[str, Any],
        *,
        typecast: bool = False,
        niche_id: str | None = None,  # noqa: ARG002 — accept-and-ignore (SR-A parity)
    ) -> dict[str, Any]:
        """SharePoint update — ``niche_id`` accepted for API parity (PR #532)."""
        return self._proxy(table).update(record_id, fields, typecast=typecast)

    def delete(
        self,
        table: str,
        record_id: str,
        *,
        niche_id: str | None = None,  # noqa: ARG002 — accept-and-ignore (SR-A parity)
    ) -> None:
        """SharePoint delete — ``niche_id`` accepted for API parity (PR #532)."""
        self._proxy(table).delete(record_id)

    def batch_create(
        self,
        table: str,
        records: list[dict[str, Any]],
        *,
        niche_id: str | None = None,  # noqa: ARG002 — accept-and-ignore (SR-C parity)
    ) -> list[dict[str, Any]]:
        """SharePoint batch_create — ``niche_id`` accepted for API parity.

        See :meth:`create` for the rationale — same shape, applied
        to the bulk path so the BlueprintStore batch migration
        (follow-up PR) can pass ``niche_id=`` unconditionally too.
        """
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
