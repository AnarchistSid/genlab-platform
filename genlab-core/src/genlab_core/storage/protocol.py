"""StorageBackend protocol — abstract interface for data persistence.

All table operations go through this protocol so BacklogClient can
route to either SharePoint (via GraphTableProxy) or PostgreSQL
without changing its domain methods.

R-45: ``create()`` / ``batch_create()`` return types are honestly
typed as a union of ``str`` and ``dict[str, Any]``. The Postgres
backend returns a bare UUID string (the new row's id); SharePoint
returns the full record dict (Graph API's response shape). Consumers
MUST use :func:`id_from_create_result` to extract the id portably;
the helper handles both shapes. Prior to R-45 the protocol annotated
``-> dict[str, Any]`` regardless, which made the Postgres path's
return a quiet lie — the live code worked because every caller
happened to defensively isinstance-check, but the protocol itself
documented an untrue contract.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

# R-45: the honest return shape from ``create()`` / ``batch_create()``.
# Postgres returns the bare UUID string; SharePoint returns the full
# record dict (with an ``"id"`` key). The two cases are distinguished by
# :func:`id_from_create_result` at the seam.
CreateResult = str | dict[str, Any]


def id_from_create_result(result: CreateResult) -> str:
    """Extract the record id from a ``StorageBackend.create`` return.

    Centralizes the str-vs-dict handling that used to be scattered as
    inline ``isinstance(record, str)`` defensive checks (e.g.
    ``push_to_backlog.py:902``, ``analytics_store.py:301``). The
    union return shape is honest — both backends are correct — but
    the extraction has to live in exactly one place.

    Args:
        result: The value returned by ``backend.create(...)``.
                Either a bare UUID string (Postgres) or a record
                dict carrying an ``"id"`` key (SharePoint).

    Returns:
        The record id as a string.

    Raises:
        TypeError: When ``result`` is neither a str nor a dict.
        KeyError: When ``result`` is a dict with no ``"id"`` field.
    """
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        return result["id"]
    raise TypeError(
        f"Unexpected create() return: {type(result).__name__} (expected str or dict[str, Any])"
    )


@runtime_checkable
class StorageBackend(Protocol):
    """Unified CRUD interface for a single storage engine."""

    def find(
        self,
        table: str,
        *,
        formula: str | None = None,
        niche_id: str | None = None,
        max_records: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return records matching the filter.

        Args:
            table: Logical table name (e.g. "Stories", "Blueprints").
            formula: Legacy formula-syntax filter string.
            niche_id: Optional niche_id to inject into the filter.
            max_records: Maximum number of records to return.
        """
        ...

    def get(self, table: str, record_id: str) -> dict[str, Any]:
        """Fetch a single record by its ID."""
        ...

    def create(
        self,
        table: str,
        fields: dict[str, Any],
        *,
        typecast: bool = False,
    ) -> CreateResult:
        """Create a record. Returns the new id, in one of two shapes:

        * **Postgres**: a bare UUID string (``str``).
        * **SharePoint**: a full record dict carrying an ``"id"`` key.

        Use :func:`id_from_create_result` to extract the id portably —
        the inline isinstance dance is a R-45 smell, not a feature.
        """
        ...

    def update(
        self,
        table: str,
        record_id: str,
        fields: dict[str, Any],
        *,
        typecast: bool = False,
    ) -> dict[str, Any]:
        """Update a record's fields and return the updated record."""
        ...

    def delete(self, table: str, record_id: str) -> None:
        """Delete a record by ID."""
        ...

    def batch_create(
        self,
        table: str,
        records: list[dict[str, Any]],
    ) -> list[CreateResult]:
        """Create multiple records.

        Returns a list whose elements match :data:`CreateResult` —
        i.e. each element is either a UUID string (Postgres) or a
        full record dict (SharePoint). Iterate with
        :func:`id_from_create_result` to extract ids portably.
        """
        ...

    def batch_update(
        self,
        table: str,
        records: list[dict[str, Any]],
        **kwargs,
    ) -> list[dict[str, Any]]:
        """Update multiple records (each with 'id' and 'fields' keys)."""
        ...

    def upload_attachment(
        self,
        table: str,
        record_id: str,
        field_name: str,
        file_path: str,
    ) -> dict[str, Any] | None:
        """Upload a file attachment to a record field."""
        ...
