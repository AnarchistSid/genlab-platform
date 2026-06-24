"""Lifecycle-timestamp helpers for records returned by the storage layer.

Centralises the ``created_at`` / ``updated_at`` lookup pattern that any
consumer of ``BacklogClient.X.all()`` / ``find()`` / ``get()`` needs.

## Why a helper exists

``PostgresBackend._row_to_record`` (in ``postgres.py``) deliberately
puts the auto-managed lifecycle columns at the record's TOP LEVEL,
not inside ``fields``. The rationale lives in that function's docstring
— TL;DR: putting them in ``fields`` would round-trip them through
``_split_fields`` into the ``extra`` JSONB on every write, growing
the JSONB unboundedly and shadowing the authoritative SQL values.

This is a deliberate design choice, but it creates a footgun for any
caller that does the intuitive thing:

    >>> created = bp.get("fields", {}).get("created_at")   # ← wrong!

That returns ``None`` for every record returned by the Postgres backend
(the column was popped before ``fields`` was built). The trap is silent —
the calling code degrades to "missing date" handling, often with no
visible symptom. Tracked instances of this trap firing in production:

  * PR #500 → PR #501: URL-dedup TTL no-op (silently wrong filter)
  * PR #510 → PR #511: oldest-age computation no-op (silent missing
    banner suffix)

## The helper protocol

Both helpers below try the top-level key first (post-PR-#501 Postgres
shape), then fall back to ``fields.get(...)`` (legacy / JSON backend /
test fixtures). Returns ``None`` when the column genuinely isn't set
on either path.

``record_created_at`` / ``record_updated_at`` return the raw value
(usually an ISO 8601 string). ``record_created_at_dt`` /
``record_updated_at_dt`` parse to a timezone-aware ``datetime``,
returning ``None`` on missing / unparseable input.

## When NOT to use these helpers

  * For direct SQL ``cur.fetchall()`` rows (no ``_row_to_record``
    transformation has happened). Read ``row["created_at"]`` directly.
  * For Pydantic StoryCandidate or similar model_dump() output.
    Those carry the field as a normal attribute on the dict.

The helpers are specifically for records that came through the
``_row_to_record`` shape — primarily ``BacklogClient.*.all()`` results.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


def _read_lifecycle_field(record: dict[str, Any], field_name: str) -> Any:
    """Internal: read ``field_name`` with the top-level → fields fallback.

    Returns whatever the field's value is (string, datetime, None).
    Caller decides how to parse.

    Truthiness semantics: an EMPTY value at top level falls through to
    fields. Rationale — downstream consumers treat ``None`` and ``""``
    the same way (both indicate "no usable timestamp"), so an explicit
    empty string at top level shadowing a real value in fields would
    be a silent regression with no observable benefit. If a caller
    genuinely wants to distinguish "explicitly empty" from "absent",
    they can call ``record.get(...)`` directly and bypass the helper.
    """
    if not isinstance(record, dict):
        return None
    # Top-level first (Postgres backend post-PR-#501). Truthy check
    # rather than ``is not None`` so an empty-string at top level
    # falls through to fields.
    value = record.get(field_name)
    if value:
        return value
    # Fields fallback (legacy / JSON backend / test fixtures)
    fields = record.get("fields") or {}
    if isinstance(fields, dict):
        return fields.get(field_name) or None
    return None


def record_created_at(record: dict[str, Any]) -> Any:
    """Return the ``created_at`` value from a storage record, top-level first.

    Type-preserving — returns whatever the backend stored (typically an
    ISO 8601 string from the Postgres backend, or a raw ``datetime`` if
    a caller passed one in). Use ``record_created_at_dt`` when you need
    a parsed datetime.

    Returns ``None`` when the field is absent on both top-level and
    fields. Never raises.
    """
    return _read_lifecycle_field(record, "created_at")


def record_updated_at(record: dict[str, Any]) -> Any:
    """Mirror of ``record_created_at`` for the ``updated_at`` column."""
    return _read_lifecycle_field(record, "updated_at")


def _parse_iso_lenient(value: Any) -> datetime | None:
    """Parse an ISO 8601 string or pass-through a ``datetime``.

    Tolerant of the trailing-Z form (``"2026-06-24T00:00:00Z"``) that
    some backends emit. Returns ``None`` on missing / unparseable input.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        # E.g. ``date`` instances — accept by upgrading to datetime via str.
        try:
            value = str(value)
        except Exception:
            return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def record_created_at_dt(record: dict[str, Any]) -> datetime | None:
    """Return the ``created_at`` as a parsed ``datetime``, or ``None``.

    Convenience wrapper combining ``record_created_at`` +
    ``_parse_iso_lenient``. Use this when you need to compare ages
    (most common consumer pattern).
    """
    return _parse_iso_lenient(record_created_at(record))


def record_updated_at_dt(record: dict[str, Any]) -> datetime | None:
    """Mirror of ``record_created_at_dt`` for ``updated_at``."""
    return _parse_iso_lenient(record_updated_at(record))
