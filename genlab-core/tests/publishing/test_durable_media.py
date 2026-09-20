"""Approved means durable, published means released.

THE TWO CLOCKS. Run directories prune to the last three runs; the publish queue
schedules a week out. 28 approved reels were living on the shorter clock, and
two had already died on it — jamming the publisher for three days because a
scheduled record could neither publish (no media) nor archive (the guard).
"""

from __future__ import annotations

import json

import pytest
from genlab_core.publishing.durable_media import (
    MediaCopyError,
    copy_for_schedule,
    is_durable,
    orphans,
    parse_paths,
    release,
)


def _bp(tmp_path, n=1):
    srcs = []
    for i in range(n):
        p = tmp_path / "run" / f"reel{i}.mp4"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"video-bytes-%d" % i)
        srcs.append(str(p))
    return {"visual_paths": json.dumps(srcs)}, srcs


def test_approved_means_the_durable_copy_exists(tmp_path):
    fields, srcs = _bp(tmp_path, 2)
    out = copy_for_schedule("rec1", fields, root=tmp_path / "durable")
    assert len(out) == 2
    for p in out:
        assert (tmp_path / "durable" / "rec1").exists()
        assert p.startswith(str(tmp_path / "durable" / "rec1"))


def test_the_run_directory_is_left_exactly_as_found(tmp_path):
    """Copies, never moves. Cleanup keeps its own policy and nothing scheduled
    depends on it."""
    fields, srcs = _bp(tmp_path)
    copy_for_schedule("rec1", fields, root=tmp_path / "durable")
    for s in srcs:
        assert (tmp_path / "run" / s.rsplit("/", 1)[1]).exists()


def test_a_pruned_run_directory_leaves_the_blueprint_publishable(tmp_path):
    """The whole point. Prune the original and the durable copy still resolves."""
    import shutil

    fields, _ = _bp(tmp_path)
    out = copy_for_schedule("rec1", fields, root=tmp_path / "durable")
    shutil.rmtree(tmp_path / "run")
    assert all((tmp_path / "durable" / "rec1" / p.rsplit("/", 1)[1]).exists() for p in out)


def test_missing_media_refuses_rather_than_approving_anyway(tmp_path):
    """A blueprint approved with media the store does not hold is a reel the
    publisher will select, fail to find, and archive — after a human reviewed
    it."""
    with pytest.raises(MediaCopyError):
        copy_for_schedule(
            "rec1", {"visual_paths": json.dumps(["/gone/x.mp4"])}, root=tmp_path / "durable"
        )
    with pytest.raises(MediaCopyError):
        copy_for_schedule("rec1", {}, root=tmp_path / "durable")


def test_published_releases_the_copy(tmp_path):
    fields, _ = _bp(tmp_path)
    copy_for_schedule("rec1", fields, root=tmp_path / "durable")
    assert release("rec1", root=tmp_path / "durable") is True
    assert not (tmp_path / "durable" / "rec1").exists()
    assert release("rec1", root=tmp_path / "durable") is False  # idempotent


def test_already_durable_is_a_no_op(tmp_path):
    fields, _ = _bp(tmp_path)
    out = copy_for_schedule("rec1", fields, root=tmp_path / "durable")
    again = copy_for_schedule("rec1", {"visual_paths": json.dumps(out)}, root=tmp_path / "durable")
    assert again == out
    assert is_durable({"visual_paths": json.dumps(out)}, root=tmp_path / "durable")


def test_orphans_are_directories_with_no_live_blueprint(tmp_path):
    """A publish or archive that did not release. Counted before it is a disk
    finding."""
    fields, _ = _bp(tmp_path)
    copy_for_schedule("live", fields, root=tmp_path / "durable")
    copy_for_schedule("dead", fields, root=tmp_path / "durable")
    got = orphans({"live"}, root=tmp_path / "durable")
    assert [d.name for d in got] == ["dead"]


def test_visual_paths_has_one_reader(tmp_path):
    assert parse_paths({"visual_paths": '["/a.mp4"]'}) == ["/a.mp4"]
    assert parse_paths({"visual_paths": "/a.mp4"}) == ["/a.mp4"]
    assert parse_paths({}) == []


def test_the_accept_path_copies_before_it_approves():
    """Structural: the copy must precede the status change, not follow it."""
    import inspect

    from genlab_core.scheduling import auto_approver

    src = inspect.getsource(auto_approver)
    assert src.index("copy_for_schedule(") < src.index('"action_taken": "approved"')
    assert "media_copy_failed" in src


def test_publish_and_archive_both_release():
    import inspect

    from genlab_core.publishing import publish_all_platforms

    src = inspect.getsource(publish_all_platforms)
    assert src.count("release(record_id)") >= 2, "publish and archive must both release"
