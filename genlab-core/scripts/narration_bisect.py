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


def l35_argv_input_is_artifact(audio_path: str) -> Result:
    """The VO slot in argv points at the artifact L1 found -- and that file
    really contains the narration.

    L3 proved a 3-input command is BUILT. It did not prove the third input is
    the right file, nor that the file holds speech. A command wired to a stale
    or silent artifact is indistinguishable from a correct one at the argv
    level.
    """
    r = Result("L3.5", "argv VO input == L1 artifact")
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from genlab_core.media.audio_replacer import (  # type: ignore
            AudioMixSpec, build_ffmpeg_command,
        )
        spec = AudioMixSpec(
            source_video_path=Path("/tmp/_b_src.mp4"),
            music_bed_path=Path("/tmp/_b_music.mp3"),
            output_path=Path("/tmp/_b_out.mp4"),
            narration_audio_path=Path(audio_path),
        )
        argv = build_ffmpeg_command(spec, "ffmpeg")
    except Exception as exc:
        return r.set(SKIP, f"command build failed: {exc}", "")
    inputs = [argv[i + 1] for i, a in enumerate(argv) if a == "-i"]
    if len(inputs) < 3:
        return r.set(FAIL, f"only {len(inputs)} inputs", f"{inputs}")
    if str(inputs[2]) != str(audio_path):
        return r.set(FAIL, "input[2] is not the L1 artifact",
                     f"argv={inputs[2]} vs L1={audio_path}")
    dur = _probe_duration(Path(audio_path))
    band = _measure_band(Path(audio_path), 300, 3400)
    if dur is None or dur < 1.0:
        return r.set(FAIL, f"VO artifact has no usable duration ({dur})", "")
    if band is not None and band < -50:
        return r.set(FAIL, f"VO artifact is effectively silent ({band} dB)", "")
    return r.set(OK, f"input[2] == artifact, {dur:.1f}s speech-band {band} dB",
                 str(inputs[2]))


def l36_map_selects_mix(audio_path: str) -> Result:
    """-map selects the FILTERGRAPH output, not the source's own audio.

    ``-map 0:a`` instead of ``-map [aout]`` yields a video whose audio is the
    untouched source: no music, no VO, and every spec gate still passes. That
    failure is invisible to duration, codec and loudness checks alike.
    """
    r = Result("L3.6", "-map selects mixed stream")
    try:
        from genlab_core.media.audio_replacer import (  # type: ignore
            AudioMixSpec, build_ffmpeg_command,
        )
        argv = build_ffmpeg_command(AudioMixSpec(
            source_video_path=Path("/tmp/_b_src.mp4"),
            music_bed_path=Path("/tmp/_b_music.mp3"),
            output_path=Path("/tmp/_b_out.mp4"),
            narration_audio_path=Path(audio_path)), "ffmpeg")
    except Exception as exc:
        return r.set(SKIP, f"command build failed: {exc}", "")
    maps = [argv[i + 1] for i, a in enumerate(argv) if a == "-map"]
    graph = next((argv[i + 1] for i, a in enumerate(argv)
                  if a == "-filter_complex"), "")
    out_label = re.findall(r"\[(\w+)\]\s*$", graph.strip())
    audio_maps = [m for m in maps if not m.endswith(":v")]
    if not audio_maps:
        return r.set(FAIL, "no audio -map at all", f"maps={maps}")
    picked = audio_maps[0]
    if not picked.startswith("["):
        return r.set(FAIL, f"audio -map is a raw stream, not the mix: {picked}",
                     f"graph ends in {out_label}; source audio would ship unmixed")
    if out_label and picked.strip("[]") != out_label[0]:
        return r.set(FAIL, f"-map {picked} != graph output [{out_label[0]}]",
                     f"maps={maps}")
    return r.set(OK, f"-map {picked} matches graph output", f"maps={maps}")


