# Microsoft Lists Backup & Restore System

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent data loss by creating daily local JSON backups of all 8 Microsoft Lists tables, with tested restore capability and a pre-pipeline sanity check that halts if record counts drop unexpectedly.

**Architecture:** A `backup_lists.py` script dumps all tables to timestamped JSON files in `.tmp/backups/`. A companion `restore_lists.py` re-creates records from any backup. A lightweight `preflight_check.py` compares current record counts against the latest backup and aborts if any table lost >20% of records. The backup runs as the first step of `daily_intel.sh` (before any mutations).

**Tech Stack:** BacklogClient (existing), JSON, argparse, cron integration via `daily_intel.sh`

---

### Task 1: Backup Script — Core Dump

**Files:**
- Create: `execution/backup_lists.py`
- Test: `tests/test_backup_restore.py`

**Step 1: Write the failing test for backup**

```python
# tests/test_backup_restore.py
"""Tests for backup and restore of Microsoft Lists data."""
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_client():
    from execution.utils.backlog_client import BacklogClient
    client = BacklogClient.__new__(BacklogClient)
    for table in (
        "stories", "blueprints", "templates", "assets",
        "sources", "publishing_analytics", "analytics", "ab_tests",
    ):
        setattr(client, table, MagicMock())
    return client


@pytest.fixture
def sample_records():
    return [
        {"id": "1", "fields": {"title": "Story A", "status": "DRAFTED"}},
        {"id": "2", "fields": {"title": "Story B", "status": "PUBLISHED"}},
    ]


class TestBackup:
    def test_backup_creates_directory_and_json_files(self, tmp_path, mock_client, sample_records):
        """Backup should create one JSON file per table in a timestamped dir."""
        mock_client.stories.all.return_value = sample_records
        mock_client.blueprints.all.return_value = []
        mock_client.templates.all.return_value = []
        mock_client.assets.all.return_value = []
        mock_client.sources.all.return_value = []
        mock_client.publishing_analytics.all.return_value = []
        mock_client.analytics.all.return_value = []
        mock_client.ab_tests.all.return_value = []

        from execution.backup_lists import run_backup
        result = run_backup(client=mock_client, backup_root=tmp_path)

        assert result["success"] is True
        backup_dir = Path(result["backup_dir"])
        assert backup_dir.exists()
        assert (backup_dir / "stories.json").exists()
        data = json.loads((backup_dir / "stories.json").read_text())
        assert data["table"] == "stories"
        assert data["count"] == 2
        assert len(data["records"]) == 2

    def test_backup_writes_manifest(self, tmp_path, mock_client):
        """Backup should write a manifest.json with counts per table."""
        for table in ("stories", "blueprints", "templates", "assets",
                      "sources", "publishing_analytics", "analytics", "ab_tests"):
            getattr(mock_client, table).all.return_value = []

        from execution.backup_lists import run_backup
        result = run_backup(client=mock_client, backup_root=tmp_path)

        manifest_path = Path(result["backup_dir"]) / "manifest.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text())
        assert "timestamp" in manifest
        assert "tables" in manifest
        assert manifest["tables"]["stories"] == 0

    def test_backup_rotation_keeps_n(self, tmp_path, mock_client):
        """Old backups beyond keep_last should be deleted."""
        for table in ("stories", "blueprints", "templates", "assets",
                      "sources", "publishing_analytics", "analytics", "ab_tests"):
            getattr(mock_client, table).all.return_value = []

        from execution.backup_lists import run_backup
        # Create 5 backups, keep_last=3
        for i in range(5):
            run_backup(client=mock_client, backup_root=tmp_path, keep_last=3)

        backup_dirs = sorted(d for d in tmp_path.iterdir() if d.is_dir())
        assert len(backup_dirs) == 3  # oldest 2 pruned
```

**Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_backup_restore.py::TestBackup -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'execution.backup_lists'`

**Step 3: Write the backup script**

Create `execution/backup_lists.py`:

```python
#!/usr/bin/env python3
"""Backup all Microsoft Lists tables to local JSON files.

Usage:
    python execution/backup_lists.py                    # backup all tables
    python execution/backup_lists.py --keep-last 7      # keep 7 most recent
    python execution/backup_lists.py --tables stories blueprints  # specific tables
"""
import argparse
import json
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# Bootstrap — keep at top before local imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from execution.utils.script_bootstrap import bootstrap  # noqa: E402
PROJECT_ROOT, logger, _ = bootstrap(__file__, "backup_lists")

BACKUP_ROOT = PROJECT_ROOT / ".tmp" / "backups"

TABLES = [
    "stories", "blueprints", "templates", "assets",
    "sources", "publishing_analytics", "analytics", "ab_tests",
]


def run_backup(
    client=None,
    backup_root: Path = BACKUP_ROOT,
    keep_last: int = 7,
    tables: list[str] | None = None,
) -> dict:
    """Dump all tables to timestamped JSON directory.

    Returns dict with: success, backup_dir, table_counts, errors.
    """
    if client is None:
        from execution.utils.backlog_client import BacklogClient
        client = BacklogClient()

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_dir = Path(backup_root) / ts
    backup_dir.mkdir(parents=True, exist_ok=True)

    target_tables = tables or TABLES
    table_counts = {}
    errors = []

    for table_name in target_tables:
        table = getattr(client, table_name, None)
        if table is None:
            logger.warning("Table %s not found on client, skipping", table_name)
            continue
        try:
            records = table.all()
            table_counts[table_name] = len(records)

            payload = {
                "table": table_name,
                "backed_up_at": ts,
                "count": len(records),
                "records": records,
            }
            out_path = backup_dir / f"{table_name}.json"
            out_path.write_text(json.dumps(payload, indent=2, default=str))
            logger.info("  %-25s %4d records", table_name, len(records))
        except Exception as exc:
            errors.append({"table": table_name, "error": str(exc)})
            logger.error("Failed to backup %s: %s", table_name, exc)

    # Write manifest
    manifest = {
        "timestamp": ts,
        "tables": table_counts,
        "errors": [e["table"] for e in errors],
        "total_records": sum(table_counts.values()),
    }
    (backup_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    # Rotate old backups
    _rotate_backups(Path(backup_root), keep_last)

    return {
        "success": len(errors) == 0,
        "backup_dir": str(backup_dir),
        "table_counts": table_counts,
        "errors": errors,
    }


def _rotate_backups(backup_root: Path, keep_last: int):
    """Delete oldest backup directories beyond keep_last."""
    if not backup_root.exists():
        return
    dirs = sorted(
        (d for d in backup_root.iterdir() if d.is_dir()),
        key=lambda d: d.name,
    )
    to_remove = dirs[:-keep_last] if len(dirs) > keep_last else []
    for d in to_remove:
        shutil.rmtree(d)
        logger.info("Pruned old backup: %s", d.name)


def main():
    parser = argparse.ArgumentParser(description="Backup Microsoft Lists tables")
    parser.add_argument("--keep-last", type=int, default=7, help="Backups to retain (default: 7)")
    parser.add_argument("--tables", nargs="+", choices=TABLES, help="Specific tables to backup")
    args = parser.parse_args()

    logger.info("Starting Microsoft Lists backup...")
    result = run_backup(keep_last=args.keep_last, tables=args.tables)

    total = result["table_counts"]
    logger.info(
        "Backup complete: %d tables, %d total records → %s",
        len(total), sum(total.values()), result["backup_dir"],
    )
    if result["errors"]:
        logger.error("Errors: %s", result["errors"])
        sys.exit(1)


if __name__ == "__main__":
    main()
```

**Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_backup_restore.py::TestBackup -v`
Expected: 3 PASS

**Step 5: Commit**

```bash
git add execution/backup_lists.py tests/test_backup_restore.py
git commit -m "feat: add Microsoft Lists backup with rotation"
```

---

### Task 2: Restore Script

**Files:**
- Create: `execution/restore_lists.py`
- Modify: `tests/test_backup_restore.py` (add TestRestore class)

**Step 1: Write the failing test for restore**

Add to `tests/test_backup_restore.py`:

```python
class TestRestore:
    def _create_backup(self, backup_dir, table_name, records):
        """Helper: write a backup JSON file."""
        payload = {
            "table": table_name,
            "backed_up_at": "20260301_120000",
            "count": len(records),
            "records": records,
        }
        (backup_dir / f"{table_name}.json").write_text(json.dumps(payload))
        manifest = {"timestamp": "20260301_120000", "tables": {table_name: len(records)}}
        (backup_dir / "manifest.json").write_text(json.dumps(manifest))

    def test_restore_calls_create_for_each_record(self, tmp_path, mock_client, sample_records):
        """Restore should call table.create() for each record in the backup."""
        backup_dir = tmp_path / "20260301_120000"
        backup_dir.mkdir()
        self._create_backup(backup_dir, "stories", sample_records)
        mock_client.stories.create.return_value = {"id": "new_1"}

        from execution.restore_lists import run_restore
        result = run_restore(
            backup_dir=backup_dir,
            client=mock_client,
            tables=["stories"],
            dry_run=False,
        )

        assert result["success"] is True
        assert mock_client.stories.create.call_count == 2

    def test_restore_dry_run_does_not_mutate(self, tmp_path, mock_client, sample_records):
        """Dry run should report what would happen without calling create."""
        backup_dir = tmp_path / "20260301_120000"
        backup_dir.mkdir()
        self._create_backup(backup_dir, "stories", sample_records)

        from execution.restore_lists import run_restore
        result = run_restore(
            backup_dir=backup_dir,
            client=mock_client,
            tables=["stories"],
            dry_run=True,
        )

        assert result["success"] is True
        assert result["would_restore"]["stories"] == 2
        mock_client.stories.create.assert_not_called()
```

**Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_backup_restore.py::TestRestore -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'execution.restore_lists'`

**Step 3: Write the restore script**

Create `execution/restore_lists.py`:

```python
#!/usr/bin/env python3
"""Restore Microsoft Lists tables from a local backup.

Usage:
    python execution/restore_lists.py <backup_dir>                         # dry run
    python execution/restore_lists.py <backup_dir> --apply                 # live restore
    python execution/restore_lists.py <backup_dir> --tables stories        # single table
    python execution/restore_lists.py .tmp/backups/latest --apply          # latest backup
"""
import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from execution.utils.script_bootstrap import bootstrap  # noqa: E402
PROJECT_ROOT, logger, _ = bootstrap(__file__, "restore_lists")

BACKUP_ROOT = PROJECT_ROOT / ".tmp" / "backups"

TABLES = [
    "stories", "blueprints", "templates", "assets",
    "sources", "publishing_analytics", "analytics", "ab_tests",
]


def _resolve_backup_dir(path: Path) -> Path:
    """Resolve 'latest' to most recent backup directory."""
    if path.name == "latest":
        parent = path.parent if path.parent.exists() else BACKUP_ROOT
        dirs = sorted((d for d in parent.iterdir() if d.is_dir()), key=lambda d: d.name)
        if not dirs:
            raise FileNotFoundError(f"No backups found in {parent}")
        return dirs[-1]
    return path


def run_restore(
    backup_dir: Path,
    client=None,
    tables: list[str] | None = None,
    dry_run: bool = True,
) -> dict:
    """Restore records from a backup directory.

    Returns dict with: success, restored counts, errors.
    """
    backup_dir = _resolve_backup_dir(Path(backup_dir))

    if not backup_dir.exists():
        return {"success": False, "error": f"Backup dir not found: {backup_dir}"}

    if client is None and not dry_run:
        from execution.utils.backlog_client import BacklogClient
        client = BacklogClient()

    target_tables = tables or TABLES
    restored = {}
    would_restore = {}
    errors = []

    for table_name in target_tables:
        json_path = backup_dir / f"{table_name}.json"
        if not json_path.exists():
            logger.info("No backup file for %s, skipping", table_name)
            continue

        data = json.loads(json_path.read_text())
        records = data.get("records", [])

        if dry_run:
            would_restore[table_name] = len(records)
            logger.info("  [DRY RUN] %-25s would restore %d records", table_name, len(records))
            continue

        table = getattr(client, table_name, None)
        if table is None:
            errors.append({"table": table_name, "error": "Table not found on client"})
            continue

        count = 0
        for record in records:
            fields = record.get("fields", {})
            if not fields:
                continue
            try:
                table.create(fields)
                count += 1
            except Exception as exc:
                errors.append({"table": table_name, "record_id": record.get("id"), "error": str(exc)})

        restored[table_name] = count
        logger.info("  %-25s restored %d / %d records", table_name, count, len(records))

    return {
        "success": len(errors) == 0,
        "backup_dir": str(backup_dir),
        "restored": restored,
        "would_restore": would_restore,
        "errors": errors,
    }


def main():
    parser = argparse.ArgumentParser(description="Restore Microsoft Lists from backup")
    parser.add_argument("backup_dir", type=Path, help="Path to backup directory (or 'latest')")
    parser.add_argument("--apply", action="store_true", help="Actually restore (default is dry-run)")
    parser.add_argument("--tables", nargs="+", choices=TABLES, help="Specific tables to restore")
    args = parser.parse_args()

    mode = "LIVE" if args.apply else "DRY RUN"
    logger.info("Restore from %s [%s]...", args.backup_dir, mode)

    result = run_restore(
        backup_dir=args.backup_dir,
        tables=args.tables,
        dry_run=not args.apply,
    )

    if result.get("error"):
        logger.error(result["error"])
        sys.exit(1)

    if args.apply:
        total = sum(result["restored"].values())
        logger.info("Restore complete: %d records across %d tables", total, len(result["restored"]))
    else:
        total = sum(result["would_restore"].values())
        logger.info("Dry run complete: would restore %d records. Use --apply to execute.", total)

    if result["errors"]:
        logger.error("%d errors during restore", len(result["errors"]))
        sys.exit(1)


if __name__ == "__main__":
    main()
```

**Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_backup_restore.py -v`
Expected: 5 PASS (3 backup + 2 restore)

**Step 5: Commit**

```bash
git add execution/restore_lists.py tests/test_backup_restore.py
git commit -m "feat: add Microsoft Lists restore with dry-run mode"
```

---

### Task 3: Pre-Pipeline Sanity Check

**Files:**
- Create: `execution/preflight_check.py`
- Modify: `tests/test_backup_restore.py` (add TestPreflightCheck class)

**Step 1: Write the failing test**

Add to `tests/test_backup_restore.py`:

```python
class TestPreflightCheck:
    def _write_manifest(self, backup_root, counts):
        ts = "20260301_120000"
        d = backup_root / ts
        d.mkdir(parents=True, exist_ok=True)
        manifest = {"timestamp": ts, "tables": counts, "total_records": sum(counts.values())}
        (d / "manifest.json").write_text(json.dumps(manifest))

    def test_passes_when_counts_stable(self, tmp_path, mock_client):
        """No alarm when current counts match backup."""
        self._write_manifest(tmp_path, {"stories": 100, "blueprints": 200})
        mock_client.stories.all.return_value = [{"id": str(i)} for i in range(100)]
        mock_client.blueprints.all.return_value = [{"id": str(i)} for i in range(200)]

        from execution.preflight_check import run_preflight
        result = run_preflight(client=mock_client, backup_root=tmp_path, threshold=0.20)
        assert result["safe"] is True

    def test_fails_when_count_drops_over_threshold(self, tmp_path, mock_client):
        """Alarm when a table lost >20% of records."""
        self._write_manifest(tmp_path, {"stories": 100, "blueprints": 200})
        mock_client.stories.all.return_value = [{"id": str(i)} for i in range(70)]  # 30% drop
        mock_client.blueprints.all.return_value = [{"id": str(i)} for i in range(200)]

        from execution.preflight_check import run_preflight
        result = run_preflight(client=mock_client, backup_root=tmp_path, threshold=0.20)
        assert result["safe"] is False
        assert "stories" in result["alerts"][0]["table"]

    def test_passes_when_no_backup_exists(self, tmp_path, mock_client):
        """First run (no backup) should pass — nothing to compare against."""
        from execution.preflight_check import run_preflight
        result = run_preflight(client=mock_client, backup_root=tmp_path, threshold=0.20)
        assert result["safe"] is True
```

**Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_backup_restore.py::TestPreflightCheck -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Write the preflight check**

Create `execution/preflight_check.py`:

```python
#!/usr/bin/env python3
"""Pre-pipeline sanity check: compare current record counts against last backup.

Exits with code 1 if any table lost more than --threshold (default 20%) of records.
Designed to run as step 0 of daily_intel.sh — halt pipeline before mutations.

Usage:
    python execution/preflight_check.py              # check + exit 1 on alarm
    python execution/preflight_check.py --threshold 0.30   # 30% tolerance
"""
import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from execution.utils.script_bootstrap import bootstrap  # noqa: E402
PROJECT_ROOT, logger, _ = bootstrap(__file__, "preflight_check")

BACKUP_ROOT = PROJECT_ROOT / ".tmp" / "backups"

# Only check tables that matter for data loss detection
CHECK_TABLES = ["stories", "blueprints", "templates", "assets", "sources"]


def _latest_manifest(backup_root: Path) -> dict | None:
    """Find the most recent manifest.json in backup directories."""
    if not backup_root.exists():
        return None
    dirs = sorted((d for d in backup_root.iterdir() if d.is_dir()), key=lambda d: d.name)
    for d in reversed(dirs):
        manifest_path = d / "manifest.json"
        if manifest_path.exists():
            return json.loads(manifest_path.read_text())
    return None


def run_preflight(
    client=None,
    backup_root: Path = BACKUP_ROOT,
    threshold: float = 0.20,
) -> dict:
    """Compare current counts against last backup. Returns safe/alerts."""
    manifest = _latest_manifest(Path(backup_root))
    if manifest is None:
        logger.info("No previous backup found — preflight passes (first run)")
        return {"safe": True, "alerts": [], "reason": "no_previous_backup"}

    if client is None:
        from execution.utils.backlog_client import BacklogClient
        client = BacklogClient()

    baseline = manifest.get("tables", {})
    alerts = []

    for table_name in CHECK_TABLES:
        expected = baseline.get(table_name, 0)
        if expected == 0:
            continue  # Nothing to compare

        table = getattr(client, table_name, None)
        if table is None:
            continue

        try:
            current = len(table.all())
        except Exception as exc:
            alerts.append({
                "table": table_name,
                "error": f"Failed to count: {exc}",
            })
            continue

        drop_pct = (expected - current) / expected if expected > 0 else 0

        if drop_pct > threshold:
            alerts.append({
                "table": table_name,
                "expected": expected,
                "current": current,
                "drop_pct": round(drop_pct * 100, 1),
            })
            logger.error(
                "ALERT: %s dropped from %d → %d (%.1f%% loss, threshold %.0f%%)",
                table_name, expected, current, drop_pct * 100, threshold * 100,
            )
        else:
            logger.info("  %-25s %d → %d (OK)", table_name, expected, current)

    safe = len(alerts) == 0
    return {"safe": safe, "alerts": alerts, "baseline_ts": manifest.get("timestamp")}


def main():
    parser = argparse.ArgumentParser(description="Pre-pipeline record count sanity check")
    parser.add_argument("--threshold", type=float, default=0.20, help="Max allowed drop ratio (default: 0.20 = 20%%)")
    args = parser.parse_args()

    logger.info("Preflight check (threshold: %.0f%%)...", args.threshold * 100)
    result = run_preflight(threshold=args.threshold)

    if not result["safe"]:
        logger.error(
            "PREFLIGHT FAILED — %d table(s) have suspicious record drops. "
            "Pipeline halted. Check .tmp/backups/ for the last good snapshot.",
            len(result["alerts"]),
        )
        sys.exit(1)

    logger.info("Preflight passed — all tables within expected range")


if __name__ == "__main__":
    main()
```

**Step 4: Run all tests**

Run: `venv/bin/python -m pytest tests/test_backup_restore.py -v`
Expected: 8 PASS (3 backup + 2 restore + 3 preflight)

**Step 5: Commit**

```bash
git add execution/preflight_check.py tests/test_backup_restore.py
git commit -m "feat: add pre-pipeline record count sanity check"
```

---

### Task 4: Pipeline Integration

**Files:**
- Modify: `runbooks/daily_intel.sh` (add backup + preflight as steps 0a/0b)

**Step 1: Add backup + preflight to daily_intel.sh**

Before the existing step 1 (`fetch_ai_creators.py`), add:

```bash
# ── Step 0a: Backup all tables ──
log_step "0a" "Backing up Microsoft Lists..."
"$PYTHON" execution/backup_lists.py --keep-last 7 2>&1 | tee -a "$LOG_FILE"
# Non-fatal: backup failure should not block pipeline
BACKUP_EXIT=$?
if [ $BACKUP_EXIT -ne 0 ]; then
    log_step "0a" "WARNING: Backup failed (exit $BACKUP_EXIT) — continuing pipeline"
fi

# ── Step 0b: Preflight sanity check ──
log_step "0b" "Running preflight record count check..."
"$PYTHON" execution/preflight_check.py --threshold 0.20 2>&1 | tee -a "$LOG_FILE"
PREFLIGHT_EXIT=$?
if [ $PREFLIGHT_EXIT -ne 0 ]; then
    log_step "0b" "FATAL: Preflight failed — pipeline halted to prevent data loss"
    exit 1
fi
```

**Step 2: Verify the script runs**

Run: `cd /Users/anarchistsid/GenLab/Content\ Scraper && venv/bin/python execution/backup_lists.py --keep-last 7`
Expected: Creates `.tmp/backups/<timestamp>/` with 8 JSON files + manifest.json

Run: `venv/bin/python execution/preflight_check.py`
Expected: Passes (compares against the backup just created)

**Step 3: Commit**

```bash
git add runbooks/daily_intel.sh execution/backup_lists.py execution/preflight_check.py execution/restore_lists.py tests/test_backup_restore.py
git commit -m "feat: Microsoft Lists backup/restore system with pipeline integration"
```

---

### Task 5: Full Integration Test

**Step 1: Run the backup manually**

```bash
venv/bin/python execution/backup_lists.py --keep-last 7
```

Verify:
- `.tmp/backups/<timestamp>/manifest.json` exists
- All 8 table JSON files present
- Record counts match what we see in the dashboard (257 blueprints, 217 stories, etc.)

**Step 2: Run the preflight check**

```bash
venv/bin/python execution/preflight_check.py
```

Verify: Passes with "all tables within expected range"

**Step 3: Test restore dry-run**

```bash
venv/bin/python execution/restore_lists.py .tmp/backups/latest
```

Verify: Shows "would restore N records" for each table, no mutations

**Step 4: Run full test suite**

```bash
venv/bin/python -m pytest tests/ -x -q
```

Expected: All tests pass, no regressions

**Step 5: Final commit with any adjustments**

```bash
git add -A
git commit -m "chore: finalize backup system integration"
```
