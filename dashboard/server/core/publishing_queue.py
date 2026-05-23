"""Publishing Queue Manager — approve/hold gate for the Command Centre.

Maps the existing backlog fields (status + action_taken) into a clean
publishing lifecycle for the dashboard:

    PENDING_APPROVAL  →  status=VISUAL_READY, action_taken blank
    APPROVED          →  status=VISUAL_READY, action_taken=approved
    HELD              →  status=VISUAL_READY, action_taken=held
    PUBLISHING        →  transient (set during publish_all_platforms)
    PUBLISHED         →  status=PUBLISHED
    PUBLISH_FAILED    →  status=PUBLISH_FAILED, OR (status=VISUAL_READY, action_taken=approved, error present)

Gate invariant: nothing publishes unless action_taken == "approved".
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

# Virtual queue statuses derived from backlog field combinations
QUEUE_STATUS_PENDING = "PENDING_APPROVAL"
QUEUE_STATUS_APPROVED = "APPROVED"
QUEUE_STATUS_HELD = "HELD"
QUEUE_STATUS_PUBLISHED = "PUBLISHED"
QUEUE_STATUS_FAILED = "PUBLISH_FAILED"


def _get_client():
    from server.core.graph_sync import get_sync_client

    return get_sync_client()


def _lock_key(niche_id: str) -> int:
    """Deterministic, cross-process-stable advisory-lock key for a niche.

    R-22: the previous `hash(niche_id)` is salted per-process (PYTHONHASHSEED),
    so each gunicorn worker computed a DIFFERENT key for the same niche and the
    workers never contended on the lock — slot serialization silently never
    happened. sha256 is stable across processes, so all workers now block on the
    same key. (`pg_advisory_xact_lock` is a global named lock, so holding it on
    the lock connection across the `yield` correctly serializes the slot
    read-modify-write that runs on the pooled connection.)
    """
    import hashlib

    return int.from_bytes(hashlib.sha256(niche_id.encode()).digest()[:4], "big") & 0x7FFFFFFF


@contextlib.contextmanager
def _advisory_lock(niche_id: str) -> Generator[None, None, None]:
    """Acquire a PostgreSQL advisory lock scoped to niche_id.

    Prevents two concurrent approve() calls from picking the same
    publish slot for the same niche. The lock is transaction-scoped
    and released on commit/rollback.

    Falls through silently if Postgres is not configured (SharePoint mode).
    """
    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        yield
        return

    try:
        import psycopg  # noqa: F811 — only imported here to avoid top-level dep
    except ImportError:
        yield
        return

    lock_key = _lock_key(niche_id)
    try:
        with psycopg.connect(dsn) as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))
            yield
            conn.commit()
    except Exception as e:
        logger.warning("[QUEUE] Advisory lock failed (non-fatal): %s", e)
        yield


def _next_available_slot(niche_id: str = "") -> str | None:
    """Return the next available publish slot as an ISO 8601 string.

    Reads schedule_slots from publishing.yaml. Checks existing blueprints
    to avoid scheduling two posts from the same niche at the same slot.

    Args:
        niche_id: If set, checks for per-niche collisions (1 post per niche per day).
    """
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

    config_path = os.getenv("BACKLOG_CONFIG_PATH", "")
    if not config_path:
        return None

    pub_yaml = Path(config_path).parent / "publishing.yaml"
    if not pub_yaml.exists():
        # Try genlab-core config
        genlab_root = Path(config_path).parent.parent.parent
        pub_yaml = genlab_root / "genlab-core" / "config" / "publishing.yaml"
    if not pub_yaml.exists():
        return None

    try:
        with open(pub_yaml) as f:
            cfg = yaml.safe_load(f) or {}
    except Exception:
        return None

    # Support both flat (BB: instagram.schedule_slots) and nested (CW/SR/FD: platforms.instagram.schedule_slots)
    ig_cfg = cfg.get("instagram", {}) or cfg.get("platforms", {}).get("instagram", {})
    slots = ig_cfg.get("schedule_slots", ["12:00"])
    tz_str = ig_cfg.get("timezone", cfg.get("timezone", "Asia/Kolkata"))
    tz = ZoneInfo(tz_str)

    now_local = datetime.now(tz)
    # Must be at least 1 hour from now to allow finalization
    earliest = now_local + timedelta(hours=1)
    base_date = earliest.date()

    # Load existing scheduled blueprints for collision check
    occupied_slots: set[str] = set()  # "YYYY-MM-DD HH:MM niche_id"
    if niche_id:
        try:
            client = _get_client()
            records = client.blueprints.all(
                formula="OR({status}='VISUAL_READY',{status}='PUBLISHED')",
            )
            for r in records:
                f = r.get("fields", {})
                r_niche = (f.get("niche_id") or "").strip()
                if r_niche != niche_id:
                    continue
                sched_raw = f.get("scheduled_for", "")
                if not sched_raw:
                    continue
                try:
                    dt = datetime.fromisoformat(str(sched_raw).replace("Z", "+00:00"))
                    dt_local = dt.astimezone(tz)
                    key = f"{dt_local.strftime('%Y-%m-%d %H:%M')} {r_niche}"
                    occupied_slots.add(key)
                except (ValueError, TypeError):
                    pass
        except Exception as e:
            logger.warning("[QUEUE] Could not check slot collisions: %s", e)

    for day_offset in range(0, 8):
        candidate_date = base_date + timedelta(days=day_offset)
        for slot_str in slots:
            hour, minute = map(int, slot_str.split(":"))
            candidate = datetime(
                candidate_date.year,
                candidate_date.month,
                candidate_date.day,
                hour,
                minute,
                0,
                tzinfo=tz,
            )
            if candidate <= earliest:
                continue
            # Check niche collision
            if niche_id:
                key = f"{candidate.strftime('%Y-%m-%d %H:%M')} {niche_id}"
                if key in occupied_slots:
                    logger.info(
                        "[QUEUE] Slot %s occupied for %s, trying next",
                        slot_str,
                        niche_id,
                    )
                    continue
            return candidate.astimezone(UTC).isoformat().replace("+00:00", "Z")

    return None


def _fetch_blueprints_sync(status_filter: str = "VISUAL_READY") -> list[dict[str, Any]]:
    """Fetch blueprints via the shared sync Graph client.

    Delegates to SyncBacklogClient from graph_sync.py which handles
    credential management, token caching, and OData translation.
    """
    try:
        client = _get_client()
        formula = f"{{status}}='{status_filter}'"
        records = client.blueprints.all(formula=formula)
        logger.info("[QUEUE] Fetched %d %s blueprints (sync)", len(records), status_filter)
        return records
    except Exception as e:
        logger.error("[QUEUE] Failed to fetch %s blueprints: %s", status_filter, e)
        return []


def _update_blueprint_sync(record_id: str, fields: dict[str, Any]) -> None:
    """Update a blueprint's fields via the shared sync Graph client."""
    client = _get_client()
    client.blueprints.update(record_id, fields)


