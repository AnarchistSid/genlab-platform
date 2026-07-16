#!/usr/bin/env python3
"""Generate placeholder motion intros + outros for the intelligent
transformation ``motion_graphics`` dimension (task #524, 2026-07-05).

Purpose
-------
Unblocks the bandit's ``intro_animation`` and ``outro_cta`` arms with
brand-consistent placeholder assets so all 11 transformation
dimensions can fire. Operator later swaps in Canva-designed MP4s by
dropping same-named files into the same dirs.

Design decisions
----------------
* **Differentiated by visual style so the bandit can distinguish
  arms.** Intros differ by background color per template name; outros
  differ by CTA text overlay. Same-named templates would collapse the
  arm space.
* **Static (no motion animation).** Real Canva versions will animate;
  placeholders are static + branded so the bandit signal is honest
  (bandit measures "which STYLE / TEXT resonates", not "which
  animation style"). When Canva versions land, the arm identities
  survive but the underlying assets improve.
* **Silent AAC audio track.** ``motion_compositor.py:23`` requires
  each concat input to have an audio stream — without it FFmpeg
  ``concat`` filter aborts. ``anullsrc`` gives us zero-bytes audio at
  the right sample rate + channels.
* **Spec compliance** (matches ``motion_compositor.build_ffmpeg_command``
  + CLAUDE.md render rules):
    - 1080x1920, 9:16 portrait, 30 fps
    - H.264 libx264, yuv420p, bt709 colorspace/primaries/trc
    - AAC 48 kHz stereo, 128 kbps
    - ~2.5 s duration

Usage
-----
::

    # Default — writes into <repo>/assets/motion_placeholders/<niche>/
    uv run python3 scripts/generate_placeholder_motion.py

    # Write directly into prod paths (needs the channel dirs to exist)
    uv run python3 scripts/generate_placeholder_motion.py \
        --output-mode prod --prod-root /opt/genlab

    # Single niche override
    uv run python3 scripts/generate_placeholder_motion.py --niche gaming

Exit codes
----------
* ``0`` — every intro+outro produced
* ``1`` — at least one FFmpeg invocation failed
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# niche_id: (channel_dir_on_prod, logo_relpath_from_repo, accent_hex, display_name)
NICHES: dict[str, tuple[str, str, str, str]] = {
    "ai_creators": (
        "BlackboxBrief",
        "BlackboxBrief/assets/logos/blackbox_brief.png",
        "0x00D4FF",
        "Blackbox Brief",
    ),
    "gaming": (
        "CriticalRush/niches/gaming",
        "CriticalRush/niches/gaming/assets/criticalrush_logo.png",
        "0xF97316",
        "CriticalRush",
    ),
    "sports": (
        "ClutchWire",
        "ClutchWire/assets/logos/ClutchWire-Logo.png",
        "0xFF2040",
        "ClutchWire",
    ),
    "movies": (
        "SpliceReel",
        "SpliceReel/assets/logos/SpliceReel-Logo.png",
        "0xC9A84C",
        "SpliceReel",
    ),
}

# Intros — three templates, each with a distinct background so the
# bandit can pick up a real signal from "which style resonates".
# (background_kind, logo_scale_fraction)
INTRO_TEMPLATES: dict[str, tuple[str, float]] = {
    "logo_zoom": ("accent", 0.60),
    "logo_tagline_reveal": ("dark", 0.55),
    "pattern_break_intro": ("white", 0.55),
}

# Outros — CTA text differentiates each style. All share the same
# dark background + accent-colored second line for readability.
OUTRO_TEMPLATES: dict[str, str] = {
    "follow": "FOLLOW\nFOR MORE",
    "comment": "COMMENT\nYOUR TAKE",
    "save": "SAVE\nTHIS",
    "share": "SEND\nTO A FRIEND",
    "question": "AGREE OR\nDISAGREE?",
}

DURATION_S: float = 2.5
DARK_HEX: str = "0x0A0A1E"
WHITE_HEX: str = "0xFFFFFF"


def resolve_bg_color(kind: str, accent: str) -> str:
    """Resolve a template's ``bg_kind`` to an FFmpeg hex color."""
    if kind == "accent":
        return accent
    if kind == "dark":
        return DARK_HEX
    if kind == "white":
        return WHITE_HEX
    raise ValueError(f"Unknown bg_kind {kind!r}")


