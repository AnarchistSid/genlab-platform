"""loudness-normalize — hit a loudness target deterministically, and prove it.

## What it does

Normalises an audio or video file to an EBU R128 integrated-loudness target
using ffmpeg's `loudnorm` in **two-pass** mode, and returns the measured
loudness before and after so the result is verifiable rather than asserted.

Video in, video out: the video stream is stream-copied untouched, only the
audio is re-encoded.

## Why two-pass

Single-pass `loudnorm` is adaptive — it adjusts gain as it goes, so the same
input does not reliably land on the target. Measured on one real 18.5s reel
rendered four times through an identical pipeline:

    -14.33   -14.78   -14.85   -15.51   LUFS      (target -14.0)

A 1.18 LU spread, with one render 1.51 LU off. Platform loudness targets are
pass/fail specs, so "usually near target" is not the same as "on target".

Two-pass measures the file first, then applies a fixed offset with
`linear=true`. Same input, same output, every time. The cost is one extra
analysis pass — seconds for short-form content.

## Presets

    streaming   -14 LUFS  (YouTube, Spotify, Meta, TikTok)
    podcast     -16 LUFS  (Apple Podcasts)
    broadcast   -23 LUFS  (EBU R128 / ATSC A/85)

True peak defaults to -1.5 dBTP, which leaves headroom for lossy transcoders
to overshoot without clipping.
"""
import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from inferencesh import BaseApp, BaseAppSetup, File
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_PRESETS: dict[str, float] = {
    "streaming": -14.0,
    "podcast": -16.0,
    "broadcast": -23.0,
}
_FFMPEG_TIMEOUT = 600
_PROBE_TIMEOUT = 60


class AppSetup(BaseAppSetup):
    """No setup config — a thin ffmpeg wrapper."""
    pass


class RunInput(BaseModel):
    media: File = Field(
        description="Audio or video file to normalise. Video streams are "
        "stream-copied untouched; only audio is re-encoded.",
    )
    preset: str = Field(
        "streaming",
        description="Loudness preset: 'streaming' (-14 LUFS: YouTube, Spotify, "
        "Meta, TikTok), 'podcast' (-16), 'broadcast' (-23), or 'custom' to use "
        "target_lufs.",
    )
    target_lufs: float = Field(
        -14.0,
        description="Integrated loudness target when preset='custom'. Ignored "
        "otherwise.",
    )
    true_peak_dbtp: float = Field(
        -1.5,
        description="True-peak ceiling in dBTP. -1.5 leaves headroom for lossy "
        "transcoders to overshoot without clipping.",
    )
    loudness_range: float = Field(
        11.0, description="Target loudness range (LRA) in LU.",
    )
    two_pass: bool = Field(
        True,
        description="Measure then apply (deterministic). Setting False uses "
        "single-pass adaptive normalisation, which is faster but lands in a "
        "~1 LU band around the target rather than on it.",
    )
    audio_bitrate: str = Field("192k", description="AAC bitrate for the output.")


class RunOutput(BaseModel):
    media: File = Field(description="Normalised file.")
    target_lufs: float = Field(description="Target that was applied.")
    input_lufs: Optional[float] = Field(None, description="Integrated loudness before.")
    output_lufs: Optional[float] = Field(None, description="Integrated loudness after.")
    input_true_peak: Optional[float] = Field(None, description="True peak before, dBTP.")
    output_true_peak: Optional[float] = Field(None, description="True peak after, dBTP.")
    input_lra: Optional[float] = Field(None, description="Loudness range before, LU.")
    output_lra: Optional[float] = Field(None, description="Loudness range after, LU.")
    deviation_lu: Optional[float] = Field(
        None, description="How far the output landed from target, in LU. "
        "Signed: negative means quieter than target.",
    )
    on_target: Optional[bool] = Field(
        None, description="True when the output is within 1 LU of target — the "
        "tolerance most platform specs allow.",
    )
    two_pass_used: bool = Field(description="Whether deterministic two-pass ran.")
    had_audio: bool = Field(description="False when the input carried no audio stream.")
    report: str = Field(description="Human-readable summary.")


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _has_audio(path: str) -> bool:
    result = _run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
        _PROBE_TIMEOUT,
    )
    return "audio" in (result.stdout or "")


