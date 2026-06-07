"""R-54/R-03: per-platform ffmpeg encodes run inside the publisher's parallel
fan-out. A process-wide semaphore must cap concurrent encodes (default 1) so
the 4GB box can't OOM from up to 5 simultaneous ffmpeg processes.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from genlab_core.publishing.publish_all_platforms import (
    _MAX_CONCURRENT_ENCODES,
    _transcode_for_platform,
)


def test_default_concurrency_is_one():
    assert _MAX_CONCURRENT_ENCODES == 1


def test_encodes_are_serialized(tmp_path: Path):
    peak = [0]
    cur = [0]
    lock = threading.Lock()

    def tracking_run(*args, **kwargs):
        with lock:
            cur[0] += 1
            peak[0] = max(peak[0], cur[0])
        time.sleep(0.05)  # hold the "encode" so overlap would be observable
        with lock:
            cur[0] -= 1
        return MagicMock(returncode=0)

    sources = []
    for i in range(5):
        s = tmp_path / f"src{i}.mp4"
        s.write_bytes(b"x" * 20000)
        sources.append(s)

    # Patch at the CALL SITE (transcode module) — refactor-#9 PR 2/N moved
    # the imports to the top of transcode.py, so the bound names there are
    # ``transcode.subprocess`` and ``transcode.get_ffmpeg_binary``; patching
    # the source modules leaves those bound names pointing at the real ones
    # (classic "where to patch" gotcha that didn't manifest before because
    # the originals were deferred imports inside the function body).
    with (
        patch("genlab_core.publishing.transcode.subprocess.run", side_effect=tracking_run),
        patch("genlab_core.publishing.transcode.get_ffmpeg_binary", return_value="ffmpeg"),
    ):
        threads = [
            threading.Thread(target=_transcode_for_platform, args=(s, "instagram")) for s in sources
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert peak[0] >= 1, "encodes did not run"
    assert peak[0] <= _MAX_CONCURRENT_ENCODES, (
        f"peak {peak[0]} concurrent encodes exceeded the cap {_MAX_CONCURRENT_ENCODES}"
    )
