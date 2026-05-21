"""Perceptual video deduplication via frame hashing.

Detects the same clip re-uploaded across sources (e.g., same viral clip
on Twitch + YouTube + Reddit). Must run before compilation assembly.
Distinct from identity dedup (URL/ID matching) — this catches re-uploads.

Extracts keyframes via FFmpeg, computes average perceptual hashes with
imagehash + Pillow, then combines into a single 64-bit hex hash.
Hashes are stored in an append-only JSON database that persists across runs
so we catch cross-day duplicates.

Hamming distance < threshold (default 10) between two 64-bit hashes = duplicate.
"""

import json
import logging
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

KEYFRAME_COUNT = 8


def hamming_distance(hash_a: str, hash_b: str) -> int:
    """Compute Hamming distance between two hex hash strings."""
    int_a = int(hash_a, 16)
    int_b = int(hash_b, 16)
    xor = int_a ^ int_b
    return bin(xor).count("1")


def _extract_keyframes(video_path: str, out_dir: str) -> list[str]:
    """Extract evenly-spaced keyframes from a video via FFmpeg."""
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-show_entries",
        "format=duration",
        "-of",
        "csv=p=0",
        video_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        duration = float(result.stdout.strip())
    except (ValueError, subprocess.TimeoutExpired, FileNotFoundError):
        return []

    if duration <= 0:
        return []

    interval = duration / (KEYFRAME_COUNT + 1)
    frame_paths = []
    for i in range(KEYFRAME_COUNT):
        ts = interval * (i + 1)
        out_path = str(Path(out_dir) / f"frame_{i:02d}.png")
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{ts:.2f}",
            "-i",
            video_path,
            "-frames:v",
            "1",
            "-q:v",
            "2",
            out_path,
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=15)
            if Path(out_path).exists():
                frame_paths.append(out_path)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    return frame_paths


def compute_hash(video_path: str) -> str | None:
    """Compute perceptual hash of a video file.

    Extracts keyframes, computes average hash per frame with imagehash,
    then XORs all frame hashes into a single 64-bit hash.
    Returns hex hash string, or None if hashing fails.
    """
    try:
        import imagehash
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp_dir:
            frames = _extract_keyframes(video_path, tmp_dir)
            if not frames:
                logger.warning("Failed to hash %s: no keyframes extracted", video_path)
                return None

            combined = None
            for fp in frames:
                img = Image.open(fp).resize((64, 64), Image.LANCZOS)
                h = imagehash.average_hash(img)
                if combined is None:
                    combined = h
                else:
                    combined = imagehash.ImageHash(combined.hash ^ h.hash)

            return str(combined) if combined is not None else None

    except Exception as e:
        logger.warning("Failed to hash %s: %s", video_path, e)
        return None


class HashStore:
    """Append-only perceptual hash database backed by a JSON file.

    Persists across pipeline runs to catch cross-day duplicates.
    """

    def __init__(self, store_path: str | Path):
        self.store_path = Path(store_path)
        self.entries: list[dict] = []

    def load(self) -> None:
        """Load existing entries from disk."""
        if self.store_path.exists():
            with open(self.store_path) as f:
                self.entries = json.load(f)
            logger.info("Loaded %d hashes from %s", len(self.entries), self.store_path)
        else:
            self.entries = []

    def save(self) -> None:
        """Persist entries to disk."""
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.store_path, "w") as f:
            json.dump(self.entries, f)
        logger.info("Saved %d hashes to %s", len(self.entries), self.store_path)

    def add(self, clip_id: str, hash_hex: str, source_url: str) -> None:
        """Add a new hash entry."""
        self.entries.append(
            {
                "clip_id": clip_id,
                "hash_hex": hash_hex,
                "source_url": source_url,
                "added_at": datetime.now(UTC).isoformat(),
            }
        )

    def find_near_duplicates(self, hash_hex: str, threshold: int = 10) -> list[dict]:
        """Find entries with Hamming distance below threshold."""
        duplicates = []
        for entry in self.entries:
            try:
                dist = hamming_distance(hash_hex, entry["hash_hex"])
                if dist < threshold:
                    duplicates.append({**entry, "hamming_distance": dist})
            except (ValueError, KeyError):
                continue
        return duplicates