def _derive_queue_status(fields: dict[str, Any]) -> str:
    """Derive a virtual queue status from backlog status + action_taken."""
    status = (fields.get("status") or "").upper()
    action = (fields.get("action_taken") or "").lower().strip()
    publish_error = fields.get("error_message") or fields.get("error_log") or ""

    if status == "PUBLISHED":
        return QUEUE_STATUS_PUBLISHED
    if status == "PUBLISH_FAILED":
        return QUEUE_STATUS_FAILED
    if action == "approved" and publish_error:
        return QUEUE_STATUS_FAILED
    if action == "approved":
        return QUEUE_STATUS_APPROVED
    if action == "held":
        return QUEUE_STATUS_HELD
    return QUEUE_STATUS_PENDING


class PublishingQueueManager:
    """Facade over BacklogClient that enforces the approve/hold gate.

    All queue reads go through here so the dashboard gets a consistent
    view regardless of the underlying field layout.
    """

    def __init__(self, client=None):
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = _get_client()
        return self._client

    # ── Reads ──────────────────────────────────────────────────

    def get_queue(
        self,
        *,
        niche_id: str = "ai_creators",
        queue_status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Fetch publishing queue items with derived virtual status.

        Args:
            niche_id: Filter by niche (client-side, OData unreliable).
                Use "all" to return items from every niche.
            queue_status: Optional filter — one of PENDING_APPROVAL,
                APPROVED, HELD, PUBLISH_FAILED, PUBLISHED.
            limit: Max items to return.
        """
        # Use sync REST API to avoid eventlet + asyncio deadlock
        # Fetch VISUAL_READY, PUBLISHED, and PUBLISH_FAILED for full lifecycle view
        vr_records = _fetch_blueprints_sync("VISUAL_READY")
        pub_records = _fetch_blueprints_sync("PUBLISHED")
        failed_records = _fetch_blueprints_sync("PUBLISH_FAILED")

        # Merge and deduplicate by record ID
        seen_ids: set[str] = set()
        records: list[dict[str, Any]] = []
        for r in vr_records + pub_records + failed_records:
            rid = str(r.get("id", ""))
            if rid not in seen_ids:
                seen_ids.add(rid)
                records.append(r)

        # Client-side niche filter — skip when niche_id is "all"
        if niche_id != "all":
            records = [
                r
                for r in records
                if (r.get("fields", {}).get("niche_id") or "ai_creators") == niche_id
            ]

        items = []
        for r in records:
            fields = r.get("fields", {})
            item = {"id": r["id"], **fields}
            item["queue_status"] = _derive_queue_status(fields)
            items.append(item)

        if queue_status:
            items = [i for i in items if i["queue_status"] == queue_status]

        # Sort: PENDING first, then by priority_score desc
        status_rank = {
            QUEUE_STATUS_PENDING: 0,
            QUEUE_STATUS_HELD: 1,
            QUEUE_STATUS_APPROVED: 2,
            QUEUE_STATUS_FAILED: 3,
            QUEUE_STATUS_PUBLISHED: 4,
        }

        def _sort_key(item):
            rank = status_rank.get(item["queue_status"], 99)
            try:
                score = float(item.get("priority_score", 0) or 0)
            except (ValueError, TypeError):
                score = 0.0
            return (rank, -score)

        items.sort(key=_sort_key)
        return items[:limit]

    def get_stats(self, *, niche_id: str = "ai_creators") -> dict[str, int]:
        """Return counts per queue status for the stats bar."""
        items = self.get_queue(niche_id=niche_id, limit=500)
        stats = {
            "pending": 0,
            "approved": 0,
            "held": 0,
            "published": 0,
            "failed": 0,
            "total": len(items),
        }
        for item in items:
            qs = item["queue_status"]
            if qs == QUEUE_STATUS_PENDING:
                stats["pending"] += 1
            elif qs == QUEUE_STATUS_APPROVED:
                stats["approved"] += 1
            elif qs == QUEUE_STATUS_HELD:
                stats["held"] += 1
            elif qs == QUEUE_STATUS_PUBLISHED:
                stats["published"] += 1
            elif qs == QUEUE_STATUS_FAILED:
                stats["failed"] += 1
        return stats

    # ── Writes (gate enforcement) ─────────────────────────────

    def approve(
        self,
        record_id: str,
        *,
        notes: str = "",
        scheduled_for: str | None = None,
        niche_id: str = "",
    ) -> dict[str, Any]:
        """Approve a blueprint for publishing.

        Sets action_taken=approved so the publisher daemon will pick it up
        when the scheduled_for time arrives. If no scheduled_for is provided
        and the blueprint doesn't already have one, auto-assigns the next
        available slot that doesn't collide with another post from the same niche.
        """
        update: dict[str, Any] = {
            "action_taken": "approved",
            "reviewed_at": datetime.now(UTC).isoformat(),
        }
        if notes:
            update["review_notes"] = notes

        # Resolve niche_id if not provided
        if not niche_id:
            niche_id = self._get_niche_id(record_id)

        # Auto-schedule if caller didn't provide a slot.
        # Use an advisory lock to prevent two concurrent approvals from
        # picking the same slot for the same niche.
        if scheduled_for:
            update["scheduled_for"] = scheduled_for
            _update_blueprint_sync(record_id, update)
        else:
            with _advisory_lock(niche_id):
                existing_sched = self._get_scheduled_for(record_id)
                if not existing_sched:
                    next_slot = _next_available_slot(niche_id=niche_id)
                    if next_slot:
                        update["scheduled_for"] = next_slot
                        logger.info(
                            "[QUEUE] Auto-scheduled %s (%s) → %s",
                            record_id,
                            niche_id,
                            next_slot,
                        )
                _update_blueprint_sync(record_id, update)

        logger.info("[QUEUE] Approved %s (%s)", record_id, niche_id)
        return update

    def _get_niche_id(self, record_id: str) -> str:
        """Fetch niche_id for a blueprint from SharePoint."""
        try:
            record = self.client.blueprints.get(record_id)
            return (record.get("fields", {}).get("niche_id") or "").strip()
        except Exception:
            logger.debug("[QUEUE] Could not fetch niche_id for %s", record_id)
        return ""

    def _get_scheduled_for(self, record_id: str) -> str | None:
        """Check if a blueprint already has a scheduled_for value."""
        try:
            record = self.client.blueprints.get(record_id)
            val = record.get("fields", {}).get("scheduled_for", "")
            return val if val else None
        except Exception:
            logger.debug("[QUEUE] Could not check scheduled_for for %s", record_id)
        return None

    def hold(
        self,
        record_id: str,
        *,
        reason: str = "",
    ) -> dict[str, Any]:
        """Hold a blueprint — prevents it from being published.

        The publisher daemon skips items where action_taken != 'approved',
        so setting 'held' is sufficient to block publishing.
        """
        update: dict[str, Any] = {
            "action_taken": "held",
            "reviewed_at": datetime.now(UTC).isoformat(),
        }
        if reason:
            update["review_notes"] = reason
        _update_blueprint_sync(record_id, update)
        logger.info("[QUEUE] Held %s (reason: %s)", record_id, reason[:60])
        return update

    def release(
        self,
        record_id: str,
    ) -> dict[str, Any]:
        """Release a held blueprint back to PENDING_APPROVAL.

        Clears action_taken so it re-enters the review queue.
        """
        # Graph API requires null (not empty string) to clear fields
        update = {
            "action_taken": None,
            "review_notes": None,
            "reviewed_at": None,
        }
        _update_blueprint_sync(record_id, update)
        logger.info("[QUEUE] Released %s back to pending", record_id)
        return {"action_taken": "", "review_notes": "", "reviewed_at": ""}

    def is_publishable(self, record_id: str) -> bool:
        """Gate check: can this blueprint be published?

        Returns True only if action_taken == 'approved'.
        This is the single enforcement point for the publish gate.
        """
        try:
            record = self.client.blueprints.get(record_id)
            fields = record.get("fields", {})
            return (fields.get("action_taken") or "").lower().strip() == "approved"
        except Exception:
            logger.warning("[QUEUE] Could not verify publishability for %s", record_id)
            return False
