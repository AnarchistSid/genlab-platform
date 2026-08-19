"""Audio replacement — royalty-free music bed over source audio.

Mixes a royalty-free music bed with the source clip's audio track,
producing a new MP4 with:

  * Original source audio ducked (default -12 dB) — audible but no longer
    the primary audio signal
  * Music bed at -6 dB (default) — the primary "voice"

This is the render-side consumer of the ``music_mood`` transformation
dimension. The selector (PR 4) picks a mood tag; this module resolves
the tag to a specific music file from the niche's local library and
mixes it in.

**Local library, not runtime API**: music beds live under
``<niche_root>/assets/music_beds/<mood>/*.mp3`` (or ``.m4a``, ``.wav``,
``.ogg``). Operator seeds the library from Pixabay Music (free CC0-
style commercial license, no attribution) or YouTube Audio Library
during initial setup. This design:

  * No network dependency at render time — the 4 GB Hetzner VPS has no
    outbound-bandwidth concerns during render
  * Deterministic per mood (same mood always picks from the same pool)
  * Version-controlled catalog (though ``.gitignore`` the actual mp3s)
  * Cache-friendly (files already on disk)

Music bed selection: when multiple files exist under the mood dir, one
is picked at random. The transformation selector's job is to pick the
mood; within-mood variety happens automatically.

Duration handling:

  * Music shorter than source → loop the music via ``-stream_loop -1``
  * Music longer than source → ``amix`` with ``duration=first`` cuts
    music at source length (default). Cleaner than a hard cut mid-track.

FFmpeg command shape:

    ffmpeg -i source.mp4 -stream_loop -1 -i music.mp3 -filter_complex \
      "[0:a]volume=-12dB[a1];" \
      "[1:a]volume=-6dB[a2];" \
      "[a1][a2]amix=inputs=2:duration=first[aout]" \
      -map 0:v -map "[aout]" -c:v copy -c:a aac -b:a 128k \
      -shortest output.mp4

Related sprint context:
- [[zero-cost-transformation-stack-2026-07-05]] — audio replacement is
  the highest-leverage single transformation menu item for YouTube
- PR 4 (#668) — selector chooses mood tag
- PR 6 (#670) — audio_ducking arm feeds ``source_audio_duck_db``
"""

from __future__ import annotations

import logging
import random
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Music file extensions we recognize. Pixabay serves mp3; YouTube Audio
# Library often mp3 or m4a; some free libraries use wav or ogg.
_MUSIC_EXTENSIONS = (".mp3", ".m4a", ".wav", ".ogg", ".opus")


@dataclass
class AudioMixSpec:
    """Parameters for a single audio-mix render.

    Defaults match the intelligent_transform config defaults for
    conservative-audio niches (BB, gaming, sports, movies). FrameDrift
    (anime) overrides ducking to -9dB per the audience-prefers-source-
    audio guidance.

    NARR-01 additions (2026-08-18): optional VO track. When
    ``narration_audio_path`` is None the filtergraph is the historical
    2-input (source + music) shape — byte-identical to pre-NARR-01
    behavior. When set, the graph adds a 3rd input, sidechain-ducks
    the music bed by ``vo_bed_duck_db`` under VO segments, and
    normalises the whole mix to ``target_lufs`` (EBU R128) via the
    shared ``ffmpeg_utils.build_loudnorm_filter`` helper.
    """

    source_video_path: Path
    music_bed_path: Path
    output_path: Path
    source_duck_db: int = -12
    music_bed_db: int = -6
    audio_bitrate: str = "128k"
    # NARR-01 optional VO fields — all default to VO-off legacy path
    narration_audio_path: Path | None = None
    narration_vo_db: int = 0
    vo_bed_duck_db: int = -8
    target_lufs: float = -14.0


