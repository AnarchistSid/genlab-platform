"""Measure how often a video actually cuts, and whether that suits short-form.

A reel can pass every technical check — right resolution, right codec, right
loudness — and still be one unbroken shot for thirty seconds. Nothing in a
typical QC pipeline notices, because "is this edited?" is not a codec property.

This came out of auditing a video pipeline that published 89 reels a month and
could not explain why they underperformed. Its own aesthetic analyzer had been
recording the answer for weeks: a cut frequency of 0.04–0.16 against a
competitive short-form norm of roughly one cut every 1–3 seconds. The reels
were trimmed source clips with text on top. The number existed; nobody read it.

What this reports:

  * cuts, cuts per second, and average shot length
  * the **longest static stretch** — the single most useful number, because
    that is where a viewer's thumb starts moving
  * a verdict against short-form pacing norms, with the thresholds exposed so
    you can set your own

Detection is ffmpeg's scene-change score, not ML: two passes, CPU only, a few
seconds. It sees hard cuts reliably; slow dissolves and whip-pans are the known
blind spot, which is why the raw per-shot list is returned rather than just a
score.
"""
import logging
import re
import shutil
import subprocess
from typing import List, Optional

from inferencesh import BaseApp, BaseAppSetup, File
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_FFPROBE_TIMEOUT = 60
_FFMPEG_TIMEOUT = 300


class Shot(BaseModel):
    index: int = Field(description="0-based shot number.")
    start_seconds: float = Field(description="Where the shot begins.")
    duration_seconds: float = Field(description="How long it holds.")


class AppSetup(BaseAppSetup):
    """Stateless — analysis is pure ffmpeg."""


class RunInput(BaseModel):
    video: File = Field(description="The video to analyse (mp4/mov/webm).")
    scene_threshold: float = Field(
        0.30,
        description="ffmpeg scene-change score that counts as a cut, 0-1. "
        "0.30 suits most edited footage. Lower it (0.15-0.20) for subtle cuts "
        "within a single scene; raise it (0.40+) if camera motion is being "
        "counted as cuts.",
    )
    target_cuts_per_second_min: float = Field(
        0.33,
        description="Lower bound of the pacing you want — 0.33 is a cut every "
        "3 seconds. 0 disables the verdict.",
    )
    target_cuts_per_second_max: float = Field(
        1.50,
        description="Upper bound — above this the edit may read as frantic.",
    )
    max_static_stretch_seconds: float = Field(
        5.0,
        description="Longest single shot you will tolerate before calling it "
        "static. This is usually the number that matters most.",
    )


class RunOutput(BaseModel):
    passed: bool = Field(description="True when pacing is inside your targets.")
    duration_seconds: float = Field(description="Total video length.")
    cut_count: int = Field(description="Hard cuts detected.")
    shot_count: int = Field(description="Shots (cuts + 1).")
    cuts_per_second: float = Field(description="Cut density.")
    avg_shot_seconds: float = Field(description="Mean shot length.")
    median_shot_seconds: float = Field(description="Median shot length — more robust than the mean when one long tail shot dominates.")
    longest_static_seconds: float = Field(description="The longest single unbroken shot.")
    longest_static_at: float = Field(description="Where that stretch starts.")
    verdict: str = Field(description="static | well-paced | frantic | unknown.")
    findings: List[str] = Field(description="What failed, in plain language.")
    shots: List[Shot] = Field(description="Every detected shot, so you can check the detector's work.")
    report: str = Field(description="Human-readable summary.")


def _run(cmd: List[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _duration(path: str) -> float:
    r = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
              "-of", "default=nw=1:nk=1", path], _FFPROBE_TIMEOUT)
    try:
        return float((r.stdout or "0").strip())
    except ValueError:
        return 0.0


def detect_cuts(path: str, threshold: float) -> List[float]:
    """Return cut timestamps in seconds.

    ffmpeg emits one showinfo line per frame that scores above the scene
    threshold; pts_time on those lines is the cut point.
    """
    r = _run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", path,
         "-filter:v", f"select='gt(scene,{threshold})',showinfo",
         "-f", "null", "-"],
        _FFMPEG_TIMEOUT,
    )
    # showinfo writes to stderr by design
    return [float(m) for m in re.findall(r"pts_time:([0-9.]+)", r.stderr or "")]


