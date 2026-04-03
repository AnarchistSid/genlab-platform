"""CapCut-style word-by-word ASS subtitle generator.

Generates Advanced SubStation Alpha (ASS) subtitle files with karaoke-style
word highlighting. At any given moment, the currently-spoken word appears
in a highlight colour (default yellow) while all other visible words
appear in a normal colour (default white).

Key design decisions:
  - Uses ``\\c`` (primary colour override) per word, NOT karaoke tags
    (``\\k``, ``\\kf``, ``\\ko``). Karaoke tags create cumulative fill
    animations; we want discrete colour switching.
  - ASS uses BGR colour order: ``&HAABBGGRR``.
    Yellow = ``&H0000FFFF``, White = ``&H00FFFFFF``.
  - Words are grouped into chunks of ``max_words_per_group`` (default 5)
    for readability on small mobile screens.
  - All words are UPPERCASED for the viral short-form aesthetic.
  - ``MarginV=350`` keeps text above platform UI safe zones
    (TikTok/Reels/Shorts overlay ~18% of the bottom).

Transplanted from Gaming Clips with import updates.
"""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Timestamp conversion
# ---------------------------------------------------------------------------

def seconds_to_ass(seconds: float) -> str:
    """Convert seconds to ASS timestamp format: ``H:MM:SS.cc`` (centiseconds)."""
    if seconds < 0:
        seconds = 0.0
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centiseconds = int(round((seconds % 1) * 100))
    if centiseconds > 99:
        centiseconds = 99
    return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"


# ---------------------------------------------------------------------------
# ASS header generation
# ---------------------------------------------------------------------------

def generate_ass_header(
    *,
    font_name: str = "Montserrat ExtraBold",
    font_size: int = 72,
    primary_color: str = "&H00FFFFFF",
    outline_color: str = "&H00000000",
    back_color: str = "&H80000000",
    outline_width: int = 4,
    shadow_depth: int = 2,
    alignment: int = 2,
    margin_bottom: int = 350,
    play_res_x: int = 1080,
    play_res_y: int = 1920,
) -> str:
    """Generate the ASS file header: [Script Info], [V4+ Styles], [Events] format line."""
    lines = [
        "[Script Info]",
        "Title: Gaming Compilation Captions",
        "ScriptType: v4.00+",
        f"PlayResX: {play_res_x}",
        f"PlayResY: {play_res_y}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        (
            "Format: Name, Fontname, Fontsize, PrimaryColour, "
            "SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
            "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
            "MarginV, Encoding"
        ),
        (
            f"Style: Default,{font_name},{font_size},{primary_color},"
            f"&H000000FF,{outline_color},{back_color},"
            f"1,0,0,0,100,100,0,0,"
            f"1,{outline_width},{shadow_depth},{alignment},"
            f"40,40,{margin_bottom},1"
        ),
        "",
        "[Events]",
        (
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
            "MarginV, Effect, Text"
        ),
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Word grouping
# ---------------------------------------------------------------------------

def _group_words(
    words: list[dict],
    max_words_per_group: int = 5,
    min_confidence: float = 0.0,
) -> list[list[dict]]:
    """Split a flat list of words into display groups."""
    filtered = [
        w for w in words
        if w.get("word", "").strip()
        and w.get("probability", 1.0) >= min_confidence
    ]

    if not filtered:
        return []

    groups: list[list[dict]] = []
    for i in range(0, len(filtered), max_words_per_group):
        groups.append(filtered[i : i + max_words_per_group])
    return groups


# ---------------------------------------------------------------------------
# Dialogue line generation
# ---------------------------------------------------------------------------

def generate_dialogue_lines(
    transcription: dict,
    *,
    highlight_color: str = "&H0000FFFF",
    normal_color: str = "&H00FFFFFF",
    max_words_per_group: int = 5,
    uppercase: bool = True,
    min_confidence: float = 0.5,
) -> list[str]:
    """Generate ASS Dialogue lines with per-word colour highlighting."""
    if not transcription or not transcription.get("segments"):
        return []

    dialogue_lines: list[str] = []

    for seg in transcription["segments"]:
        seg_words = seg.get("words")
        if not seg_words:
            continue

        groups = _group_words(
            seg_words,
            max_words_per_group=max_words_per_group,
            min_confidence=min_confidence,
        )

        for group in groups:
            display_texts = []
            for w in group:
                text = w.get("word", "").strip()
                if uppercase:
                    text = text.upper()
                display_texts.append(text)

            for active_idx, active_word in enumerate(group):
                if not display_texts[active_idx]:
                    continue

                start = seconds_to_ass(active_word["start"])
                end = seconds_to_ass(active_word["end"])

                parts: list[str] = []
                for j, text in enumerate(display_texts):
                    if not text:
                        continue
                    if j == active_idx:
                        parts.append(f"{{\\c{highlight_color}}}{text}")
                    else:
                        parts.append(f"{{\\c{normal_color}}}{text}")

                line_text = " ".join(parts)
                dialogue_lines.append(
                    f"Dialogue: 0,{start},{end},Default,,0,0,0,,{line_text}"
                )

    return dialogue_lines