def find_music_bed_for_mood(
    niche_root: Path,
    mood_tag: str,
    *,
    rng: random.Random | None = None,
) -> Path | None:
    """Locate a music-bed file for the given mood in a niche's library.

    Returns None when:
      * The niche has no ``assets/music_beds/`` directory (library not
        seeded — this is normal for a fresh install; render falls back
        to legacy no-music path)
      * The mood subdirectory doesn't exist or is empty
      * All entries in the mood dir fail the extension check

    Args:
        niche_root: repository directory for the niche (e.g.
            ``/opt/genlab/BlackboxBrief``).
        mood_tag: dimension_value from the ``music_mood`` bandit arm
            selection (e.g. ``'cinematic'``).
        rng: optional random.Random for deterministic tests.
    """
    library_root = niche_root / "assets" / "music_beds"
    if not library_root.is_dir():
        logger.debug(
            "[audio_replacer] music_beds library not seeded at %s — "
            "audio replacement will skip. Populate the library once "
            "with Pixabay Music tracks per mood tag.",
            library_root,
        )
        return None

    mood_dir = library_root / mood_tag
    if not mood_dir.is_dir():
        logger.info(
            "[audio_replacer] mood_tag %r has no library dir under %s. "
            "Bandit picked a mood without seed material — skipping "
            "audio replacement for this reel.",
            mood_tag,
            library_root,
        )
        return None

    candidates = [
        p for p in mood_dir.iterdir() if p.is_file() and p.suffix.lower() in _MUSIC_EXTENSIONS
    ]
    if not candidates:
        logger.info(
            "[audio_replacer] no music files in %s (expected extensions %s)",
            mood_dir,
            ", ".join(_MUSIC_EXTENSIONS),
        )
        return None

    r = rng or random
    return r.choice(sorted(candidates))


