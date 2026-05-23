"""R-55: the YouTube quota state file is shared across all 5 niche processes.
Each record() must re-read the latest state under a cross-process lock before
applying its delta, so a stale in-memory counter can't clobber another
process's increment (lost update -> quota overrun -> uploadLimitExceeded).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from genlab_core.monitoring.youtube_quota import UPLOAD_COST, YouTubeQuotaTracker


@pytest.fixture()
def state_file(tmp_path: Path) -> Path:
    return tmp_path / "youtube_quota.json"


def test_second_instance_sees_first_instances_writes(state_file: Path) -> None:
    a = YouTubeQuotaTracker(state_path=state_file)
    a.record("upload")  # +1600

    b = YouTubeQuotaTracker(state_path=state_file)  # __init__ loads from disk
    assert b.status()["used"] == UPLOAD_COST
    assert b.daily_uploads_used() == 1


def test_no_lost_update_across_instances(state_file: Path) -> None:
    # Two long-lived trackers (simulating two niche processes) interleave writes.
    a = YouTubeQuotaTracker(state_path=state_file)
    b = YouTubeQuotaTracker(state_path=state_file)

    a.record("upload")  # disk: 1600
    b.record("upload")  # b re-reads 1600 under lock, adds 1600 -> disk: 3200

    # `a` still holds a stale in-memory 1600; its next record must RE-READ disk
    # (3200) before adding, not overwrite with 1600 + delta.
    total = a.record("comment_list")  # +1
    assert total == 2 * UPLOAD_COST + 1  # 3201, proving no lost update


def test_save_is_atomic_no_partial_file(state_file: Path) -> None:
    a = YouTubeQuotaTracker(state_path=state_file)
    a.record("upload")
    # A fresh reader always sees a complete, parseable file (os.replace rename).
    import json

    data = json.loads(state_file.read_text())
    assert data["used"] == UPLOAD_COST
    # No leftover temp files in the directory.
    assert not list(state_file.parent.glob("*.tmp.*"))