def _has_video(path: str) -> bool:
    result = _run(
        ["ffprobe", "-v", "error", "-select_streams", "v",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
        _PROBE_TIMEOUT,
    )
    return "video" in (result.stdout or "")


def _measure(path: str, target: float, tp: float, lra: float) -> Optional[dict]:
    """loudnorm analysis pass. None when the JSON block can't be parsed."""
    result = _run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", path,
         "-af", f"loudnorm=I={target}:TP={tp}:LRA={lra}:print_format=json",
         "-f", "null", "-"],
        _FFMPEG_TIMEOUT,
    )
    match = re.search(r"\{[^{}]*input_i[^{}]*\}", result.stderr or "", re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _f(value) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    # loudnorm reports -inf for digital silence; JSON-invalid downstream.
    return out if out == out and abs(out) != float("inf") else None


class App(BaseApp):
    async def setup(self, config: AppSetup):
        for binary in ("ffmpeg", "ffprobe"):
            if not shutil.which(binary):
                raise RuntimeError(
                    f"{binary} not found on PATH — this app requires ffmpeg "
                    "in the runtime image."
                )
        logger.info("loudness-normalize ready")

    async def run(self, input_data: RunInput) -> RunOutput:
        src = input_data.media.path
        if not Path(src).exists():
            raise RuntimeError(f"Input file missing: {src}")

        target = _PRESETS.get(input_data.preset, input_data.target_lufs)
        tp = input_data.true_peak_dbtp
        lra = input_data.loudness_range
        logger.info(
            "normalising %s to %.1f LUFS (preset=%s, two_pass=%s)",
            Path(src).name, target, input_data.preset, input_data.two_pass,
        )

        if not _has_audio(src):
            # Nothing to normalise. Return the input untouched rather than
            # failing — a silent clip is a legitimate input, not an error.
            out_path = "/tmp/loudness_passthrough" + Path(src).suffix
            shutil.copy2(src, out_path)
            logger.warning("input has no audio stream — returning it unchanged")
            return RunOutput(
                media=File(path=out_path), target_lufs=target,
                two_pass_used=False, had_audio=False,
                report="No audio stream — input returned unchanged.",
            )

        measured = _measure(src, target, tp, lra) if input_data.two_pass else None
        if input_data.two_pass and measured is None:
            logger.warning(
                "analysis pass failed to parse — falling back to single-pass; "
                "output may land up to ~1 LU from target"
            )

        if measured:
            required = ("input_i", "input_tp", "input_lra", "input_thresh",
                        "target_offset")
            if all(k in measured for k in required):
                filter_spec = (
                    f"loudnorm=I={target}:TP={tp}:LRA={lra}"
                    f":measured_I={measured['input_i']}"
                    f":measured_TP={measured['input_tp']}"
                    f":measured_LRA={measured['input_lra']}"
                    f":measured_thresh={measured['input_thresh']}"
                    f":offset={measured['target_offset']}"
                    ":linear=true:print_format=summary"
                )
                two_pass_used = True
            else:
                filter_spec = f"loudnorm=I={target}:TP={tp}:LRA={lra}"
                two_pass_used = False
        else:
            filter_spec = f"loudnorm=I={target}:TP={tp}:LRA={lra}"
            two_pass_used = False

        # Container choice matters: the audio is re-encoded to AAC, and AAC is
        # not a legal stream in every container. Writing AAC into a .mp3
        # produced "Nothing was written into output file, because at least one
        # of its streams received no packets" — a hard ffmpeg failure, caught
        # testing a real podcast-preset input rather than a synthetic one.
        #
        # Video in  -> keep the input container (mp4/mov carry AAC fine).
        # Audio in  -> .m4a, the standard AAC container, regardless of input.
        has_video = _has_video(src)
        if has_video:
            suffix = Path(src).suffix or ".mp4"
        else:
            suffix = ".m4a"
        out_path = f"/tmp/loudness_normalized{suffix}"

        cmd = ["ffmpeg", "-y", "-i", src, "-af", filter_spec]
        if has_video:
            # Never re-encode video for an audio change.
            cmd += ["-c:v", "copy", "-map", "0:v", "-map", "0:a"]
        cmd += ["-c:a", "aac", "-b:a", input_data.audio_bitrate,
                "-ar", "48000", out_path]

        result = _run(cmd, _FFMPEG_TIMEOUT)
        if result.returncode != 0 or not Path(out_path).exists():
            raise RuntimeError(
                f"ffmpeg normalisation failed (exit={result.returncode}): "
                f"{result.stderr[-600:]}"
            )

        before = measured or _measure(src, target, tp, lra) or {}
        after = _measure(out_path, target, tp, lra) or {}
        out_lufs = _f(after.get("input_i"))
        deviation = (out_lufs - target) if out_lufs is not None else None
        on_target = abs(deviation) <= 1.0 if deviation is not None else None

        logger.info(
            "done: %s -> %s LUFS (target %.1f, deviation %s)",
            before.get("input_i"), after.get("input_i"), target,
            f"{deviation:+.2f}" if deviation is not None else "?",
        )

        lines = [
            f"{'ON TARGET' if on_target else 'OFF TARGET'} — "
            f"{out_lufs:.2f} LUFS vs target {target:.1f}"
            if out_lufs is not None else "normalised (measurement unavailable)",
            f"before: {before.get('input_i')} LUFS, "
            f"TP {before.get('input_tp')} dBTP, LRA {before.get('input_lra')}",
            f"after:  {after.get('input_i')} LUFS, "
            f"TP {after.get('input_tp')} dBTP, LRA {after.get('input_lra')}",
            f"mode:   {'two-pass (deterministic)' if two_pass_used else 'single-pass (adaptive, ~1 LU band)'}",
        ]

        return RunOutput(
            media=File(path=out_path),
            target_lufs=target,
            input_lufs=_f(before.get("input_i")),
            output_lufs=out_lufs,
            input_true_peak=_f(before.get("input_tp")),
            output_true_peak=_f(after.get("input_tp")),
            input_lra=_f(before.get("input_lra")),
            output_lra=_f(after.get("input_lra")),
            deviation_lu=round(deviation, 2) if deviation is not None else None,
            on_target=on_target,
            two_pass_used=two_pass_used,
            had_audio=True,
            report="\n".join(lines),
        )
