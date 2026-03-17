"""Word-by-word reveal animation engine for FFmpeg drawtext filters.

Canonical location: genlab_core.rendering.word_animator
(Moved from BlackboxBrief/execution/utils/word_by_word_animator.py)

Classes:
    WordTiming          — Timing + position dataclass for a single animated word
    WordByWordAnimator  — Full pipeline: text -> timed layout -> FFmpeg filters

Functions:
    get_wbw_config()    — Load word-by-word animation config with defaults
"""

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Text optimizer import ─────────────────────────────────────
# Optional: try BlackboxBrief's text_optimizer first (runtime sys.path injection),
# then fall back to hardcoded safe-zone constants.
try:
    from execution.utils.text_optimizer import (
        SAFE_LEFT,
        SAFE_RIGHT,
        SAFE_TOP,
        calculate_optimal_font_size,
        calculate_safe_position,
    )
    _HAS_TEXT_OPTIMIZER = True
except ImportError:
    _HAS_TEXT_OPTIMIZER = False
    # Fallback safe zone constants (match instagram_specs.yaml)
    SAFE_TOP = 250
    SAFE_LEFT = 60
    SAFE_RIGHT = 120

# Fallback font size ranges (same as render_text_overlays constants)
HOOK_SIZE_RANGE = (120, 160)
BODY_SIZE_RANGE = (56, 72)


def get_wbw_config() -> dict:
    """Get the animation.word_by_word config block with defaults.

    Returns a flat dict with all word-by-word animation parameters.
    Missing keys are filled with sensible defaults so callers never
    need to handle missing values.

    Looks for config/instagram_specs.yaml relative to the BlackboxBrief
    root (if available at runtime). Falls back to hardcoded defaults.
    """
    # Try to find instagram_specs.yaml in known locations
    candidates = [
        # BlackboxBrief root (when sys.path includes it)
        Path(__file__).resolve().parent.parent.parent.parent.parent
        / "BlackboxBrief" / "config" / "instagram_specs.yaml",
    ]
    wbw: dict = {}
    for specs_path in candidates:
        if specs_path.exists():
            import yaml
            with open(specs_path) as f:
                specs = yaml.safe_load(f) or {}
            wbw = specs.get("animation", {}).get("word_by_word", {})
            break

    return {
        "wpm": wbw.get("wpm", 150),
        "start_delay": wbw.get("start_delay", 0.30),
        "body_gap": wbw.get("body_gap", 0.20),
        "highlight_color": wbw.get("highlight_color", "#FFD700").lstrip("#"),
        "base_color": wbw.get("base_color", "#FFFFFF").lstrip("#"),
        "shadow_color": wbw.get("shadow_color", "#000000").lstrip("#"),
        "shadow_alpha": wbw.get("shadow_alpha", 0.9),
        "transition_duration": wbw.get("transition_duration", 0.15),
        "max_words_per_line": wbw.get("max_words_per_line", 6),
        "char_width_factor": wbw.get("char_width_factor", 0.55),
        "space_width_factor": wbw.get("space_width_factor", 0.30),
        "line_height_factor": wbw.get("line_height_factor", 1.45),
    }


# ══════════════════════════════════════════════════════════════
# Word-by-Word Animation Engine
# ══════════════════════════════════════════════════════════════

@dataclass
class WordTiming:
    """Timing + position data for a single animated word."""
    word: str
    index: int              # 0-based word index
    line: int               # 0-based line index
    col: int                # 0-based word-in-line index
    # Pixel positions (pre-calculated for centered line layout)
    x: int = 0              # left edge of this word (pixels)
    y: int = 0              # top edge of the line (pixels)
    word_width_px: int = 0  # estimated pixel width of this word
    # Timing (seconds from video start)
    appear_time: float = 0.0    # word becomes visible
    highlight_end: float = 0.0  # gold->white transition starts
    fade_end: float = 0.0       # word is fully white


