"""Find the gap between how long a file claims to be and how long it can actually be decoded.

A media container reports one duration. Its video stream reports another. The
number of frames you can actually decode implies a third. These disagree more
often than people expect, and the disagreement is invisible in every player --
which is exactly why it breaks tooling instead of playback.

This came out of a real failure. A third-party video analyser refused five
different files with "Could not extract frame at 16.768s". The files were fine:
16.810s by the container, 16.767s by the video stream, 503 frames at 30fps. The
AAC tail ran roughly 43ms past the last video frame, so any tool that computes
"the end" from the container's duration seeks into a gap where no frame exists.
ffmpeg fails there too, in both seek modes. Nothing is corrupt; two correct
numbers simply mean different things.

That is a nasty bug to chase, because the input looks healthy everywhere you
would normally look. So this app does not just compare metadata -- it actually
attempts the seek and reports whether it worked:

  * container duration, per-stream durations, and the drift between them
  * decoded frame count and the duration those frames actually imply
  * ``last_decodable_frame_seconds`` -- the real, tested end of the video
  * ``seek_at_container_end_fails`` -- an empirical result, not a prediction
  * a recommended seek strategy for whoever consumes the file next

Useful before you hand video to any tool that trims, loops, thumbnails, or
samples the final frame. CPU only, one ffprobe pass plus two short seek probes.
"""
import logging
import shutil
import subprocess
from typing import List, Optional

from inferencesh import BaseApp, BaseAppSetup, File
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_FFPROBE_TIMEOUT = 60
_FFMPEG_TIMEOUT = 120

# Below this, the drift is ordinary encoder padding and harms nothing.
# Above it, tools that derive "the end" from the container start failing.
_DRIFT_WARN_SECONDS = 0.005


class StreamDuration(BaseModel):
    index: int = Field(description="Stream index within the container.")
    codec_type: str = Field(description="video, audio, subtitle, ...")
    codec_name: str = Field(description="Codec of this stream.")
    duration_seconds: Optional[float] = Field(
        None, description="Duration this stream declares, if it declares one."
    )


class AppSetup(BaseAppSetup):
    """Stateless -- everything is ffprobe/ffmpeg."""


class RunInput(BaseModel):
    video: File = Field(description="The media file to inspect (mp4/mov/mkv/webm).")
    count_frames: bool = Field(
        True,
        description="Decode the whole video stream to count frames exactly. This is "
        "the only way to know the true end, but it costs a full pass. Turn it off "
        "for very long files and rely on stream metadata instead.",
    )
    probe_seek: bool = Field(
        True,
        description="Actually attempt a frame extraction at the container's reported "
        "end. This is what turns a metadata comparison into a reproduction of the "
        "bug, so leave it on unless you only want the numbers.",
    )


class RunOutput(BaseModel):
    container_duration_seconds: Optional[float] = Field(
        None, description="Duration the container declares. Usually the longest stream."
    )
    video_duration_seconds: Optional[float] = Field(
        None, description="Duration the video stream declares."
    )
    audio_duration_seconds: Optional[float] = Field(
        None, description="Duration the audio stream declares."
    )
    streams: List[StreamDuration] = Field(
        default_factory=list, description="Every stream and the duration it claims."
    )

    decoded_frame_count: Optional[int] = Field(
        None, description="Video frames actually decoded. Null if count_frames was off."
    )
    frame_rate: Optional[float] = Field(None, description="Average frame rate.")
    frames_imply_seconds: Optional[float] = Field(
        None, description="decoded_frame_count / frame_rate -- duration the frames imply."
    )

    drift_seconds: Optional[float] = Field(
        None,
        description="container_duration - video_duration. Positive means the file "
        "claims to run longer than its video does, which is the dangerous direction.",
    )
    last_decodable_frame_seconds: Optional[float] = Field(
        None, description="Timestamp of the last frame that could actually be decoded."
    )
    seek_at_container_end_fails: Optional[bool] = Field(
        None,
        description="Tested, not predicted: did extracting a frame just inside the "
        "container's reported end actually fail?",
    )

    safe: bool = Field(
        description="True when the drift is negligible and the end-of-file seek worked."
    )
    verdict: str = Field(description="One line on what was found.")
    recommendation: str = Field(
        description="How a downstream tool should seek the end of this file."
    )


def _ffprobe_json(path: str, args: List[str]) -> dict:
    import json

    cmd = ["ffprobe", "-v", "error", "-of", "json", *args, path]
    proc = subprocess.run(cmd, capture_output=True, timeout=_FFPROBE_TIMEOUT)
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(f"ffprobe failed: {err[:300]}")
    return json.loads((proc.stdout or b"{}").decode("utf-8", "replace") or "{}")


def _parse_rate(value: Optional[str]) -> Optional[float]:
    """r_frame_rate arrives as '30/1'. Also tolerate a plain number."""
    if not value:
        return None
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            den_f = float(den)
            return float(num) / den_f if den_f else None
        return float(value)
    except (ValueError, ZeroDivisionError):
        return None


def _as_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _seek_succeeds(path: str, timestamp: float) -> bool:
    """Try to pull exactly one frame at `timestamp`. True only on a real frame."""
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "probe.png")
        proc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{timestamp:.6f}", "-i", path,
             "-frames:v", "1", out],
            capture_output=True,
            timeout=_FFMPEG_TIMEOUT,
        )
        # A non-zero exit is decisive, but ffmpeg can also exit 0 having written
        # nothing at all when the seek lands past the last frame.
        return proc.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0


