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


def _effective_per_day_cap(niche_id: str, platform: str = "instagram") -> int:
    """Resolve the per-day cap for (niche, platform) — the SAME number
    ``DailyCapEnforcer.can_publish`` will enforce at publish-time.

    Defaults to 1 under R-09's "1 reel/channel/day" rule; rises only
    when the operator opts in via ``multi_publish.enabled: true`` AND
    the platform is allowlisted. Defensively falls back to cap=1 on
    ANY failure — over-scheduling beyond the publisher's actual cap is
    the bug this helper exists to prevent, so a cap-lookup failure must
    NEVER raise the effective cap. (Mirrors the same defensive posture
    as ``DailyCapEnforcer._effective_cap``.)

    Why ``instagram`` is the default: the scheduler currently picks ONE
    ``scheduled_for`` time and all platforms publish from it; IG is the
    canonical platform whose cap drives scheduling. A future per-platform
    schedule extension would pass the actual target platform.
    """
    try:
        from genlab_core.publishing.daily_cap import _load_caps, _load_caps_config
        from genlab_core.scheduling.multi_publish_gate import (
            effective_cap,
            is_multi_publish_enabled,
        )

        caps_config = _load_caps_config()
        caps = _load_caps()
        daily_post_cap = int(caps.get(platform, 1))
        ceiling = int((caps_config.get("max_per_day_ceiling") or {}).get(platform, daily_post_cap))
        enabled = is_multi_publish_enabled(caps_config, platform=platform)
        return effective_cap(
            niche_id=niche_id,
            platform=platform,
            daily_post_cap=daily_post_cap,
            max_per_day_ceiling=ceiling,
            multi_publish_enabled=enabled,
        )
    except Exception as exc:
        logger.warning(
            "[QUEUE] effective-cap lookup failed for %s/%s — falling back to 1/day: %s",
            niche_id,
            platform,
            exc,
        )
        return 1


def mark_cap_violations(records: list[dict[str, Any]]) -> None:
    """Tag historically-overscheduled blueprints with ``cap_violation: True``.

    The 2026-06-15 scheduler fix prevents NEW packing-beyond-cap, but
    historical records (FrameDrift on Jun 15, etc.) carry stranded
    ``scheduled_for`` values pointing at slots the publisher will skip.
    This helper groups records by (niche_id, local-date) and tags the
    2nd, 3rd, ... post in each bucket — preserving the earliest one as
    the only post that will actually publish that day.

    Mutates records in place. Skips records without ``scheduled_for`` or
    ``niche_id``. Best-effort: any parse/lookup failure on one record
    leaves it unmarked rather than failing the batch.

    Frontend reads ``cap_violation`` and renders a warning badge so the
    operator knows which posts will silently skip at publish-time.
    """
    if not records:
        return

    # Group eligible records by (niche, local-date) → list of (scheduled_dt, record).
    # IST is the canonical local zone (matches the scheduler's tz default).
    buckets: dict[tuple[str, str], list[tuple[datetime, dict[str, Any]]]] = {}
    for r in records:
        fields = r.get("fields", r)
        niche_id = (fields.get("niche_id") or "").strip()
        sched_raw = fields.get("scheduled_for", "")
        if not niche_id or not sched_raw:
            continue
        try:
            dt = datetime.fromisoformat(str(sched_raw).replace("Z", "+00:00"))
            dt_local = dt.astimezone(IST)
        except (ValueError, TypeError):
            continue
        key = (niche_id, dt_local.strftime("%Y-%m-%d"))
        buckets.setdefault(key, []).append((dt_local, r))

    # For each bucket, sort by scheduled time and tag everything past
    # the cap as a violation. Earliest-first keeps the post the
    # publisher will actually fire as the clean one.
    cap_cache: dict[str, int] = {}
    for (niche_id, _day), entries in buckets.items():
        if niche_id not in cap_cache:
            cap_cache[niche_id] = _effective_per_day_cap(niche_id)
        cap = cap_cache[niche_id]
        if len(entries) <= cap:
            continue
        entries.sort(key=lambda x: x[0])
        for _dt, record in entries[cap:]:
            # Mark at the top level so the existing flat-serialization
            # at ``_transform_media({"id": ..., **fields})`` carries it
            # through to the frontend without a schema change.
            target = record.get("fields", record)
            target["cap_violation"] = True