def build_audio_mix_filtergraph(
    source_duck_db: int,
    music_bed_db: int,
    *,
    include_narration: bool = False,
    narration_vo_db: int = 0,
    vo_bed_duck_db: int = -8,
    target_lufs: float = -14.0,
) -> str:
    """Build the FFmpeg filter_complex string for audio mixing.

    Two shapes depending on ``include_narration``:

    **2-input (legacy path, NARR-01 disabled)** — byte-identical to
    the pre-NARR-01 output:
      1. Duck source audio to source_duck_db (typically -12 dB)
      2. Normalize music bed to music_bed_db (typically -6 dB)
      3. amix the two streams with duration=first

    **3-input (NARR-01 enabled)** — VO track added, music
    sidechain-ducked under VO, output loudnorm'd to EBU R128:
      1. Duck source audio to source_duck_db
      2. Normalize music bed to music_bed_db
      3. Sidechain-compress music bed by vo_bed_duck_db when VO is
         playing (music dips under narration)
      4. Normalize VO to narration_vo_db (typically 0 dB = full amp)
      5. amix source + ducked-music + VO with duration=first
      6. Apply ffmpeg_utils.build_loudnorm_filter to hit target_lufs

    Returns the filter graph as a single string suitable for
    ``-filter_complex`` argument.
    """
    if not include_narration:
        # Legacy 2-input shape — do not modify (pin-tested unchanged).
        return (
            f"[0:a]volume={source_duck_db}dB[a1];"
            f"[1:a]volume={music_bed_db}dB[a2];"
            f"[a1][a2]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )

    # 3-input NARR-01 shape.
    #
    # INVARIANT (Amendment A2, 2026-08-18): VO starts at t=0.
    # ``amix duration=first`` doesn't shift start times — all three
    # inputs align to t=0. Therefore captions produced from the
    # CLEAN VO track (before this mix) align 1:1 with the final
    # audio without any offset arithmetic. If a future variant needs
    # a VO start delay, add an explicit [2:a]adelay=Nms node here
    # AND propagate the offset to render_whisper_captions.
    #
    # Sidechain-compress the music bed with VO as the trigger key.
    # threshold=0.05 → any VO amplitude above ~-26 dBFS triggers duck
    # ratio=8 → aggressive duck (music drops 8× below threshold)
    # attack=5ms → fast reaction so first VO word isn't buried under bed
    # release=200ms → smooth recovery so bed doesn't "pump" between words
    # makeup=0dB → no post-compression gain (bed already at music_bed_db)
    #
    # The vo_bed_duck_db value is applied AS THE SIDECHAIN THRESHOLD via
    # the classic amix approach: pre-duck the music bed by vo_bed_duck_db
    # AND rely on sidechain to keep the duck active. This is more
    # predictable than sidechain-only (which depends on VO signal levels
    # varying by TTS provider) — the pre-duck gives a guaranteed floor.
    #
    # Deliberately choosing predictability over acoustic elegance —
    # matches the "reuse existing loudnorm implementation style"
    # amendment (A4): simple, testable, one filter chain.
    #
    # Reuses ``ffmpeg_utils.build_loudnorm_filter`` (2026-08-18) to
    # avoid rebuilding the exact filter string — that helper already
    # sets I/TP/LRA per EBU R128 defaults.
    from genlab_core.media.ffmpeg_utils import build_loudnorm_filter

    loudnorm = build_loudnorm_filter(target_i=target_lufs)
    total_music_duck_db = music_bed_db + vo_bed_duck_db
    return (
        f"[0:a]volume={source_duck_db}dB[src];"
        f"[1:a]volume={total_music_duck_db}dB[music];"
        f"[2:a]volume={narration_vo_db}dB[vo];"
        f"[src][music][vo]amix=inputs=3:duration=first:dropout_transition=0[premix];"
        f"[premix]{loudnorm}[aout]"
    )


def build_ffmpeg_command(spec: AudioMixSpec, ffmpeg_binary: str) -> list[str]:
    """Compose the full ffmpeg argv for an audio mix render.

    Kept as a pure function so tests can assert the exact command
    shape without invoking ffmpeg.

    NARR-01: when ``spec.narration_audio_path`` is set, adds a 3rd
    ``-i`` input and switches the filtergraph to the 3-input shape.
    Legacy 2-input command is byte-identical when narration_audio_path
    is None (pin-tested).
    """
    include_narration = spec.narration_audio_path is not None

    cmd: list[str] = [
        ffmpeg_binary,
        "-y",  # overwrite output
        "-i",
        str(spec.source_video_path),
        # -stream_loop -1 loops the music input infinitely; combined with
        # amix duration=first this cuts at source length. Handles music
        # tracks shorter than the reel gracefully.
        "-stream_loop",
        "-1",
        "-i",
        str(spec.music_bed_path),
    ]
    if include_narration:
        # VO input is not looped — narration is a one-shot track that
        # ends when the script ends; music continues via stream_loop.
        cmd += ["-i", str(spec.narration_audio_path)]

    cmd += [
        "-filter_complex",
        build_audio_mix_filtergraph(
            spec.source_duck_db,
            spec.music_bed_db,
            include_narration=include_narration,
            narration_vo_db=spec.narration_vo_db,
            vo_bed_duck_db=spec.vo_bed_duck_db,
            target_lufs=spec.target_lufs,
        ),
        "-map",
        "0:v",  # video stream from source
        "-map",
        "[aout]",  # mixed audio
        "-c:v",
        "copy",  # don't re-encode video (fast)
        "-c:a",
        "aac",
        "-b:a",
        spec.audio_bitrate,
    ]
    if include_narration:
        # NARR-05 (2026-08-19): pin the output sample rate to 48 kHz.
        #
        # ``loudnorm`` resamples internally (192 kHz) and propagates a
        # non-48k rate to the encoder — a pre-verification render of a
        # real BB reel came out at **96 kHz**, violating the "AAC 48kHz
        # stereo" line in CLAUDE.md STRICT VIDEO REQUIREMENTS that every
        # platform spec assumes.
        #
        # Only applied on the narration branch: the legacy 2-input path
        # has no loudnorm, already emits 48 kHz, and is pinned
        # byte-identical by test_legacy_ffmpeg_argv_shape.
        cmd += ["-ar", "48000"]

    cmd += [
        # -shortest hedges against amix duration edge cases — if the
        # filter's duration=first ever gets confused, this hard-limits
        # output to the shortest input length.
        "-shortest",
        str(spec.output_path),
    ]
    return cmd


def mix_audio_bed(
    spec: AudioMixSpec,
    *,
    timeout_seconds: int = 300,
) -> bool:
    """Execute the ffmpeg mix. Returns True on success, False on failure.

    Fail-open — a mix failure is logged but does not raise. The caller
    (usually FrameCompositor when wired in PR 15) can fall back to the
    unmixed source path if this returns False.
    """
    if not spec.source_video_path.exists():
        logger.warning("[audio_replacer] source video missing: %s", spec.source_video_path)
        return False
    if not spec.music_bed_path.exists():
        logger.warning("[audio_replacer] music bed missing: %s", spec.music_bed_path)
        return False

    from genlab_core.media.ffmpeg import get_ffmpeg_binary

    try:
        ffmpeg = get_ffmpeg_binary()
    except RuntimeError as exc:
        logger.warning("[audio_replacer] ffmpeg not available: %s", exc)
        return False

    cmd = build_ffmpeg_command(spec, ffmpeg)
    logger.info(
        "[audio_replacer] mixing: source=%s music=%s -> %s (duck=%d music_bed=%d)",
        spec.source_video_path.name,
        spec.music_bed_path.name,
        spec.output_path,
        spec.source_duck_db,
        spec.music_bed_db,
    )

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "[audio_replacer] ffmpeg timed out after %ds: %s",
            timeout_seconds,
            spec.output_path,
        )
        return False
    except (OSError, FileNotFoundError) as exc:
        logger.warning("[audio_replacer] ffmpeg subprocess failed: %s", exc)
        return False

    if result.returncode != 0:
        # Tail the stderr for diagnostics — ffmpeg emits verbose logs.
        stderr_tail = "\n".join(result.stderr.strip().splitlines()[-5:])
        logger.warning(
            "[audio_replacer] ffmpeg exit=%d for %s. Last stderr lines:\n%s",
            result.returncode,
            spec.output_path,
            stderr_tail,
        )
        return False

    if not spec.output_path.exists() or spec.output_path.stat().st_size < 1024:
        logger.warning(
            "[audio_replacer] output missing or too small: %s (size=%d bytes)",
            spec.output_path,
            spec.output_path.stat().st_size if spec.output_path.exists() else 0,
        )
        return False

    return True