def _last_decodable_frame(path: str, near_end: Optional[float]) -> Optional[float]:
    """Timestamp of the final decodable video frame.

    Read only the last couple of seconds rather than the whole stream. ffprobe's
    `-read_intervals` wants an absolute start, so derive one from whichever
    duration we trust most and let it run to the end.

    Deliberately no `+#N` frame cap on the interval: that caps frames *read*
    from the preceding keyframe, which stops short of the end and silently
    reports the wrong last frame.
    """
    start = max((near_end or 0.0) - 2.0, 0.0)
    try:
        data = _ffprobe_json(
            path,
            ["-select_streams", "v:0",
             "-read_intervals", f"{start:.3f}%",
             "-show_entries", "frame=best_effort_timestamp_time,pkt_pts_time"],
        )
    except (RuntimeError, subprocess.TimeoutExpired, ValueError):
        return None
    stamps = []
    for frame in data.get("frames", []):
        for key in ("best_effort_timestamp_time", "pkt_pts_time"):
            value = _as_float(frame.get(key))
            if value is not None:
                stamps.append(value)
    return max(stamps) if stamps else None


class App(BaseApp):
    async def setup(self, setup: AppSetup):
        for binary in ("ffprobe", "ffmpeg"):
            if not shutil.which(binary):
                raise RuntimeError(f"{binary} not found on PATH")
        logger.info("av-duration-drift-check ready")

    async def run(self, input_data: RunInput) -> RunOutput:
        path = input_data.video.path
        logger.info("inspecting %s", path)

        probe = _ffprobe_json(path, ["-show_format", "-show_streams"])
        container = _as_float((probe.get("format") or {}).get("duration"))

        streams: List[StreamDuration] = []
        video_duration = audio_duration = frame_rate = None
        for raw in probe.get("streams", []):
            kind = raw.get("codec_type") or "unknown"
            duration = _as_float(raw.get("duration"))
            streams.append(
                StreamDuration(
                    index=int(raw.get("index", -1)),
                    codec_type=kind,
                    codec_name=raw.get("codec_name") or "unknown",
                    duration_seconds=duration,
                )
            )
            if kind == "video" and video_duration is None:
                video_duration = duration
                frame_rate = _parse_rate(raw.get("avg_frame_rate")) or _parse_rate(
                    raw.get("r_frame_rate")
                )
            elif kind == "audio" and audio_duration is None:
                audio_duration = duration

        logger.info(
            "container=%s video=%s audio=%s fps=%s",
            container, video_duration, audio_duration, frame_rate,
        )

        frame_count = frames_imply = None
        if input_data.count_frames:
            counted = _ffprobe_json(
                path,
                ["-select_streams", "v:0", "-count_frames",
                 "-show_entries", "stream=nb_read_frames"],
            )
            for raw in counted.get("streams", []):
                try:
                    frame_count = int(raw.get("nb_read_frames"))
                except (TypeError, ValueError):
                    frame_count = None
                break
            if frame_count and frame_rate:
                frames_imply = frame_count / frame_rate
            logger.info("decoded %s frames -> %s s", frame_count, frames_imply)

        drift = None
        if container is not None and video_duration is not None:
            drift = container - video_duration

        # Prefer the video stream's own duration as the hint; fall back to the
        # container, then to whatever the frames implied.
        hint = video_duration or container or frames_imply
        last_frame = _last_decodable_frame(path, hint)

        seek_fails = None
        if input_data.probe_seek and container is not None:
            # Just inside the container's claimed end -- where naive tools look.
            target = max(container - 0.042, 0.0)
            seek_fails = not _seek_succeeds(path, target)
            logger.info("seek probe at %.3fs -> %s", target,
                        "FAILED" if seek_fails else "ok")

        drifted = drift is not None and drift > _DRIFT_WARN_SECONDS
        safe = not drifted and not bool(seek_fails)

        if seek_fails:
            verdict = (
                f"Container claims {container:.3f}s but a frame could not be "
                f"extracted there. The video stream ends at "
                f"{video_duration if video_duration is not None else float('nan'):.3f}s"
                f"{f' and the last decodable frame is at {last_frame:.3f}s' if last_frame is not None else ''}."
            )
            recommendation = (
                "Do not derive the end from format.duration. Use '-sseof' to read "
                "backwards from the end, or clamp any seek target to the video "
                "stream's own duration."
            )
        elif drifted:
            verdict = (
                f"Container runs {drift:.3f}s longer than the video stream "
                f"(usually an audio tail). The end-of-file seek still worked, but "
                f"tools that trust format.duration are one rounding step from failing."
            )
            recommendation = (
                "Clamp seek targets to the video stream duration. Remux with "
                "'-shortest' if you want the two numbers to agree."
            )
        else:
            verdict = (
                "Container and video stream agree, and a frame was extracted at the "
                "end of the file."
            )
            recommendation = "No action needed; seeking by container duration is safe."

        return RunOutput(
            container_duration_seconds=container,
            video_duration_seconds=video_duration,
            audio_duration_seconds=audio_duration,
            streams=streams,
            decoded_frame_count=frame_count,
            frame_rate=frame_rate,
            frames_imply_seconds=frames_imply,
            drift_seconds=drift,
            last_decodable_frame_seconds=last_frame,
            seek_at_container_end_fails=seek_fails,
            safe=safe,
            verdict=verdict,
            recommendation=recommendation,
        )