def _base_ffmpeg_args(bg_color: str) -> list[str]:
    """Common FFmpeg input args: solid color source + logo + silent audio."""
    return [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={bg_color}:s=1080x1920:d={DURATION_S}:r=30",
        # Slot [1:*] left for logo; caller appends "-i <logo>"
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=r=48000:cl=stereo:d={DURATION_S}",
    ]


def _encode_flags() -> list[str]:
    return [
        "-c:v",
        "libx264",
        "-crf",
        "20",
        "-preset",
        "fast",
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-b:a",
        "128k",
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-shortest",
        "-t",
        str(DURATION_S),
    ]


def build_intro_cmd(
    output: Path,
    logo: Path,
    bg_color: str,
    logo_scale: float,
    channel_name: str,
    template: str,
) -> list[str]:
    """Compose FFmpeg command for one intro asset. Pure — returns argv."""
    filters = [
        f"[1:v]scale={int(1080 * logo_scale)}:-1[lg]",
    ]
    if template == "logo_tagline_reveal":
        filters.append("[0:v][lg]overlay=(W-w)/2:(H-h)/2-120[withlogo]")
        filters.append(
            f"[withlogo]drawtext=text='{channel_name}':"
            "fontcolor=white:fontsize=54:"
            "x=(w-text_w)/2:y=(h/2)+220[out]"
        )
    else:
        filters.append("[0:v][lg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[out]")
    filter_complex = ";".join(filters)

    return [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={bg_color}:s=1080x1920:d={DURATION_S}:r=30",
        "-i",
        str(logo),
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=r=48000:cl=stereo:d={DURATION_S}",
        "-filter_complex",
        filter_complex,
        "-map",
        "[out]",
        "-map",
        "2:a",
        *_encode_flags(),
        str(output),
    ]


def build_outro_cmd(
    output: Path,
    logo: Path,
    accent_color: str,
    cta_text: str,
) -> list[str]:
    """Compose FFmpeg command for one outro asset. Pure — returns argv.

    Outros are all dark background with logo top-center + two-line
    CTA text (first line white, second line accent-colored).
    """
    escaped = cta_text.replace(":", r"\:").replace("'", r"\'")
    lines = escaped.split("\n")

    filters = [
        "[1:v]scale=460:-1[lg]",
        "[0:v][lg]overlay=(W-w)/2:200[withlogo]",
    ]
    accent_hash = accent_color.replace("0x", "#")
    if len(lines) == 1:
        filters.append(
            f"[withlogo]drawtext=text='{lines[0]}':"
            "fontcolor=white:fontsize=110:"
            "x=(w-text_w)/2:y=(h-text_h)/2[out]"
        )
    else:
        filters.append(
            f"[withlogo]drawtext=text='{lines[0]}':"
            "fontcolor=white:fontsize=110:"
            "x=(w-text_w)/2:y=(h-text_h)/2-80[l1]"
        )
        filters.append(
            f"[l1]drawtext=text='{lines[1]}':"
            f"fontcolor={accent_hash}:fontsize=110:"
            "x=(w-text_w)/2:y=(h-text_h)/2+80[out]"
        )
    filter_complex = ";".join(filters)

    return [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={DARK_HEX}:s=1080x1920:d={DURATION_S}:r=30",
        "-i",
        str(logo),
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=r=48000:cl=stereo:d={DURATION_S}",
        "-filter_complex",
        filter_complex,
        "-map",
        "[out]",
        "-map",
        "2:a",
        *_encode_flags(),
        str(output),
    ]


