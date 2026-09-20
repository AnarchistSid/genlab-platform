"""bt709-metadata-retag — rewrite h264 color-space metadata to bt709
without re-encoding.

## What it does

Many rendering pipelines produce h264 videos with color-space
metadata tagged as `smpte170m`, `bt470bg`, or `unknown`. Meta / TikTok
/ YouTube Shorts validation tooling that requires bt709 rejects
these on upload. Full re-encode fixes it but costs 30-60s per clip
and burns compute.

This app uses ffmpeg's `h264_metadata` bitstream filter to rewrite
the SPS color-primaries / transfer / matrix flags to bt709 (index 1)
WITHOUT touching the encoded frame bytes. Cost: ~1s per minute of
video. Zero quality change.

## Concrete origin

Discovered 2026-08-16 root-causing a Gen Lab video pipeline crash:
gaming compilation renders concatenated clips via `-c copy` (stream
copy), which inherits the source clips' color metadata. When any
source clip had `smpte170m` primaries, the compilation output
inherited it, then post-encode validation rejected it. Fixed in
CriticalRush at commit 2a98b510 — extracting the tool as a standalone
app so other agents can use it without re-implementing.
"""
import logging
import shutil
import subprocess
from pathlib import Path

from inferencesh import BaseApp, BaseAppSetup, File
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class AppSetup(BaseAppSetup):
    """No setup config needed — the app is a thin ffmpeg wrapper."""
    pass


class RunInput(BaseModel):
    """Video file to re-tag."""
    video: File = Field(
        description="Input h264 mp4 video to re-tag with bt709 "
        "color metadata. Passes through untouched if already bt709.",
    )


class RunOutput(BaseModel):
    """Retagged video + a small diagnostic string."""
    video: File = Field(
        description="Video with bt709 color_primaries / color_trc / "
        "colorspace metadata. Same encoded frames as input.",
    )
    before: str = Field(
        description="color_primaries|color_transfer|color_space of input",
    )
    after: str = Field(
        description="color_primaries|color_transfer|color_space of output",
    )
    was_already_bt709: bool = Field(
        description="True if the input was already correctly tagged; "
        "output is a byte-identical copy in that case.",
    )


def _probe_colors(path: str) -> tuple[str, str, str]:
    """Return (primaries, transfer, space) via ffprobe. Any missing
    field becomes 'unknown'."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries",
                "stream=color_primaries,color_transfer,color_space",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True, text=True, timeout=15,
        )
        lines = [ln.strip() for ln in result.stdout.strip().splitlines()]
        while len(lines) < 3:
            lines.append("unknown")
        return lines[0] or "unknown", lines[1] or "unknown", lines[2] or "unknown"
    except Exception:
        return "unknown", "unknown", "unknown"


class App(BaseApp):
    async def setup(self, config: AppSetup):
        """No model to load — ffmpeg is provided by the runtime image."""
        # Verify ffmpeg is available. Fail loud at setup rather than
        # per-request if the runtime is missing the binary.
        which = shutil.which("ffmpeg")
        if not which:
            raise RuntimeError(
                "ffmpeg binary not found on PATH — this app requires "
                "ffmpeg in the runtime image."
            )
        logger.info(f"bt709-metadata-retag ready; ffmpeg at {which}")

    async def run(self, input_data: RunInput) -> RunOutput:
        in_path = input_data.video.path
        if not Path(in_path).exists():
            raise RuntimeError(f"Input file missing: {in_path}")

        before_p, before_t, before_s = _probe_colors(in_path)
        before = f"{before_p}|{before_t}|{before_s}"
        logger.info(f"input colors: {before}")

        # Fast path: already bt709 on all 3 → passthrough copy.
        if before_p == "bt709" and before_t == "bt709" and before_s == "bt709":
            logger.info(
                "input already bt709 — skipping retag, returning copy",
            )
            out_path = "/tmp/bt709_retag_passthrough.mp4"
            shutil.copy2(in_path, out_path)
            return RunOutput(
                video=File(path=out_path),
                before=before,
                after=before,
                was_already_bt709=True,
            )

        # Rewrite bitstream metadata via h264_metadata BSF. No re-encode.
        # colour_primaries=1 / transfer_characteristics=1 /
        # matrix_coefficients=1 == bt709 per H.264 spec Table E-3.
        out_path = "/tmp/bt709_retag_output.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-i", in_path,
            "-c", "copy",
            "-bsf:v",
            "h264_metadata=colour_primaries=1:"
            "transfer_characteristics=1:"
            "matrix_coefficients=1",
            out_path,
        ]
        logger.info("running ffmpeg h264_metadata retag")
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg retag failed (exit={result.returncode}): "
                f"{result.stderr[-500:]}"
            )

        after_p, after_t, after_s = _probe_colors(out_path)
        after = f"{after_p}|{after_t}|{after_s}"
        logger.info(f"output colors: {after}")

        return RunOutput(
            video=File(path=out_path),
            before=before,
            after=after,
            was_already_bt709=False,
        )