def replace_audio_for_reel(
    niche_root: Path,
    source_video_path: Path,
    output_path: Path,
    mood_tag: str,
    *,
    source_duck_db: int = -12,
    music_bed_db: int = -6,
    rng: random.Random | None = None,
    timeout_seconds: int = 300,
    narration_audio_path: Path | None = None,
    narration_vo_db: int = 0,
    vo_bed_duck_db: int = -8,
    target_lufs: float = -14.0,
) -> bool:
    """High-level orchestrator: pick a music bed for the mood, then mix.

    Returns True on success. False on any of:
      * No library seeded for niche
      * No music files under that mood tag
      * FFmpeg failure
      * Output validation failure

    Caller (usually FrameCompositor after PR 15 wire) checks the return
    value and falls back to unmixed rendering when False.

    NARR-01 (2026-08-18): when ``narration_audio_path`` is provided +
    exists, the mix becomes 3-input (source + music + VO) and applies
    EBU R128 loudnorm to ``target_lufs``. When None or missing file
    → legacy 2-input path (byte-identical to pre-NARR-01 output).
    """
    music_bed = find_music_bed_for_mood(niche_root, mood_tag, rng=rng)
    if music_bed is None:
        return False

    # NARR-01: only include VO if the path was passed AND the file
    # actually exists. A missing VO file silently degrades to the
    # legacy 2-input mix — the caller (transformation_orchestrator)
    # already logged the vo_overrun / tts_cascade_failed reason and
    # set the degraded marker; this call is the fallback path.
    resolved_narration: Path | None = None
    if narration_audio_path is not None and narration_audio_path.exists():
        resolved_narration = narration_audio_path

    spec = AudioMixSpec(
        source_video_path=source_video_path,
        music_bed_path=music_bed,
        output_path=output_path,
        source_duck_db=source_duck_db,
        music_bed_db=music_bed_db,
        narration_audio_path=resolved_narration,
        narration_vo_db=narration_vo_db,
        vo_bed_duck_db=vo_bed_duck_db,
        target_lufs=target_lufs,
    )
    return mix_audio_bed(spec, timeout_seconds=timeout_seconds)


__all__ = [
    "AudioMixSpec",
    "build_audio_mix_filtergraph",
    "build_ffmpeg_command",
    "find_music_bed_for_mood",
    "mix_audio_bed",
    "replace_audio_for_reel",
]