# ---------------------------------------------------------------------------
# Full ASS file generation
# ---------------------------------------------------------------------------

def generate_ass_file(
    transcription: dict | None,
    output_path: str,
    style_config: dict | None = None,
) -> bool:
    """Generate a complete ASS subtitle file with word-by-word highlighting.

    Returns True on success, False if no usable segments or on error.
    """
    if not transcription or not transcription.get("segments"):
        logger.warning("No segments in transcription — cannot generate ASS")
        return False

    if style_config is None:
        style_config = {}

    font_name = style_config.get("font_name", "Montserrat ExtraBold")
    font_size = int(style_config.get("font_size", 72))
    highlight_color = style_config.get("highlight_color", "&H0000FFFF")
    normal_color = style_config.get("normal_color", "&H00FFFFFF")
    outline_color = style_config.get("outline_color", "&H00000000")
    back_color = style_config.get("back_color", "&H80000000")
    outline_width = int(style_config.get("outline_width", 4))
    shadow_depth = int(style_config.get("shadow_depth", 2))
    max_words_per_group = int(style_config.get("max_words_per_group", 5))
    margin_bottom = int(style_config.get("margin_bottom", 350))
    uppercase = bool(style_config.get("uppercase", True))
    min_confidence = float(style_config.get("min_word_confidence", 0.5))
    alignment = int(style_config.get("alignment", 2))
    play_res_x = int(style_config.get("play_res_x", 1080))
    play_res_y = int(style_config.get("play_res_y", 1920))

    try:
        header = generate_ass_header(
            font_name=font_name,
            font_size=font_size,
            primary_color=normal_color,
            outline_color=outline_color,
            back_color=back_color,
            outline_width=outline_width,
            shadow_depth=shadow_depth,
            alignment=alignment,
            margin_bottom=margin_bottom,
            play_res_x=play_res_x,
            play_res_y=play_res_y,
        )

        dialogue_lines = generate_dialogue_lines(
            transcription,
            highlight_color=highlight_color,
            normal_color=normal_color,
            max_words_per_group=max_words_per_group,
            uppercase=uppercase,
            min_confidence=min_confidence,
        )

        if not dialogue_lines:
            logger.warning("No dialogue lines generated — skipping ASS write")
            return False

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(header)
            f.write("\n")
            for line in dialogue_lines:
                f.write(line)
                f.write("\n")

        logger.info(
            "Generated ASS subtitle: %s (%d dialogue lines)",
            output_path,
            len(dialogue_lines),
        )
        return True

    except Exception as e:
        logger.warning("ASS file generation failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# FFmpeg burn with fontsdir support
# ---------------------------------------------------------------------------

def burn_ass_captions(
    video_path: str,
    ass_path: str,
    output_path: str,
    fonts_dir: str | None = None,
) -> bool:
    """Burn ASS subtitles into video via FFmpeg ``ass`` filter.

    Returns True on success, False on failure.
    """
    escaped_ass = ass_path.replace("\\", "\\\\").replace("'", "'\\''")
    escaped_ass = escaped_ass.replace(":", "\\:")

    if fonts_dir:
        escaped_fonts = fonts_dir.replace("\\", "\\\\").replace("'", "'\\''")
        escaped_fonts = escaped_fonts.replace(":", "\\:")
        vf = f"ass='{escaped_ass}':fontsdir='{escaped_fonts}'"
    else:
        vf = f"ass='{escaped_ass}'"

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", vf,
        "-c:a", "copy",
        output_path,
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=300,
        )
        if proc.returncode != 0:
            logger.warning(
                "FFmpeg ASS burn failed (rc=%d): %s",
                proc.returncode,
                proc.stderr.decode(errors="replace")[:500],
            )
            return False

        logger.info("Burned ASS captions into %s", output_path)
        return True

    except subprocess.TimeoutExpired:
        logger.warning("FFmpeg ASS burn timed out (300s)")
        return False
    except FileNotFoundError:
        logger.warning("FFmpeg not found — cannot burn ASS captions")
        return False
    except Exception as e:
        logger.warning("FFmpeg ASS burn error: %s", e)
        return False