def _out_paths(
    output_mode: str,
    output_root: Path,
    prod_root: Path,
    niche: str,
    chan_dir: str,
) -> tuple[Path, Path]:
    """Resolve where intros/ and outros/ go given the output mode."""
    if output_mode == "repo":
        return (
            output_root / "assets" / "motion_placeholders" / niche / "intros",
            output_root / "assets" / "motion_placeholders" / niche / "outros",
        )
    return (
        prod_root / chan_dir / "assets" / "motion" / "intros",
        prod_root / chan_dir / "assets" / "motion" / "outros",
    )


def generate(
    repo_root: Path,
    output_mode: str,
    output_root: Path,
    prod_root: Path,
    niche_filter: str | None = None,
) -> tuple[int, int]:
    """Generate the full asset set. Returns (produced, failed) counts."""
    produced = failed = 0
    for niche, (chan_dir, logo_rel, accent, display) in NICHES.items():
        if niche_filter and niche != niche_filter:
            continue
        logo = repo_root / logo_rel
        if not logo.is_file():
            print(f"  ✗ logo missing for {niche}: {logo}")
            failed += len(INTRO_TEMPLATES) + len(OUTRO_TEMPLATES)
            continue

        intros_dir, outros_dir = _out_paths(output_mode, output_root, prod_root, niche, chan_dir)
        intros_dir.mkdir(parents=True, exist_ok=True)
        outros_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n== {niche} ({display}, accent={accent}) ==")
        for name, (bg_kind, scale) in INTRO_TEMPLATES.items():
            out = intros_dir / f"{name}.mp4"
            cmd = build_intro_cmd(
                out,
                logo,
                resolve_bg_color(bg_kind, accent),
                scale,
                display,
                name,
            )
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode == 0 and out.is_file():
                print(f"  ✓ intros/{name}.mp4  ({out.stat().st_size:,} bytes)")
                produced += 1
            else:
                tail = (r.stderr or "").strip().splitlines()
                print(f"  ✗ intros/{name}.mp4: {tail[-1] if tail else 'unknown'}")
                failed += 1

        for name, cta in OUTRO_TEMPLATES.items():
            out = outros_dir / f"{name}.mp4"
            cmd = build_outro_cmd(out, logo, accent, cta)
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode == 0 and out.is_file():
                print(f"  ✓ outros/{name}.mp4  ({out.stat().st_size:,} bytes)")
                produced += 1
            else:
                tail = (r.stderr or "").strip().splitlines()
                print(f"  ✗ outros/{name}.mp4: {tail[-1] if tail else 'unknown'}")
                failed += 1

    return produced, failed


def main() -> int:
    repo_root_default = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--repo-root",
        type=Path,
        default=repo_root_default,
        help=f"Repo root (default: {repo_root_default})",
    )
    ap.add_argument(
        "--output-mode",
        choices=("repo", "prod"),
        default="repo",
        help="Where to write assets. 'repo' → assets/motion_placeholders/. "
        "'prod' → <prod-root>/<channel_dir>/assets/motion/.",
    )
    ap.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Root for --output-mode=repo (default: <repo-root>)",
    )
    ap.add_argument(
        "--prod-root",
        type=Path,
        default=Path("/opt/genlab"),
        help="Root for --output-mode=prod (default: /opt/genlab)",
    )
    ap.add_argument(
        "--niche",
        choices=list(NICHES) + [None],
        default=None,
        help="Restrict to a single niche (default: all 4)",
    )
    args = ap.parse_args()

    output_root = args.output_root or args.repo_root
    produced, failed = generate(
        args.repo_root,
        args.output_mode,
        output_root,
        args.prod_root,
        args.niche,
    )
    print(f"\n==== SUMMARY: {produced} produced, {failed} failed ====")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
