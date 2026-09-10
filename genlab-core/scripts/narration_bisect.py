#!/usr/bin/env python3
"""Bisect the narration chain: find WHICH link drops the voice-over.

Built 2026-09-10 after three separate mechanisms were confidently reported and
each falsified by the next query. The failure is not hard to fix once located —
it is hard to LOCATE, because every link reports success in isolation:

  * ``GenerateAudio`` logs ``1 generated, 0 skipped, 0 errors``
  * ``audio_replacer`` logs ``mixing: source=... music=...`` identically
    whether or not a VO was included — the line never names narration
  * the reel renders, validates, and passes every spec gate
  * the only symptom is audible, and only if you listen

So this walks the chain one link at a time and reports the first that breaks.
Each check is independent: a later link is still probed when an earlier one
fails, because "L1 and L3 both broken" is a different repair from "L1 broken".

    L1  artifact       VO file exists, non-empty, at the path the producer
                       publishes under this run_id
    L2  ctx key        the render layer maps media["audio_path"] into
                       blueprint_context["narration_audio_path"]
    L3  filtergraph    audio_replacer builds a THREE-input ffmpeg command
                       when handed that path (argv inspected, not logs)
    L4  output         the rendered mix actually contains the VO — measured
                       two ways, transcript and speech-band energy

Usage:
    narration_bisect.py --run-id ai_creators_20260910_023010 --niche ai_creators
    narration_bisect.py --run-id ... --reel /path/to/final.mp4   # adds L4

L4 needs whisper; without it the check degrades to the energy measure alone and
SAYS SO rather than reporting a pass it did not earn.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

OK, FAIL, SKIP = "PASS", "FAIL", "SKIP"


class Result:
    def __init__(self, link: str, name: str):
        self.link, self.name = link, name
        self.status, self.detail, self.evidence = SKIP, "", ""

    def set(self, status: str, detail: str, evidence: str = ""):
        self.status, self.detail, self.evidence = status, detail, evidence
        return self


def l1_artifact(niche: str, run_id: str, stem: str | None) -> Result:
    """Producer wrote a real file at the path it publishes."""
    r = Result("L1", "artifact at producer path")
    run_dir = Path(tempfile.gettempdir()) / "genlab_audio" / f"{niche}_{run_id}"
    if not run_dir.is_dir():
        return r.set(FAIL, f"run dir absent: {run_dir}",
                     "GenerateAudio never created its output directory")
    mp3s = sorted(run_dir.glob("*_audio.mp3"))
    if not mp3s:
        return r.set(FAIL, f"no *_audio.mp3 in {run_dir}",
                     f"dir exists with {len(list(run_dir.iterdir()))} entries")
    if stem:
        match = [p for p in mp3s if p.stem.startswith(stem[:32])]
        if not match:
            return r.set(FAIL, f"no artifact keyed to story {stem[:16]}",
                         f"found instead: {[p.name for p in mp3s]}")
        mp3s = match
    sized = [p for p in mp3s if p.stat().st_size > 0]
    if not sized:
        return r.set(FAIL, "artifact exists but is zero bytes",
                     "the success claim is not backed by a usable file")
    p = sized[0]
    return r.set(OK, f"{p.name} ({p.stat().st_size:,} B)", str(p))


def l2_ctx_key(audio_path: str) -> Result:
    """Render layer carries the producer's path into blueprint_context.

    Exercises the real mapping rather than re-implementing it: a re-implemented
    check passes when the mapping is broken, which is the opposite of useful.
    """
    r = Result("L2", "ctx key at orchestrator entry")
    src = Path("genlab-core/src/genlab_core/strategies/base_visual_render.py")
    if not src.exists():
        src = Path(__file__).resolve().parents[1] / "src/genlab_core/strategies/base_visual_render.py"
    if not src.exists():
        return r.set(SKIP, "base_visual_render.py not found", "")
    text = src.read_text()
    m = re.search(r'"narration_audio_path":\s*([^,\n]+)', text)
    if not m:
        return r.set(FAIL, "no narration_audio_path mapping in blueprint_context",
                     "the orchestrator reads a key nothing writes")
    expr = m.group(1).strip()
    story = {"media": {"audio_path": audio_path}}
    media = story.get("media") or {}
    try:
        value = eval(expr, {"__builtins__": {}}, {"media": media, "story": story})
    except Exception as exc:
        return r.set(FAIL, f"mapping expr not evaluable: {expr} ({exc})", expr)
    if not value:
        return r.set(FAIL, f"mapping yields empty from media['audio_path']", expr)
    return r.set(OK, f"maps via {expr}", str(value))


def l3_filtergraph(audio_path: str) -> Result:
    """audio_replacer builds a 3-input command when handed a VO path.

    Inspects argv. The log line names only source and music regardless of the
    path taken, so logs cannot answer this and never could.
    """
    r = Result("L3", "filtergraph argv")
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from genlab_core.media.audio_replacer import (  # type: ignore
            AudioMixSpec, build_ffmpeg_command,
        )
    except Exception as exc:
        return r.set(SKIP, f"import failed: {exc}", "")
    try:
        spec = AudioMixSpec(
            source_video_path=Path("/tmp/_bisect_src.mp4"),
            music_bed_path=Path("/tmp/_bisect_music.mp3"),
            output_path=Path("/tmp/_bisect_out.mp4"),
            narration_audio_path=Path(audio_path),
        )
        argv = build_ffmpeg_command(spec, "ffmpeg")
    except Exception as exc:
        return r.set(FAIL, f"spec/command build raised: {exc}", "")
    inputs = [argv[i + 1] for i, a in enumerate(argv) if a == "-i"]
    has_vo = any(Path(audio_path).name in str(x) for x in inputs)
    graph = next((argv[i + 1] for i, a in enumerate(argv)
                  if a in ("-filter_complex", "-af")), "")
    if len(inputs) < 3 or not has_vo:
        return r.set(FAIL, f"{len(inputs)} inputs, VO present={has_vo}",
                     f"inputs={inputs}")
    return r.set(OK, f"{len(inputs)} inputs incl. VO", f"graph={graph[:110]}")


def _measure_band(path: Path, lo: int, hi: int) -> float | None:
    """Mean volume in a frequency band, dB. Speech energy lives 300-3400 Hz."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
             "-af", f"highpass=f={lo},lowpass=f={hi},volumedetect",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=180,
        )
        m = re.search(r"mean_volume:\s*(-?\d+\.?\d*) dB", proc.stderr)
        return float(m.group(1)) if m else None
    except Exception:
        return None


