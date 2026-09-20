"""reel-spec-check — validate a video against short-form platform specs.

## What it does

Point it at a rendered reel and get a pass/fail report covering the things
Instagram Reels / YouTube Shorts / TikTok / Facebook actually care about:

  * geometry      1080x1920, 9:16 portrait
  * colour        bt709 primaries / transfer / matrix
  * codecs        h264 video, aac audio at 48 kHz stereo
  * duration      inside the platform window
  * loudness      EBU R128 integrated LUFS + true peak
  * voice         whether a narration track is actually audible in the mix
  * fit           whether a separate voice-over would be truncated by the video

The last two are the ones that are hard to eyeball and easy to ship broken.

## Why the voice checks exist

A reel can satisfy every structural check and still be silently wrong. Two
failure modes cost real debugging time in the pipeline this was extracted
from:

1. **Narration that never reached the mix.** The audio graph was correct, the
   TTS ran, the logs said "1 generated" — but the voice-over was produced
   downstream of the stage that consumed it, so every published reel had
   music and source audio only. Nothing in the file was malformed; the voice
   was simply absent. `likely_has_voice` catches that.

2. **Narration truncated mid-sentence.** A 29.9 s voice-over mixed against an
   18.6 s reel loses 11.4 s to `amix duration=first`. Whether that matters
   depends entirely on *what* got cut — 300 ms of trailing TTS silence is
   free, a clipped sentence is not. `voiceover_truncated_tail_is_speech`
   answers that specific question.

## How voice detection works

No ASR. Bandpass the audio to 1000-3400 Hz (the speech formant band) and
measure mean volume. Speech raises that band far above a music bed. Measured
on a real reel: music + ducked source audio sat at -40.6 dB in that band; the
same mix with narration sat at -27.7 dB. The default threshold of -35 dB sits
in that gap and is exposed as an input so it can be tuned per content style.

Two ffmpeg passes, no model, runs on CPU in about a second.
"""
import json
import logging
import re
import shutil
import subprocess
from typing import List, Optional

from inferencesh import BaseApp, BaseAppSetup, File
from pydantic import BaseModel, Field

# The installed SDK's BaseApp does not expose ``self.logger`` despite the
# docs sample using it — module-level logger, same as sibling apps.
logger = logging.getLogger(__name__)

# Speech formant band. Chosen to sit above music-bed fundamentals and below
# cymbal/hiss energy, so it responds mostly to voice.
_SPEECH_HP_HZ = 1000
_SPEECH_LP_HZ = 3400

_FFPROBE_TIMEOUT = 30
_FFMPEG_TIMEOUT = 300


class AppSetup(BaseAppSetup):
    """No setup config — the app is a thin ffmpeg/ffprobe wrapper."""
    pass


class RunInput(BaseModel):
    """A rendered reel, plus optional expectations to check it against."""

    video: File = Field(
        description="Rendered video to validate (mp4/mov). Everything else "
        "is optional tuning of what counts as a pass.",
    )
    voiceover: Optional[File] = Field(
        None,
        description="Optional separate voice-over track. When supplied, the "
        "app reports whether it fits inside the video and — if not — whether "
        "the part that would be cut contains speech or just trailing silence.",
    )

    expected_width: int = Field(
        1080, description="Required frame width. 0 disables the check.",
    )
    expected_height: int = Field(
        1920, description="Required frame height. 0 disables the check.",
    )
    min_duration_seconds: float = Field(
        15.0, description="Shortest acceptable duration. 0 disables.",
    )
    max_duration_seconds: float = Field(
        60.0, description="Longest acceptable duration. 0 disables.",
    )
    require_bt709: bool = Field(
        True,
        description="Require bt709 colour primaries/transfer/matrix. Meta and "
        "YouTube reject some non-bt709 tags on upload.",
    )
    expected_audio_sample_rate: int = Field(
        48000, description="Required audio sample rate in Hz. 0 disables.",
    )
    target_lufs: float = Field(
        -14.0, description="Target EBU R128 integrated loudness.",
    )
    lufs_tolerance: float = Field(
        1.0, description="Allowed deviation from target_lufs, in LU.",
    )
    speech_band_threshold_db: float = Field(
        -35.0,
        description="Mean volume in the 1-3.4 kHz speech band above which the "
        "audio is treated as containing voice. Default sits between a "
        "measured music-only mix (-40.6 dB) and the same mix with narration "
        "(-27.7 dB).",
    )
    voiceover_tail_speech_threshold_db: float = Field(
        -55.0,
        description="Speech-band level above which the TRUNCATED TAIL of an "
        "overrunning voice-over counts as speech rather than silence. "
        "Deliberately separate from speech_band_threshold_db: that one "
        "discriminates voice inside a full 3-track mix (music-only measured "
        "-40.6 dB, with narration -27.7 dB), while this one discriminates "
        "speech from silence on a CLEAN voice track, where speech sits near "
        "-37 dB and trailing silence far below. Reusing a single threshold "
        "for both makes the tail check misfire on exactly the case it exists "
        "to catch.",
    )
    voiceover_fit_tolerance_seconds: float = Field(
        0.5,
        description="How much longer than the video a voice-over may run "
        "before it counts as overrunning. TTS commonly carries 200-400 ms of "
        "trailing silence that can be clipped harmlessly.",
    )