def l37_amix_arithmetic(audio_path: str) -> Result:
    """amix duration/normalize arithmetic leaves the VO audible.

    ``normalize=1`` (the default) divides every input by the input count, so a
    3-input mix attenuates all of them by 9.54 dB. That is survivable. What is
    not survivable is a duration mode that truncates the mix, or a VO whose
    post-normalize level sits under the source it is supposed to lead.
    """
    r = Result("L3.7", "amix duration/normalize arithmetic")
    try:
        from genlab_core.media.audio_replacer import (  # type: ignore
            AudioMixSpec, build_ffmpeg_command,
        )
        argv = build_ffmpeg_command(AudioMixSpec(
            source_video_path=Path("/tmp/_b_src.mp4"),
            music_bed_path=Path("/tmp/_b_music.mp3"),
            output_path=Path("/tmp/_b_out.mp4"),
            narration_audio_path=Path(audio_path)), "ffmpeg")
        graph = next(argv[i + 1] for i, a in enumerate(argv) if a == "-filter_complex")
    except Exception as exc:
        return r.set(SKIP, f"command build failed: {exc}", "")

    m = re.search(r"amix=([^\[]+)", graph)
    if not m:
        return r.set(FAIL, "no amix in graph", graph[:100])
    opts = dict(kv.split("=", 1) for kv in m.group(1).strip(":").split(":") if "=" in kv)
    n = int(opts.get("inputs", "0"))
    normalize = opts.get("normalize", "1")
    duration = opts.get("duration", "longest")

    levels = {lbl: float(db) for db, lbl in
              re.findall(r"volume=(-?\d+(?:\.\d+)?)dB\[(\w+)\]", graph)}
    atten = 0.0 if normalize == "0" else -20 * (n and __import__("math").log10(n) or 0)
    vo_out = levels.get("vo", 0.0) + atten
    src_out = levels.get("src", 0.0) + atten
    detail = (f"inputs={n} normalize={normalize} duration={duration}; "
              f"vo {vo_out:+.1f}dB vs src {src_out:+.1f}dB")

    notes = []
    if vo_out <= src_out:
        notes.append(f"VO does not lead source (+{vo_out - src_out:.1f}dB)")
    if duration == "shortest":
        notes.append("duration=shortest truncates the mix to the briefest input")
    if notes:
        return r.set(FAIL, "; ".join(notes), detail)
    return r.set(OK, f"VO leads source by {vo_out - src_out:.1f}dB", detail)


def l38_execute_argv(audio_path: str, source: Path, music: Path,
                     script: str | None) -> Result:
    """Re-execute the real command in isolation and probe the output.

    The decisive checkpoint. Everything above reasons about the command; this
    RUNS it. If the VO is present here, the graph is sound and the fault is
    that production never reached this path -- a runtime value, not code. If it
    is absent here, the graph itself is the fault. Nothing else separates those
    two, and they need opposite repairs.
    """
    r = Result("L3.8", "re-executed argv output")
    if not source.exists() or not music.exists():
        return r.set(SKIP, "need --source and --music to re-execute", "")
    try:
        from genlab_core.media.audio_replacer import (  # type: ignore
            AudioMixSpec, build_ffmpeg_command,
        )
        out = Path(tempfile.gettempdir()) / "_bisect_l38.mp4"
        argv = build_ffmpeg_command(AudioMixSpec(
            source_video_path=source, music_bed_path=music, output_path=out,
            narration_audio_path=Path(audio_path)), "ffmpeg")
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0 or not out.exists():
            return r.set(FAIL, f"ffmpeg exited {proc.returncode}",
                         proc.stderr.strip().splitlines()[-1][:110] if proc.stderr else "")
    except Exception as exc:
        return r.set(SKIP, f"execution failed: {exc}", "")

    band = _measure_band(out, 300, 3400)
    try:
        import whisper  # type: ignore
        text = whisper.load_model("base").transcribe(str(out), language="en")["text"]
    except Exception:
        return r.set(SKIP, "whisper unavailable — cannot attribute speech to VO",
                     f"speech-band {band} dB; output {out}")
    if not script:
        return r.set(SKIP, "no --script to compare", f"transcript: {text.strip()[:90]}")
    words = [w.lower().strip(".,!?") for w in script.split() if len(w) > 5]
    hits = [w for w in words if w in text.lower()]
    ratio = len(hits) / max(1, len(words))
    ev = f"{len(hits)}/{len(words)} script words; band {band} dB; {out}"
    if ratio < 0.15:
        return r.set(FAIL, f"graph itself drops the VO ({ratio:.0%} overlap)", ev)
    return r.set(OK, f"graph produces audible VO ({ratio:.0%} overlap)", ev)


def _probe_duration(path: Path) -> float | None:
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=60)
        return float(proc.stdout.strip())
    except Exception:
        return None


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
    ap.add_argument("--source", default=None, help="source video for L3.8")
    ap.add_argument("--music", default=None, help="music bed for L3.8")
    a = ap.parse_args()

    results = [l1_artifact(a.niche, a.run_id, a.story_id)]
    audio = results[0].evidence if results[0].status == OK else ""
    results.append(l2_ctx_key(audio) if audio
                   else Result("L2", "ctx key at orchestrator entry")
                   .set(SKIP, "no artifact from L1 to carry", ""))
    results.append(l3_filtergraph(audio) if audio
                   else Result("L3", "filtergraph argv")
                   .set(SKIP, "no artifact from L1 to mix", ""))
    if audio:
        results.append(l35_argv_input_is_artifact(audio))
        results.append(l36_map_selects_mix(audio))
        results.append(l37_amix_arithmetic(audio))
        results.append(l38_execute_argv(audio, Path(a.source or "/nonexistent"),
                                        Path(a.music or "/nonexistent"), a.script))
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