def build_shots(cuts: List[float], duration: float) -> List[Shot]:
    boundaries = [0.0] + [c for c in cuts if 0.0 < c < duration] + [duration]
    shots: List[Shot] = []
    for i in range(len(boundaries) - 1):
        start, end = boundaries[i], boundaries[i + 1]
        if end - start <= 0.01:      # ignore zero-length artefacts
            continue
        shots.append(Shot(index=len(shots), start_seconds=round(start, 3),
                          duration_seconds=round(end - start, 3)))
    return shots


class App(BaseApp):
    async def setup(self, config: AppSetup):
        logger.info("shot-density-check ready (ffmpeg=%s)", bool(shutil.which("ffmpeg")))

    async def run(self, input_data: RunInput) -> RunOutput:
        path = input_data.video.path
        logger.info("analysing %s at scene threshold %.2f", path, input_data.scene_threshold)

        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            return RunOutput(
                passed=False, duration_seconds=0, cut_count=0, shot_count=0,
                cuts_per_second=0, avg_shot_seconds=0, median_shot_seconds=0,
                longest_static_seconds=0, longest_static_at=0, verdict="unknown",
                findings=["ffmpeg/ffprobe not available"], shots=[],
                report="FAIL: ffmpeg not available in this environment.",
            )

        duration = _duration(path)
        if duration <= 0:
            return RunOutput(
                passed=False, duration_seconds=0, cut_count=0, shot_count=0,
                cuts_per_second=0, avg_shot_seconds=0, median_shot_seconds=0,
                longest_static_seconds=0, longest_static_at=0, verdict="unknown",
                findings=["could not read duration — is this a video file?"],
                shots=[], report="FAIL: no readable video stream.",
            )

        cuts = detect_cuts(path, input_data.scene_threshold)
        shots = build_shots(cuts, duration)
        lengths = sorted(s.duration_seconds for s in shots)
        longest = max(shots, key=lambda s: s.duration_seconds) if shots else None

        cps = len(cuts) / duration if duration else 0.0
        avg = duration / len(shots) if shots else duration
        med = lengths[len(lengths) // 2] if lengths else duration

        findings: List[str] = []
        if input_data.target_cuts_per_second_min > 0:
            if cps < input_data.target_cuts_per_second_min:
                findings.append(
                    f"{cps:.3f} cuts/sec is below the {input_data.target_cuts_per_second_min:.2f} "
                    f"target — roughly one cut every {(1/cps if cps else duration):.0f}s. "
                    "Short-form usually cuts every 1-3s to hold attention."
                )
            elif cps > input_data.target_cuts_per_second_max:
                findings.append(
                    f"{cps:.3f} cuts/sec exceeds {input_data.target_cuts_per_second_max:.2f} "
                    "— the edit may read as frantic."
                )
        if longest and longest.duration_seconds > input_data.max_static_stretch_seconds:
            findings.append(
                f"longest unbroken shot is {longest.duration_seconds:.1f}s starting at "
                f"{longest.start_seconds:.1f}s, over the {input_data.max_static_stretch_seconds:.1f}s "
                "limit — this is where a viewer scrolls."
            )

        if cps < input_data.target_cuts_per_second_min:
            verdict = "static"
        elif cps > input_data.target_cuts_per_second_max:
            verdict = "frantic"
        else:
            verdict = "well-paced"

        passed = not findings
        head = "PASS" if passed else "FAIL"
        report = (
            f"{head} — {duration:.1f}s, {len(cuts)} cuts across {len(shots)} shots\n"
            f"  {cps:.3f} cuts/sec (avg shot {avg:.2f}s, median {med:.2f}s)\n"
            f"  longest static stretch: {longest.duration_seconds:.1f}s at "
            f"{longest.start_seconds:.1f}s\n" if longest else ""
        )
        report += "  verdict: " + verdict
        for f in findings:
            report += f"\n  - {f}"

        logger.info("%s: %d cuts, %.3f cuts/sec, verdict=%s", head, len(cuts), cps, verdict)
        return RunOutput(
            passed=passed, duration_seconds=round(duration, 3), cut_count=len(cuts),
            shot_count=len(shots), cuts_per_second=round(cps, 4),
            avg_shot_seconds=round(avg, 3), median_shot_seconds=round(med, 3),
            longest_static_seconds=round(longest.duration_seconds, 3) if longest else 0.0,
            longest_static_at=round(longest.start_seconds, 3) if longest else 0.0,
            verdict=verdict, findings=findings, shots=shots, report=report,
        )