class WordByWordAnimator:
    """Generates FFmpeg drawtext filters for word-by-word reveal animation.

    Competitive format (MrBeast/Hormozi/@evolving.ai style):
      - Words appear one at a time, timed to voiceover cadence
      - Active word is highlighted in gold (#FFD700)
      - Previous words fade from gold to white over 0.15s
      - 4-6 words per line, centered within safe zone
      - Hard shadows on every word for readability on video

    Architecture:
      1. calculate_word_timings()  -- splits text into words with timestamps
      2. layout_words()            -- arranges words into centered lines with
                                     per-word x,y pixel positions
      3. generate_ffmpeg_filters() -- emits one drawtext filter PER WORD with
                                     time-gated enable + color expressions

    Each word gets a single drawtext filter that handles the full lifecycle:
      - Invisible before appear_time
      - Gold (#FFD700) from appear_time to highlight_end
      - Smooth color interpolation from gold -> white over fade_duration
      - White (#FFFFFF) from fade_end onward

    FFmpeg color interpolation uses fontcolor_expr with conditional arithmetic
    on the R,G,B channels independently. This avoids the need for multiple
    overlapping filters per word.

    Usage:
        animator = WordByWordAnimator(font_path="/path/to/Inter.ttf")
        timings = animator.calculate_word_timings("AI just changed everything", wpm=150)
        timings = animator.layout_words(timings, font_size=140, x=60, y=300, max_width=900)
        filters = animator.generate_ffmpeg_filters(timings, font_size=140)
        # -> comma-separated drawtext filters for FFmpeg -vf
    """

    # -- Class-level defaults (overridden by config in __init__) --
    # Kept as fallbacks for when config loading fails or in tests.
    _DEFAULT_CONFIG = {
        "wpm": 150,
        "start_delay": 0.30,
        "body_gap": 0.20,
        "highlight_color": "FFD700",
        "base_color": "FFFFFF",
        "shadow_color": "000000",
        "shadow_alpha": 0.9,
        "transition_duration": 0.15,
        "max_words_per_line": 6,
        "char_width_factor": 0.55,
        "space_width_factor": 0.30,
        "line_height_factor": 1.45,
    }

    def __init__(
        self,
        font_path: str | None = None,
        config: dict | None = None,
    ):
        """Initialize with optional font path and config overrides.

        Args:
            font_path:  Path to .ttf/.ttc font file for FFmpeg drawtext.
            config:     Optional dict of animation parameters (from
                        get_wbw_config() or A/B test overrides). If None,
                        loads from config/instagram_specs.yaml automatically.

        Config keys (all optional, defaults filled automatically):
            wpm, start_delay, body_gap, highlight_color, base_color,
            shadow_color, shadow_alpha, transition_duration,
            max_words_per_line, char_width_factor, space_width_factor,
            line_height_factor
        """
        self.font_path = font_path

        # Merge: explicit config > YAML file > class defaults
        if config is not None:
            cfg = {**self._DEFAULT_CONFIG, **config}
        else:
            cfg = get_wbw_config()

        # -- Populate instance attributes from config ---------------
        self.HIGHLIGHT_COLOR: str = cfg["highlight_color"]
        self.BASE_COLOR: str = cfg["base_color"]
        self.SHADOW_COLOR: str = cfg["shadow_color"]
        self.SHADOW_ALPHA: float = cfg["shadow_alpha"]

        self.DEFAULT_WPM: int = cfg["wpm"]
        self.DEFAULT_FADE_DURATION: float = cfg["transition_duration"]
        self.START_DELAY: float = cfg["start_delay"]
        self.BODY_GAP: float = cfg["body_gap"]

        self.MAX_WORDS_PER_LINE: int = cfg["max_words_per_line"]
        self.CHAR_WIDTH_FACTOR: float = cfg["char_width_factor"]
        self.SPACE_WIDTH_FACTOR: float = cfg["space_width_factor"]
        self.LINE_HEIGHT_FACTOR: float = cfg["line_height_factor"]

    # ==============================================================
    # 1. TIMING
    # ==============================================================

    def calculate_word_timings(
        self,
        text: str,
        wpm: int | None = None,
        start_time: float | None = None,
        fade_duration: float | None = None,
    ) -> list[WordTiming]:
        """Split text into words with appearance timestamps.

        Each word appears at a fixed interval derived from the target WPM.
        The highlight window lasts until the NEXT word appears, then a
        short fade transitions the color from gold to white.

        All defaults come from the config loaded at __init__ time.

        Args:
            text:           The full text to animate.
            wpm:            Words per minute (default: from config).
            start_time:     Delay before first word appears (default: from config).
            fade_duration:  Duration of gold->white color fade (default: from config).

        Returns:
            List of WordTiming with index, line, col, and timing fields set.
            Pixel positions (x, y) are NOT set -- call layout_words() next.
        """
        # Resolve defaults from config
        wpm = wpm if wpm is not None else self.DEFAULT_WPM
        start_time = start_time if start_time is not None else self.START_DELAY
        fade_duration = fade_duration if fade_duration is not None else self.DEFAULT_FADE_DURATION

        words = text.split()
        if not words:
            return []

        seconds_per_word = 60.0 / wpm  # e.g., 150 WPM -> 0.4s per word

        timings: list[WordTiming] = []
        line = 0
        col = 0

        for i, word in enumerate(words):
            appear = start_time + i * seconds_per_word
            # Highlight lasts until next word appears (or slightly longer for last word)
            if i < len(words) - 1:
                highlight_end = start_time + (i + 1) * seconds_per_word
            else:
                highlight_end = appear + seconds_per_word

            timings.append(WordTiming(
                word=word,
                index=i,
                line=line,
                col=col,
                appear_time=round(appear, 3),
                highlight_end=round(highlight_end, 3),
                fade_end=round(highlight_end + fade_duration, 3),
            ))

            col += 1
            if col >= self.MAX_WORDS_PER_LINE:
                line += 1
                col = 0

        return timings

    def calculate_word_timings_from_whisper(
        self,
        text: str,
        whisper_words: list[dict],
        fade_duration: float | None = None,
    ) -> list[WordTiming]:
        """Populate word timings from Whisper speech-to-text timestamps.

        Instead of evenly spacing words by WPM, this method maps each
        authored word to the corresponding Whisper segment so the
        on-screen reveal matches actual speech cadence.

        Timing logic per word *i*:
          - appear_time  = whisper_words[i]["start"]
          - highlight_end = whisper_words[i+1]["start"]  (next word's start)
                           or whisper_words[i]["end"]    (last word)
          - fade_end     = highlight_end + fade_duration

        If the authored text has more words than whisper_words, the
        surplus words are extrapolated from the last known timestamp
        using the instance's WPM default.

        Args:
            text:           The full text to animate.
            whisper_words:  List of dicts with "start" and "end" float keys
                            (seconds), one per spoken word.
            fade_duration:  Gold-to-white transition duration (default: from
                            config ``transition_duration``).

        Returns:
            List of WordTiming with timing + line/col set.
            Pixel positions (x, y) are NOT set — call layout_words() next.
        """
        fade_duration = (
            fade_duration if fade_duration is not None
            else self.DEFAULT_FADE_DURATION
        )

        words = text.split()
        if not words:
            return []

        n_words = len(words)
        n_whisper = len(whisper_words)

        timings: list[WordTiming] = []
        line = 0
        col = 0

        for i, word in enumerate(words):
            if i < n_whisper:
                appear = whisper_words[i]["start"]
                if i < n_words - 1 and i + 1 < n_whisper:
                    highlight_end = whisper_words[i + 1]["start"]
                else:
                    highlight_end = whisper_words[i]["end"]
            else:
                # Extrapolate beyond available Whisper data
                last_end = whisper_words[-1]["end"] if n_whisper else 0.0
                spw = 60.0 / self.DEFAULT_WPM
                offset = (i - n_whisper) * spw
                appear = last_end + offset
                highlight_end = appear + spw

            timings.append(WordTiming(
                word=word,
                index=i,
                line=line,
                col=col,
                appear_time=round(appear, 3),
                highlight_end=round(highlight_end, 3),
                fade_end=round(highlight_end + fade_duration, 3),
            ))

            col += 1
            if col >= self.MAX_WORDS_PER_LINE:
                line += 1
                col = 0

        return timings

    # ==============================================================
    # 2. LAYOUT -- per-word pixel positions for centered lines
    # ==============================================================

    def layout_words(
        self,
        timings: list[WordTiming],
        font_size: int,
        x: int,
        y: int,
        max_width: int,
        line_height_factor: float | None = None,
        align_left: bool = False,
    ) -> list[WordTiming]:
        """Compute per-word pixel positions for centered multi-line layout.

        Re-wraps words into lines based on actual estimated pixel width,
        overriding the word-count-based line assignments from
        calculate_word_timings(). This ensures text never overflows the
        safe zone regardless of font size or word length.

        Algorithm:
          1. Estimate each word's pixel width
          2. Greedily pack words into lines that fit within max_width
          3. Center each line horizontally within (x, x+max_width)
          4. Assign each word its absolute x, y position

        Args:
            timings:            List of WordTiming from calculate_word_timings().
            font_size:          Font size in pixels.
            x:                  Left edge of the text area (safe zone left).
            y:                  Top edge of the first line.
            max_width:          Available width for text (safe zone width).
            line_height_factor: Line height as multiple of font_size (default: 1.45).

        Returns:
            The same list with x, y, word_width_px, line, col populated.
        """
        if not timings:
            return timings

        line_height_factor = line_height_factor if line_height_factor is not None else self.LINE_HEIGHT_FACTOR
        char_w = font_size * self.CHAR_WIDTH_FACTOR
        space_w = font_size * self.SPACE_WIDTH_FACTOR
        line_h = int(font_size * line_height_factor)

        # -- Phase 1: estimate pixel widths -------------------------
        for wt in timings:
            wt.word_width_px = max(
                int(len(wt.word) * char_w),
                int(font_size * 0.5),  # minimum: half a glyph
            )

        # -- Phase 2: greedy line-wrap by pixel width ---------------
        # Override line/col assignments from calculate_word_timings()
        # so lines actually fit within max_width.
        line_idx = 0
        col_idx = 0
        line_px = 0  # running pixel width of current line

        for wt in timings:
            needed = wt.word_width_px + (int(space_w) if col_idx > 0 else 0)
            if col_idx > 0 and line_px + needed > max_width:
                # Word doesn't fit -- wrap to next line
                line_idx += 1
                col_idx = 0
                line_px = 0
                needed = wt.word_width_px  # no leading space on new line

            wt.line = line_idx
            wt.col = col_idx
            line_px += needed
            col_idx += 1

        # -- Phase 3: center each line and assign positions ---------
        lines: dict[int, list[WordTiming]] = {}
        for wt in timings:
            lines.setdefault(wt.line, []).append(wt)

        for ln, line_words in sorted(lines.items()):
            total_word_w = sum(wt.word_width_px for wt in line_words)
            total_space_w = int(space_w * (len(line_words) - 1))
            total_line_w = total_word_w + total_space_w

            # Align words within available area.
            x_offset = x if align_left else x + max(0, (max_width - total_line_w) // 2)
            line_y = y + ln * line_h

            cursor_x = x_offset
            for wt in line_words:
                wt.x = cursor_x
                wt.y = line_y
                cursor_x += wt.word_width_px + int(space_w)

        return timings

    # ==============================================================
    # 3. FFmpeg FILTER GENERATION
    # ==============================================================

    def generate_ffmpeg_filters(
        self,
        timings: list[WordTiming],
        font_size: int,
        shadow_offset: int = 6,
        shadow_alpha: float | None = None,
    ) -> str:
        """Generate FFmpeg drawtext filters for the word-by-word animation.

        Each word gets ONE drawtext filter that handles its full lifecycle:
          - Invisible before appear_time  (enable gate)
          - Color transitions gold -> white using fontcolor_expr
          - Hard shadow for readability

        The fontcolor_expr uses time-based linear interpolation:
          R: constant 0xFF  (both gold and white have R=255)
          G: lerp from 0xD7 (gold) -> 0xFF (white)
          B:   0 -> 255 (interpolate during fade window)

        For the shadow, a separate drawtext filter runs underneath with
        black color and an offset.

        Args:
            timings:        List of WordTiming with positions populated.
            font_size:      Font size in pixels.
            shadow_offset:  Shadow displacement in pixels (default: 6).
            shadow_alpha:   Shadow opacity 0.0-1.0 (default from config).

        Returns:
            Comma-separated FFmpeg drawtext filter string.
        """
        if not timings:
            return ""

        shadow_alpha = shadow_alpha if shadow_alpha is not None else self.SHADOW_ALPHA

        font_arg = f":fontfile='{self.font_path}'" if self.font_path else ""
        filters: list[str] = []

        for wt in timings:
            escaped = self._escape_word(wt.word)

            t_appear = wt.appear_time
            t_hl_end = wt.highlight_end
            t_fade_end = wt.fade_end

            # -- Shadow filter (underneath, offset, black) ----------
            filters.append(
                f"drawtext=expansion=none:"
                f"text='{escaped}'"
                f"{font_arg}"
                f":fontsize={font_size}"
                f":fontcolor=0x{self.SHADOW_COLOR}@{shadow_alpha}"
                f":x={wt.x + shadow_offset}"
                f":y={wt.y + shadow_offset}"
                f":enable='gte(t,{t_appear})'"
            )

            # -- Main word filter with animated color ---------------
            # Color interpolation: gold (#FFD700) -> white (#FFFFFF)
            #   R: 255 -> 255 (constant)
            #   G: 215 -> 255 (interpolate during fade window)
            #   B:   0 -> 255 (interpolate during fade window)
            #
            # APPROACH: Two layers per word
            # Layer 1: White word (persistent, from appear_time to end)
            # Layer 2: Gold word ON TOP (appears, then fades out via alpha)

            # Layer 1: BASE color (permanent once revealed)
            filters.append(
                f"drawtext=expansion=none:"
                f"text='{escaped}'"
                f"{font_arg}"
                f":fontsize={font_size}"
                f":fontcolor=0x{self.BASE_COLOR}"
                f":x={wt.x}"
                f":y={wt.y}"
                f":enable='gte(t,{t_appear})'"
            )

            # Layer 2: GOLD highlight (fades out after highlight ends)
            # Alpha goes from 1.0 -> 0.0 during fade window
            fade_dur = max(0.01, t_fade_end - t_hl_end)
            alpha_expr = (
                f"if(lt(t,{t_hl_end}), 1, "
                f"if(lt(t,{t_fade_end}), "
                f"1-clip((t-{t_hl_end})/{fade_dur}, 0, 1), "
                f"0))"
            )

            filters.append(
                f"drawtext=expansion=none:"
                f"text='{escaped}'"
                f"{font_arg}"
                f":fontsize={font_size}"
                f":fontcolor=0x{self.HIGHLIGHT_COLOR}"
                f":alpha='{alpha_expr}'"
                f":x={wt.x}"
                f":y={wt.y}"
                f":enable='gte(t,{t_appear})'"
            )

        return ",".join(filters)

    # ==============================================================
    # Convenience: full pipeline in one call
    # ==============================================================

    def build_animated_filters(
        self,
        text: str,
        text_type: str = "hook",
        wpm: int | None = None,
        start_time: float | None = None,
        canvas_width: int = 1080,
        canvas_height: int = 1920,
        override_y: int | None = None,
        override_x: int | None = None,
        override_width: int | None = None,
        align_left: bool = False,
        max_font_size: int | None = None,
        whisper_words: list | None = None,
    ) -> tuple[str, float, int]:
        """Full pipeline: text -> timed word layout -> FFmpeg filters.

        Combines calculate_word_timings + layout_words + generate_ffmpeg_filters
        into a single call, using text_optimizer for font size and safe zone
        positioning.

        Args:
            text:           Text to animate word-by-word.
            text_type:      "hook" or "body" (determines font size + position).
            wpm:            Words per minute.
            start_time:     Delay before first word (seconds).
            canvas_width:   Canvas width in pixels.
            canvas_height:  Canvas height in pixels.
            override_y:     If set, use this y-position instead of text_optimizer's.
                            Used by the orchestrator to stack body below the hook's
                            actual rendered bottom (which may differ from the static
                            layout estimate when word-wrap produces more lines).
            max_font_size:  Optional hard upper bound for computed font size.
            whisper_words:  If set, use Whisper timestamps instead of WPM spacing.

        Returns:
            Tuple of (filter_string, total_animation_duration, rendered_bottom_y).
            rendered_bottom_y is the y-coordinate of the bottom edge of the last
            line of text, useful for stacking subsequent text blocks.
        """
        if not text or not text.strip():
            return "", 0.0, 0

        # Resolve defaults from config
        wpm = wpm if wpm is not None else self.DEFAULT_WPM
        start_time = start_time if start_time is not None else self.START_DELAY

        # Get font size and position from text_optimizer
        if _HAS_TEXT_OPTIMIZER:
            font_size = calculate_optimal_font_size(
                text, text_type, canvas_width, canvas_height,
            )
            if max_font_size is not None:
                font_size = min(font_size, max_font_size)
            pos = calculate_safe_position(text, text_type, canvas_width, canvas_height)
            area_x = override_x if override_x is not None else pos.x
            area_y = override_y if override_y is not None else pos.y
            area_w = override_width if override_width is not None else pos.width
        else:
            # Fallback
            font_size = HOOK_SIZE_RANGE[1] if text_type == "hook" else BODY_SIZE_RANGE[1]
            if max_font_size is not None:
                font_size = min(font_size, max_font_size)
            area_x = override_x if override_x is not None else SAFE_LEFT
            area_y = override_y if override_y is not None else (SAFE_TOP + 100)
            area_w = override_width if override_width is not None else (canvas_width - SAFE_LEFT - SAFE_RIGHT)

        # Shadow offset scales with font size
        shadow_offset = max(4, font_size // 25)

        if whisper_words is not None:
            timings = self.calculate_word_timings_from_whisper(
                text, whisper_words,
            )
        else:
            timings = self.calculate_word_timings(
                text, wpm=wpm, start_time=start_time,
            )
        timings = self.layout_words(
            timings, font_size=font_size,
            x=area_x, y=area_y, max_width=area_w, align_left=align_left,
        )

        filters = self.generate_ffmpeg_filters(
            timings, font_size=font_size, shadow_offset=shadow_offset,
        )

        # Total duration: last word's fade_end
        total_dur = timings[-1].fade_end if timings else 0.0

        # Rendered bottom: last line's y + one line height
        if timings:
            max_line = max(wt.line for wt in timings)
            line_h = int(font_size * 1.45)
            rendered_bottom = area_y + (max_line + 1) * line_h
        else:
            rendered_bottom = area_y

        return filters, total_dur, rendered_bottom

    @staticmethod
    def _escape_word(word: str) -> str:
        """Escape a single word for FFmpeg drawtext filter syntax."""
        word = word.replace("\\", "\\\\\\\\")
        word = word.replace(":", "\\:")
        word = word.replace("'", "'\\\\\\''")
        word = word.replace(";", "\\;")
        word = word.replace("[", "\\[")
        word = word.replace("]", "\\]")
        word = word.replace("%", "%%")
        return word
