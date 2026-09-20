"""The media store a scheduled blueprint's reel actually lives in.

THE TWO CLOCKS. Run directories prune to the last three runs; the publish queue
schedules a week out. Nothing reconciled them, so an approved reel's media had a
lifespan measured in pipeline activity rather than in days. Two ai_creators
blueprints lost theirs and jammed the publisher for three days before anyone
looked, and 27 more were on the same clock.

APPROVED MEANS DURABLE. A blueprint may not be approved with media the store
does not hold: the copy happens on the accept path, in the same update as the
status change, and a copy failure leaves the blueprint unapproved with a reason.
The run directory is left exactly as found — this copies, never moves — so
cleanup keeps its own policy and nothing scheduled depends on it.

The emergency backfill (`scripts/media_backfill.py`) and the accept path share
`copy_for_schedule`; it is not described twice.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

DURABLE_ROOT = Path("/opt/genlab/.media/scheduled")


class MediaCopyError(RuntimeError):
    """The durable copy did not happen. The caller must NOT approve."""


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def parse_paths(fields: dict) -> list[str]:
    """`visual_paths` is stored as a JSON string. One reader, not five."""
    raw = fields.get("visual_paths")
    if isinstance(raw, str):
        raw = json.loads(raw) if raw.strip().startswith("[") else [raw]
    return [str(p) for p in (raw or []) if str(p).strip()]


def is_durable(fields: dict, root: Path | None = None) -> bool:
    root = root or DURABLE_ROOT
    paths = parse_paths(fields)
    return bool(paths) and all(str(p).startswith(str(root)) for p in paths)


def copy_for_schedule(record_id: str, fields: dict, root: Path | None = None) -> list[str]:
    """Copy this blueprint's media into the durable store; return the new paths.

    Raises MediaCopyError if anything is missing or a hash disagrees — the
    caller must treat that as "do not approve", never as "approve anyway".
    """
    root = root or DURABLE_ROOT
    if is_durable(fields, root):
        return parse_paths(fields)

    srcs = [Path(p) for p in parse_paths(fields)]
    if not srcs:
        raise MediaCopyError("blueprint has no visual_paths")
    live = [p for p in srcs if p.exists()]
    if not live:
        raise MediaCopyError(f"none of {len(srcs)} visual_paths exist on disk")

    dest = root / record_id
    dest.mkdir(parents=True, exist_ok=True)
    out: list[str] = []
    for src in live:
        tgt = dest / src.name
        shutil.copy2(src, tgt)
        # Verified BEFORE the record is touched. A truncated copy that is never
        # checked is worse than no copy: it publishes as a broken reel.
        if _sha256(src) != _sha256(tgt):
            raise MediaCopyError(f"sha256 mismatch for {src.name}")
        out.append(str(tgt))
    logger.info("[durable-media] %s: %d file(s) -> %s", record_id[:8], len(out), dest)
    return out


def release(record_id: str, root: Path | None = None) -> bool:
    """Drop the durable copy. Called once the reel is published or archived.

    Fail-open: a store that cannot be cleaned must not stop a publish from
    being recorded. The orphan sweep catches what this misses.
    """
    root = root or DURABLE_ROOT
    dest = root / record_id
    if not dest.exists():
        return False
    try:
        shutil.rmtree(dest)
        logger.info("[durable-media] released %s", record_id[:8])
        return True
    except OSError as exc:
        logger.warning("[durable-media] could not release %s: %s", record_id[:8], exc)
        return False


def orphans(live_record_ids: set[str], root: Path | None = None) -> list[Path]:
    """Directories with no live scheduled blueprint. Reported, then pruned."""
    root = root or DURABLE_ROOT
    if not root.exists():
        return []
    return [d for d in root.iterdir() if d.is_dir() and d.name not in live_record_ids]