def l4_output(reel: Path, script: str | None) -> Result:
    """The mix actually contains the VO.

    Two independent measures, because each alone is weak: a transcript can miss
    a quiet VO under louder source speech, and band energy cannot tell VO from
    any other speech in the clip.
    """
    r = Result("L4", "VO present in output")
    if not reel.exists():
        return r.set(SKIP, f"reel not found: {reel}", "")
    band = _measure_band(reel, 300, 3400)
    transcript = None
    try:
        import whisper  # type: ignore
        transcript = whisper.load_model("base").transcribe(
            str(reel), language="en")["text"].strip()
    except Exception:
        pass
    if transcript is None:
        return r.set(SKIP, "whisper unavailable — transcript check could not run",
                     f"speech-band energy {band} dB (cannot attribute to VO)")
    if script:
        words = [w.lower().strip(".,!?") for w in script.split() if len(w) > 5]
        hits = [w for w in words if w in transcript.lower()]
        ratio = len(hits) / max(1, len(words))
        ev = f"{len(hits)}/{len(words)} script words present; band {band} dB"
        if ratio < 0.15:
            return r.set(FAIL, f"VO absent from mix ({ratio:.0%} script overlap)", ev)
        return r.set(OK, f"VO present ({ratio:.0%} script overlap)", ev)
    return r.set(SKIP, "no --script supplied to compare against",
                 f"transcript: {transcript[:90]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--niche", required=True)
    ap.add_argument("--story-id", default=None)
    ap.add_argument("--reel", default=None)
    ap.add_argument("--script", default=None)
    a = ap.parse_args()

    results = [l1_artifact(a.niche, a.run_id, a.story_id)]
    audio = results[0].evidence if results[0].status == OK else ""
    results.append(l2_ctx_key(audio) if audio
                   else Result("L2", "ctx key at orchestrator entry")
                   .set(SKIP, "no artifact from L1 to carry", ""))
    results.append(l3_filtergraph(audio) if audio
                   else Result("L3", "filtergraph argv")
                   .set(SKIP, "no artifact from L1 to mix", ""))
    results.append(l4_output(Path(a.reel), a.script) if a.reel
                   else Result("L4", "VO present in output")
                   .set(SKIP, "no --reel supplied", ""))

    print(f"\nnarration bisection — run_id={a.run_id} niche={a.niche}")
    print("=" * 78)
    for r in results:
        print(f"  {r.status:4s} {r.link}  {r.name:32s} {r.detail}")
        if r.evidence:
            print(f"         {'':4s} {'':32s} {r.evidence[:96]}")
    print("=" * 78)
    broken = [r for r in results if r.status == FAIL]
    skipped = [r for r in results if r.status == SKIP]
    if broken:
        print(f"FIRST BROKEN LINK: {broken[0].link} — {broken[0].name}")
        print(f"  fix here: {broken[0].detail}")
    elif skipped:
        print(f"NO BROKEN LINK FOUND, but {len(skipped)} check(s) could not run "
              f"({', '.join(s.link for s in skipped)}) — this is NOT a clean bill of health.")
    else:
        print("ALL LINKS PASS — VO reaches the output.")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
