"""Pin the JSON-serializer fix in ``write_alerts_to_db``.

The 2026-06-14 prod log probe surfaced ``ERROR: Failed to write
alerts to DB: Object of type UUID is not JSON serializable`` firing
on every hourly health_monitor run (16+ occurrences in the visible
window). The culprit was the default ``json.dumps(alert.details)``
call: ``alert.details`` is ``dict[str, Any]`` and ``json.dumps``
rejects UUIDs, datetimes, and other non-stdlib JSON types.

After the fix, the writer passes ``default=_alert_details_json_default``
to ``json.dumps``, which coerces those types to their canonical
string form so the whole alert lands instead of being dropped.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from genlab_core.monitoring.health_monitor import _alert_details_json_default


class TestAlertDetailsJsonDefault:
    def test_uuid_coerces_to_str(self):
        """UUID → str via str(UUID). This is the specific shape that
        caused the prod failures — every alert that included a
        record-id UUID in its details was being dropped."""
        u = uuid4()
        out = _alert_details_json_default(u)
        assert out == str(u)
        # And round-trip through json.dumps with the default
        assert json.loads(json.dumps({"id": u}, default=_alert_details_json_default)) == {
            "id": str(u)
        }

    def test_datetime_coerces_to_isoformat(self):
        """datetime → isoformat (also a common cause of the same
        TypeError class). Pinning the helper for both shapes so the
        UUID-specific fix doesn't leave the datetime gap open for
        the next investigator to re-discover."""
        dt = datetime.now(UTC)
        out = _alert_details_json_default(dt)
        assert out == dt.isoformat()

    def test_date_coerces_to_isoformat(self):
        """date object (not datetime) — same coercion path."""
        d = date(2026, 6, 14)
        out = _alert_details_json_default(d)
        assert out == "2026-06-14"

    def test_alert_with_uuid_in_details_round_trips(self):
        """End-to-end: ``json.dumps(alert.details, default=...)``
        must succeed on a typical alert payload with a UUID inside
        the details dict, not raise TypeError."""
        details = {
            "blueprint_id": uuid4(),
            "archived_count": 5,
            "reason": "stale",
        }
        # MUST NOT raise
        payload = json.dumps(details, default=_alert_details_json_default)
        parsed = json.loads(payload)
        assert parsed["archived_count"] == 5
        assert parsed["reason"] == "stale"
        # UUID came back as string
        UUID(parsed["blueprint_id"])  # would raise if not a valid UUID string

    def test_unknown_type_does_not_crash(self):
        """The last-resort branch: any other type gets repr()'d so
        we still land a row with degraded detail rather than
        dropping the whole alert. The actionable signal lives in
        check_name + message + severity; details is enrichment."""

        class Weird:
            def __repr__(self):
                return "<Weird instance>"

        out = _alert_details_json_default(Weird())
        assert out == "<Weird instance>"