class RunOutput(BaseModel):
    """Structured verdict plus every measurement behind it."""

    passed: bool = Field(description="True when no check failed.")
    failures: List[str] = Field(description="Checks that failed.")
    warnings: List[str] = Field(
        description="Things worth a look that are not hard failures.",
    )
    report: str = Field(description="Human-readable summary.")

    width: Optional[int] = Field(None, description="Frame width in pixels.")
    height: Optional[int] = Field(None, description="Frame height in pixels.")
    duration_seconds: Optional[float] = Field(None, description="Duration.")
    video_codec: Optional[str] = Field(None, description="Video codec name.")
    color_primaries: Optional[str] = Field(None, description="Colour primaries.")
    color_transfer: Optional[str] = Field(None, description="Transfer function.")
    color_space: Optional[str] = Field(None, description="Matrix coefficients.")

    has_audio: bool = Field(False, description="An audio stream is present.")
    audio_codec: Optional[str] = Field(None, description="Audio codec name.")
    sample_rate: Optional[int] = Field(None, description="Audio sample rate.")
    channels: Optional[int] = Field(None, description="Audio channel count.")

    integrated_lufs: Optional[float] = Field(
        None, description="EBU R128 integrated loudness of the final audio.",
    )
    true_peak_dbtp: Optional[float] = Field(
        None, description="True peak in dBTP.",
    )

    speech_band_mean_db: Optional[float] = Field(
        None, description="Mean volume in the 1-3.4 kHz speech band.",
    )
    likely_has_voice: Optional[bool] = Field(
        None,
        description="Whether the mix appears to contain speech, from the "
        "speech-band measurement. Heuristic, not ASR.",
    )

    voiceover_seconds: Optional[float] = Field(
        None, description="Duration of the supplied voice-over.",
    )
    voiceover_fits: Optional[bool] = Field(
        None, description="Voice-over fits inside the video within tolerance.",
    )
    voiceover_overrun_seconds: Optional[float] = Field(
        None, description="How far the voice-over exceeds the video.",
    )
    voiceover_truncated_tail_is_speech: Optional[bool] = Field(
        None,
        description="When it overruns: whether the segment that would be cut "
        "contains speech rather than trailing silence. True means a sentence "
        "gets clipped.",
    )


def _run(cmd: List[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _probe_json(path: str) -> dict:
    """Full ffprobe stream/format dump. Returns {} on any failure."""
    result = _run(
        [
            "ffprobe", "-v", "error",
            "-show_streams", "-show_format",
            "-of", "json", path,
        ],
        _FFPROBE_TIMEOUT,
    )
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def _duration(path: str) -> Optional[float]:
    result = _run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path,
        ],
        _FFPROBE_TIMEOUT,
    )
    raw = (result.stdout or "").strip()
    try:
        return float(raw)
    except ValueError:
        return None


def _mean_volume_db(
    path: str,
    highpass: Optional[int] = None,
    lowpass: Optional[int] = None,
    start: Optional[float] = None,
    duration: Optional[float] = None,
) -> Optional[float]:
    """Mean volume in dBFS, optionally band-limited and time-windowed."""
    filters = []
    if highpass:
        filters.append(f"highpass=f={highpass}")
    if lowpass:
        filters.append(f"lowpass=f={lowpass}")
    filters.append("volumedetect")

    cmd = ["ffmpeg", "-hide_banner", "-nostats"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", path]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-af", ",".join(filters), "-f", "null", "-"]

    result = _run(cmd, _FFMPEG_TIMEOUT)
    match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", result.stderr or "")
    return float(match.group(1)) if match else None


