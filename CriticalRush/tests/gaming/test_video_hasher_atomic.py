"""R-34: HashStore must persist atomically and recover from a corrupt store.

A non-atomic whole-file write could truncate the JSON on a crash, after which
load() raised and perceptual dedup was silently disabled (duplicates
re-shipped). Load now recovers loudly with an empty set, and save writes via a
temp file + atomic rename.
"""

from __future__ import annotations

from pathlib import Path

from niches.gaming.tools.video_hasher import HashStore


def test_save_load_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "hashes.json"
    store = HashStore(p)
    store.add("clip_1", "abcdef1234567890", "https://example.com/1")
    store.save()

    reloaded = HashStore(p)
    reloaded.load()
    assert len(reloaded.entries) == 1
    assert reloaded.entries[0]["clip_id"] == "clip_1"


def test_load_recovers_from_corrupt_store(tmp_path: Path, caplog) -> None:
    p = tmp_path / "hashes.json"
    p.write_text("{ this is not valid json and was truncated mid-")

    store = HashStore(p)
    store.load()  # must NOT raise

    assert store.entries == []
    assert "unreadable" in caplog.text.lower()


def test_save_leaves_no_temp_files(tmp_path: Path) -> None:
    p = tmp_path / "hashes.json"
    store = HashStore(p)
    store.add("c", "0000000000000000", "u")
    store.save()

    assert p.exists()
    assert not list(tmp_path.glob("*.tmp.*"))


def test_corrupt_then_save_produces_valid_store(tmp_path: Path) -> None:
    # The recovery path must leave a clean store after the next save.
    p = tmp_path / "hashes.json"
    p.write_text("garbage")
    store = HashStore(p)
    store.load()
    store.add("c1", "ffffffffffffffff", "u")
    store.save()

    fresh = HashStore(p)
    fresh.load()
    assert len(fresh.entries) == 1