def _next_available_slot(niche_id: str = "", exclude_record_id: str = "") -> str | None:
    """Return the next available publish slot as an ISO 8601 string.

    Reads schedule_slots from publishing.yaml. Enforces TWO collision
    invariants:

    1. **Per-(date, time, niche)**: no two posts from the same niche at
       the same exact slot.
    2. **Per-(date, niche) effective cap** (2026-06-15 fix): no more
       posts in one day than ``DailyCapEnforcer`` will actually publish.
       Without this, ``optimal_time_learner.top_n=3`` × yaml union could
       pack 4 candidates into one day. The publisher then silently
       skipped 3 of 4 at publish-time with ``[daily_cap] daily cap
       reached`` logs, leaving stranded ``scheduled_for`` values
       pointing at slots that never fired. Honors ``multi_publish``
       opt-in: when ``multi_publish.enabled: true`` for (niche,
       platform), the cap rises from 1 toward ``max_per_day_ceiling``
       (IG=3, YT=2) and packing is allowed up to that ceiling.

    Args:
        niche_id: If set, checks for per-niche collisions AND per-day cap.
        exclude_record_id: When re-scheduling an existing blueprint, pass its
            record id so its OWN ``scheduled_for`` is not treated as a
            collision. Without this, the scheduler would see the blueprint
            blocking its own slot and push it 1 day later — the 2026-06-14
            "+1 day offset" bug. Niches whose pipeline runs after the
            06:30 UTC publish window get a tomorrow-slot pre-set by
            ``push_to_backlog``, and the operator's later approval would
            then re-schedule to day-after-tomorrow instead of tomorrow.
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
    yaml_slots = ig_cfg.get("schedule_slots", ["12:00"])
    tz_str = ig_cfg.get("timezone", cfg.get("timezone", "Asia/Kolkata"))
    tz = ZoneInfo(tz_str)

    # Task #33 (2026-06-13): consult the per-platform optimal-time learner
    # before falling back to the static yaml slots. The learner mines
    # analytics.composite for the engagement hour-of-day distribution and
    # returns the top-3 hours with Bayesian shrinkage to prevent tiny-n
    # outliers from winning. When the learner returns signal, we use its
    # slots; when it doesn't (cold start, DB unreachable, < 5 obs per
    # bucket), we keep the yaml default.
    #
    # Why instagram-only here: the scheduler today picks ONE
    # scheduled_for time and all platforms publish from that. A future
    # extension can produce per-platform schedules; until then, IG is
    # the canonical platform whose engagement curve drives the picker.
    slots: list[str] = list(yaml_slots)
    if niche_id:
        try:
            from genlab_core.scheduling.optimal_time_learner import (
                optimal_slots_hhmm,
            )

            learned = optimal_slots_hhmm(niche_id, "instagram", timezone_str=tz_str)
            if learned:
                # Union learned + yaml as a safety net so a learner
                # regression can't strand us with zero slots. Order:
                # learned first (best signal), yaml after (fallback).
                seen: set[str] = set()
                slots = []
                for s in list(learned) + list(yaml_slots):
                    if s not in seen:
                        seen.add(s)
                        slots.append(s)
                logger.info(
                    "[QUEUE] Optimal-time learner picked %s for %s (yaml default was %s)",
                    learned,
                    niche_id,
                    yaml_slots,
                )
        except Exception as exc:
            # Never block scheduling on a learner failure — fall back to yaml.
            logger.warning("[QUEUE] Optimal-time learner failed: %s", exc)
            slots = list(yaml_slots)

    now_local = datetime.now(tz)
    # Must be at least 1 hour from now to allow finalization
    earliest = now_local + timedelta(hours=1)
    base_date = earliest.date()

    # Load existing scheduled blueprints for collision check.
    # ``occupied_slots`` keys per-(date,time,niche); ``posts_per_day``
    # counts per-(date,niche) so the per-day cap (2026-06-15) can
    # short-circuit BEFORE we walk every slot in the day.
    occupied_slots: set[str] = set()  # "YYYY-MM-DD HH:MM niche_id"
    posts_per_day: dict[str, int] = {}  # "YYYY-MM-DD" → count
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
                # Don't treat the blueprint being scheduled as a self-collision.
                # See ``exclude_record_id`` docstring for the +1 day bug this
                # prevents. Record id may live at the top of the row OR inside
                # fields depending on the backing store; check both.
                if exclude_record_id:
                    r_id = str(r.get("id", "") or f.get("id", "") or "")
                    if r_id == str(exclude_record_id):
                        continue
                sched_raw = f.get("scheduled_for", "")
                if not sched_raw:
                    continue
                try:
                    dt = datetime.fromisoformat(str(sched_raw).replace("Z", "+00:00"))
                    dt_local = dt.astimezone(tz)
                    key = f"{dt_local.strftime('%Y-%m-%d %H:%M')} {r_niche}"
                    occupied_slots.add(key)
                    day_key = dt_local.strftime("%Y-%m-%d")
                    posts_per_day[day_key] = posts_per_day.get(day_key, 0) + 1
                except (ValueError, TypeError):
                    pass
        except Exception as e:
            logger.warning("[QUEUE] Could not check slot collisions: %s", e)

    # Belt-and-suspenders: _effective_per_day_cap is itself defensive
    # (returns 1 on any failure), but wrap the call too so a regression
    # in the helper can never propagate up and crash slot picking.
    # Fail-closed at cap=1 keeps the R-09 "1/day" guarantee.
    if niche_id:
        try:
            effective_per_day_cap = _effective_per_day_cap(niche_id)
        except Exception as exc:
            logger.warning(
                "[QUEUE] _effective_per_day_cap raised unexpectedly for %s — using 1: %s",
                niche_id,
                exc,
            )
            effective_per_day_cap = 1
    else:
        effective_per_day_cap = 1

    for day_offset in range(0, 8):
        candidate_date = base_date + timedelta(days=day_offset)
        # Per-day cap short-circuit: skip the whole day if it would
        # over-schedule beyond what the publisher will accept.
        if niche_id:
            day_key = candidate_date.strftime("%Y-%m-%d")
            if posts_per_day.get(day_key, 0) >= effective_per_day_cap:
                logger.info(
                    "[QUEUE] Day %s at cap (%d/%d) for %s, trying next day",
                    day_key,
                    posts_per_day.get(day_key, 0),
                    effective_per_day_cap,
                    niche_id,
                )
                continue
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
                    # exclude self defensively even though existing_sched is
                    # empty here — keeps callers consistent.
                    next_slot = _next_available_slot(niche_id=niche_id, exclude_record_id=record_id)
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