def _loudness(path: str, target_lufs: float) -> tuple[Optional[float], Optional[float]]:
    """(integrated LUFS, true peak dBTP) via a loudnorm analysis pass."""
    result = _run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-i", path,
            "-af",
            f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:print_format=json",
            "-f", "null", "-",
        ],
        _FFMPEG_TIMEOUT,
    )
    match = re.search(r"\{[^{}]*input_i[^{}]*\}", result.stderr or "", re.S)
    if not match:
        return None, None
    try:
        data = json.loads(match.group(0))
        return float(data["input_i"]), float(data["input_tp"])
    except (json.JSONDecodeError, KeyError, ValueError):
        return None, None


class App(BaseApp):
    async def setup(self, config: AppSetup):
        """Fail loud at startup if the runtime image lacks ffmpeg."""
        for binary in ("ffmpeg", "ffprobe"):
            if not shutil.which(binary):
                raise RuntimeError(
                    f"{binary} not found on PATH — this app requires ffmpeg "
                    "in the runtime image."
                )
        logger.info("reel-spec-check ready; ffmpeg and ffprobe present")

    async def run(self, input_data: RunInput) -> RunOutput:
        path = input_data.video.path
        logger.info(f"probing {path}")

        probe = _probe_json(path)
        streams = probe.get("streams") or []
        video = next((s for s in streams if s.get("codec_type") == "video"), {})
        audio = next((s for s in streams if s.get("codec_type") == "audio"), {})

        if not video:
            return RunOutput(
                passed=False,
                failures=["no video stream found — is this a media file?"],
                warnings=[],
                report="FAIL: no video stream.",
            )

        failures: List[str] = []
        warnings: List[str] = []

        width = video.get("width")
        height = video.get("height")
        duration = _duration(path)
        vcodec = video.get("codec_name")
        prim = video.get("color_primaries") or "unknown"
        trc = video.get("color_transfer") or "unknown"
        space = video.get("color_space") or "unknown"

        logger.info(
            f"video: {width}x{height} {vcodec} {duration}s colours={prim}|{trc}|{space}"
        )

        if input_data.expected_width and width != input_data.expected_width:
            failures.append(f"width {width} != {input_data.expected_width}")
        if input_data.expected_height and height != input_data.expected_height:
            failures.append(f"height {height} != {input_data.expected_height}")
        if vcodec and vcodec != "h264":
            warnings.append(f"video codec is {vcodec}, not h264")
        if input_data.require_bt709 and not (
            prim == "bt709" and trc == "bt709" and space == "bt709"
        ):
            failures.append(
                f"colour tags {prim}|{trc}|{space} are not all bt709 — some "
                "platforms reject this on upload"
            )
        if duration is not None:
            if input_data.min_duration_seconds and duration < input_data.min_duration_seconds:
                failures.append(
                    f"duration {duration:.2f}s below minimum "
                    f"{input_data.min_duration_seconds}s"
                )
            if input_data.max_duration_seconds and duration > input_data.max_duration_seconds:
                failures.append(
                    f"duration {duration:.2f}s above maximum "
                    f"{input_data.max_duration_seconds}s"
                )

        has_audio = bool(audio)
        acodec = audio.get("codec_name") if audio else None
        rate = int(audio["sample_rate"]) if audio.get("sample_rate") else None
        channels = audio.get("channels") if audio else None

        lufs = peak = speech_db = None
        likely_voice = None

        if not has_audio:
            failures.append("no audio stream — the reel is silent")
        else:
            logger.info(f"audio: {acodec} {rate}Hz {channels}ch")
            if acodec and acodec != "aac":
                warnings.append(f"audio codec is {acodec}, not aac")
            if input_data.expected_audio_sample_rate and rate != input_data.expected_audio_sample_rate:
                failures.append(
                    f"sample rate {rate} != {input_data.expected_audio_sample_rate} "
                    "— loudnorm can silently resample to 96 kHz"
                )
            if channels and channels != 2:
                warnings.append(f"{channels} audio channels, expected stereo")

            lufs, peak = _loudness(path, input_data.target_lufs)
            if lufs is not None:
                logger.info(f"loudness: {lufs} LUFS, peak {peak} dBTP")
                if abs(lufs - input_data.target_lufs) > input_data.lufs_tolerance:
                    failures.append(
                        f"integrated loudness {lufs:.2f} LUFS is outside "
                        f"{input_data.target_lufs} +/- {input_data.lufs_tolerance} LU"
                    )
            else:
                warnings.append("could not measure loudness")

            speech_db = _mean_volume_db(
                path, highpass=_SPEECH_HP_HZ, lowpass=_SPEECH_LP_HZ,
            )
            if speech_db is not None:
                likely_voice = speech_db > input_data.speech_band_threshold_db
                logger.info(
                    f"speech band mean {speech_db:.1f} dB -> "
                    f"likely_has_voice={likely_voice}"
                )
                if not likely_voice:
                    warnings.append(
                        f"speech-band energy {speech_db:.1f} dB is below "
                        f"{input_data.speech_band_threshold_db} dB — this reel "
                        "may have no audible narration"
                    )

        vo_seconds = vo_fits = vo_overrun = vo_tail_speech = None
        if input_data.voiceover is not None:
            vo_path = input_data.voiceover.path
            vo_seconds = _duration(vo_path)
            logger.info(f"voiceover duration {vo_seconds}s vs video {duration}s")
            if vo_seconds is not None and duration is not None:
                vo_overrun = max(0.0, vo_seconds - duration)
                vo_fits = vo_overrun <= input_data.voiceover_fit_tolerance_seconds
                if not vo_fits:
                    # Measure ONLY the segment that would be cut. Trailing
                    # silence is free; clipped speech is not.
                    tail_db = _mean_volume_db(
                        vo_path,
                        highpass=_SPEECH_HP_HZ,
                        lowpass=_SPEECH_LP_HZ,
                        start=duration,
                    )
                    if tail_db is not None:
                        vo_tail_speech = (
                            tail_db > input_data.voiceover_tail_speech_threshold_db
                        )
                        logger.info(
                            f"truncated tail speech band {tail_db:.1f} dB -> "
                            f"is_speech={vo_tail_speech}"
                        )
                    if vo_tail_speech:
                        failures.append(
                            f"voice-over overruns the video by {vo_overrun:.2f}s "
                            "and the truncated tail contains speech — a "
                            "sentence will be cut mid-word"
                        )
                    else:
                        warnings.append(
                            f"voice-over overruns the video by {vo_overrun:.2f}s, "
                            "but the truncated part appears to be silence"
                        )

        passed = not failures
        lines = [
            f"{'PASS' if passed else 'FAIL'} — {width}x{height} {vcodec}, "
            f"{duration:.2f}s" if duration else f"{'PASS' if passed else 'FAIL'}",
        ]
        if has_audio:
            lines.append(f"audio: {acodec} {rate}Hz {channels}ch")
        if lufs is not None:
            lines.append(f"loudness: {lufs:.2f} LUFS (peak {peak:.2f} dBTP)")
        if speech_db is not None:
            lines.append(
                f"speech band: {speech_db:.1f} dB -> "
                f"{'voice detected' if likely_voice else 'NO VOICE DETECTED'}"
            )
        if vo_seconds is not None:
            lines.append(
                f"voice-over: {vo_seconds:.2f}s, "
                f"{'fits' if vo_fits else f'overruns by {vo_overrun:.2f}s'}"
            )
        for f in failures:
            lines.append(f"  FAIL  {f}")
        for w in warnings:
            lines.append(f"  warn  {w}")

        logger.info(f"verdict passed={passed} failures={len(failures)}")

        return RunOutput(
            passed=passed,
            failures=failures,
            warnings=warnings,
            report="\n".join(lines),
            width=width,
            height=height,
            duration_seconds=duration,
            video_codec=vcodec,
            color_primaries=prim,
            color_transfer=trc,
            color_space=space,
            has_audio=has_audio,
            audio_codec=acodec,
            sample_rate=rate,
            channels=channels,
            integrated_lufs=lufs,
            true_peak_dbtp=peak,
            speech_band_mean_db=speech_db,
            likely_has_voice=likely_voice,
            voiceover_seconds=vo_seconds,
            voiceover_fits=vo_fits,
            voiceover_overrun_seconds=vo_overrun,
            voiceover_truncated_tail_is_speech=vo_tail_speech,
        )
