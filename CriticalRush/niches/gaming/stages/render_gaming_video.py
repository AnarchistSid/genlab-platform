"""Pipeline stage: Render video output — dual-path compilation + single-clip.

Path A (compilation mode): When 3+ stories have local clips, builds a
compilation plan (theme detection, diversity-first selection, pacing,
transitions), runs perceptual dedup, normalizes clips to 9:16, concatenates
with xfade transitions, and adds text overlays. Output goes to blueprints.

Path B (single-clip fallback): Per-story render with hook text overlay.
Tries short-video-maker service first (if flag enabled), falls back to FFmpeg.

Implements genlab-core VisualRenderStrategy interface.

Usage:
    stage = RenderGamingVideo()
    context = stage.execute(context)
"""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
import yaml
from genlab_core.media.ffmpeg_utils import (
    add_text_overlay,
    concat_with_transitions,
    get_duration,
    normalize_clip,
)
from genlab_core.media.ffmpeg_utils import (
    probe_media as probe,
)
from genlab_core.media.frame_compositor import FrameCompositor
from genlab_core.media.sandbox_runner import (
    SandboxedFFmpegRunner,
    sandbox_rendering_enabled,
)
from genlab_core.media.video_compositor import derive_landscape
from genlab_core.media.video_validator import validate_platform_variant
from genlab_core.settings import settings
from genlab_core.strategies import VisualRenderStrategy
from niches.gaming.tools.compilation_planner import (
    build_compilation_plan,
)
from niches.gaming.tools.video_hasher import HashStore, compute_hash

logger = logging.getLogger(__name__)


class VideoValidationError(RuntimeError):
    """Raised when a rendered video fails post-encode quality validation."""


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
NICHE_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_TARGET_DURATION = 25
MIN_CLIPS_FOR_COMPILATION = 3
DEDUP_HAMMING_THRESHOLD = 10

# Hook text wrapping: max chars per line at fontsize=52 on 1080px wide frame
# with 40px margins each side = 1000px usable. At ~19px per char, ~32 chars fit.
HOOK_MAX_CHARS_PER_LINE = 32
HOOK_MAX_TOTAL_CHARS = 80

# Platform → aspect ratio mapping (all platforms unified to 9:16 vertical)
VERTICAL_PLATFORMS = {
    "instagram",
    "youtube",
    "youtube_shorts",
    "tiktok",
    "threads",
    "facebook",
    "x_twitter",
    "twitter",
}
LANDSCAPE_PLATFORMS: set[str] = set()


@dataclass(frozen=True)
class RenderSpec:
    """Target render dimensions and conversion strategy per platform group."""

    width: int
    height: int
    aspect: str
    conversion: str  # "pillarbox_blur" | "passthrough"


def get_render_spec(platform: str) -> RenderSpec:
    """Return the correct render spec for a platform."""
    if platform in VERTICAL_PLATFORMS:
        return RenderSpec(
            width=1080,
            height=1920,
            aspect="9:16",
            conversion="pillarbox_blur",
        )
    return RenderSpec(
        width=1920,
        height=1080,
        aspect="16:9",
        conversion="passthrough",
    )


def wrap_hook_text(hook: str) -> str:
    """Word-wrap hook text to fit within the 9:16 safe zone.

    Caps at HOOK_MAX_TOTAL_CHARS, then wraps at HOOK_MAX_CHARS_PER_LINE.
    Returns text with newlines for FFmpeg drawtext rendering.
    """
    if len(hook) > HOOK_MAX_TOTAL_CHARS:
        hook = hook[:HOOK_MAX_TOTAL_CHARS].rsplit(" ", 1)[0]
        if not hook:
            hook = hook[:HOOK_MAX_TOTAL_CHARS]
    return "\n".join(textwrap.wrap(hook, width=HOOK_MAX_CHARS_PER_LINE))


def _slugify(title: str, max_len: int = 40) -> str:
    """Convert title to a filesystem-safe slug."""
    slug = title.lower().replace(" ", "_")
    slug = re.sub(r"[^a-z0-9_]", "", slug)
    return slug[:max_len]


def _load_yaml(path: Path) -> dict:
    """Load a YAML config file."""
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


class RenderGamingVideo(VisualRenderStrategy):
    """Render video output — dual-path compilation + single-clip fallback."""

    def __init__(self) -> None:
        self._compilation_rules: dict | None = None
        self._overlay_styles: dict | None = None
        self._scoring_config: dict | None = None
        # ARCH #45 (2026-06-13): VideoCompositor was collapsed into a
        # module-level derive_landscape() function — no instance to cache.
        self._frame_compositor: FrameCompositor | None = None
        self._sandbox_runner: SandboxedFFmpegRunner | None = None
        self._owns_sandbox: bool = False

    def _ensure_config(self) -> None:
        """Lazy-load config files."""
        if self._compilation_rules is not None:
            return
        config_dir = NICHE_ROOT / "config"
        self._compilation_rules = _load_yaml(config_dir / "compilation_rules.yaml")
        self._overlay_styles = _load_yaml(config_dir / "overlay_styles.yaml")
        self._scoring_config = _load_yaml(config_dir / "scoring_weights.yaml")

    def _get_sandbox_runner(self) -> SandboxedFFmpegRunner | None:
        """Lazy-create sandbox runner if enabled.

        If _sandbox_runner was injected externally (by SandboxAwareStageRunner),
        returns it without entering or claiming ownership.
        """
        if self._sandbox_runner is not None:
            return self._sandbox_runner
        if sandbox_rendering_enabled():
            genlab_root = NICHE_ROOT.parent.parent  # CriticalRush → GenLab
            self._sandbox_runner = SandboxedFFmpegRunner(genlab_root=genlab_root)
            self._sandbox_runner.__enter__()
            self._owns_sandbox = True
            logger.info("Sandbox rendering enabled for gaming pipeline")
        return self._sandbox_runner

    def _get_frame_compositor(self) -> FrameCompositor:
        """Lazy-create FrameCompositor from visuals.yaml."""
        if self._frame_compositor is None:
            visuals_yaml = NICHE_ROOT / "config" / "visuals.yaml"
            self._frame_compositor = FrameCompositor.from_visuals_yaml(str(visuals_yaml))
        return self._frame_compositor

    # ARCH #45 (2026-06-13): _get_compositor() removed — derive_landscape is
    # now a stateless function called directly with the sandbox_runner kwarg.
    # See call site below.

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._execute_inner(context)
        finally:
            if self._owns_sandbox and self._sandbox_runner is not None:
                self._sandbox_runner.__exit__(None, None, None)
                logger.info("Sandbox renderer cleaned up")
                self._sandbox_runner = None
                self._owns_sandbox = False

    def _execute_inner(self, context: dict[str, Any]) -> dict[str, Any]:
        stories = context.get("stories", [])
        flags = context.get("feature_flags", {})

        run_id = "unknown"
        from genlab_core.context import get_current_context

        ctx = get_current_context()
        if ctx:
            run_id = ctx.run_id

        stats = {
            "rendered": 0,
            "failed": 0,
            "skipped": 0,
            "method_breakdown": {},
            "compilation_built": False,
        }

        # Count stories with local clips for path decision
        stories_with_clips = [s for s in stories if self._get_clip_path(s) is not None]

        use_compilation = (
            flags.get("use_compilation_mode", True)
            and len(stories_with_clips) >= MIN_CLIPS_FOR_COMPILATION
        )

        if use_compilation:
            self._ensure_config()
            comp_result = self._render_compilation(
                stories,
                run_id,
                stats,
                flags,
            )
            if comp_result:
                context.setdefault("blueprints", []).append(comp_result)
                stats["compilation_built"] = True
                stats["method_breakdown"]["compilation"] = 1
                logger.info(
                    "[RENDER] Compilation built: %s (%d clips)",
                    comp_result.get("compilation_id", "?"),
                    comp_result.get("clip_count", 0),
                )
                # Assign compilation path to the FIRST participating story only.
                # Publishing each story with the same compilation video would
                # duplicate posts (F-04). Other stories are marked _in_compilation
                # so Path B skips them, but they don't get rendered_path.
                comp_path = comp_result.get("rendered_path")
                if comp_path:
                    # Apply FrameCompositor to add logo + channel name + hook
                    # (compilation path previously skipped branding — Sprint 59 TODO)
                    primary_story = next((s for s in stories if s.get("_in_compilation")), None)
                    hook_text = (primary_story or {}).get("hook", "") if primary_story else ""
                    if not hook_text:
                        hook_text = (
                            comp_result.get("hook", stories[0].get("hook", "")) if stories else ""
                        )
                    try:
                        fc = self._get_frame_compositor()
                        comp_dur = comp_result.get("duration_seconds") or 55
                        branded_path = str(
                            Path(comp_path).parent / f"{Path(comp_path).stem}_branded.mp4"
                        )
                        # Use overlay_branding (not compose) — compilation is
                        # already 1080x1920, compose() would classify it as
                        # "portrait" and skip branding entirely.
                        fc.overlay_branding(
                            source_video_path=comp_path,
                            hook_text=hook_text,
                            output_path=branded_path,
                            duration_seconds=min(comp_dur, 60),
                        )
                        comp_path = branded_path
                        comp_result["rendered_path"] = branded_path
                        logger.info("[RENDER] Branding overlaid on compilation: %s", branded_path)
                    except Exception as e:
                        logger.warning(
                            "[RENDER] Branding overlay failed on compilation: %s — using unbranded",
                            e,
                        )

                    assigned = False
                    for story in stories:
                        if story.get("_in_compilation"):
                            if not assigned:
                                story.setdefault("media", {})["rendered_path"] = comp_path
                                story["_compilation_primary"] = True
                                assigned = True
                            else:
                                story.setdefault("media", {})["rendered_path"] = None
                    logger.info("[RENDER] Compilation path assigned to primary story")

        # Determine which aspect ratios are needed from enabled platforms
        platforms_enabled = flags.get("platforms_enabled", [])
        need_vertical = (
            any(p in VERTICAL_PLATFORMS for p in platforms_enabled) or not platforms_enabled
        )
        need_landscape = any(p in LANDSCAPE_PLATFORMS for p in platforms_enabled)

        # Path B: single-clip render for stories not in compilation
        # AND non-primary compilation stories (so they get individual renders
        # instead of staying DRAFTED with rendered_path=None).
        for story in stories:
            # Skip the primary compilation story — it already has the comp video
            if story.get("_compilation_primary"):
                continue

            clip_path = self._get_clip_path(story)
            hook = (story.get("content") or {}).get("hook")

            if not clip_path or not hook:
                stats["skipped"] += 1
                story.setdefault("media", {})["rendered_path"] = None
                continue

            title = story.get("title", "untitled")
            slug = _slugify(title)
            output_dir = PROJECT_ROOT / ".tmp" / "rendered" / run_id
            output_dir.mkdir(parents=True, exist_ok=True)

            rendered_path = None
            render_method = None
            rendered_variants: dict[str, str] = {}

            # Try short-video-maker service first (vertical only)
            if flags.get("use_short_video_maker", False):
                output_path = output_dir / f"{slug}.mp4"
                rendered_path = self._try_short_video_maker(clip_path, hook, str(output_path))
                if rendered_path:
                    render_method = "short_video_maker"
                    rendered_variants["vertical"] = rendered_path

            # FrameCompositor: render vertical variant (9:16) for IG / YT Shorts
            if not rendered_path and need_vertical:
                vert_path = output_dir / f"{slug}_vertical.mp4"
                try:
                    fc = self._get_frame_compositor()
                    # Must pass duration — color= filter creates infinite stream
                    clip_dur = story.get("duration_seconds") or 55
                    rendered_path = fc.compose(
                        source_video_path=clip_path,
                        hook_text=hook,
                        output_path=str(vert_path),
                        duration_seconds=min(clip_dur, 60),
                    )
                    render_method = "frame_compositor"
                    rendered_variants["vertical"] = rendered_path
                except Exception as e:
                    logger.warning("[RENDER] FrameCompositor vertical failed: %s", e)

            # ARCH #45 (2026-06-13): derive_landscape is now a module-level
            # function — no compositor instance to cache. Sandbox routing
            # via the optional kwarg.
            if need_landscape:
                land_path = output_dir / f"{slug}_landscape.mp4"
                try:
                    vert_master = Path(rendered_variants.get("vertical", clip_path))
                    derive_landscape(
                        vert_master,
                        land_path,
                        sandbox_runner=self._get_sandbox_runner(),
                    )
                    land_rendered = str(land_path)
                    rendered_variants["landscape"] = land_rendered
                    if not rendered_path:
                        rendered_path = land_rendered
                        render_method = "compositor"
                except Exception as e:
                    logger.warning("[RENDER] Compositor landscape failed: %s", e)

            # Post-encode validation — verify bt709 colorspace on each variant.
            # VMAF skipped: no lossless master at this stage (deferred to Sprint 3
            # when per-platform transcoding from master is added).
            for vkey in list(rendered_variants):
                vpath = rendered_variants[vkey]
                try:
                    valid = validate_platform_variant(
                        master=Path(vpath),
                        variant=Path(vpath),
                        platform=vkey,
                        run_vmaf=False,
                    )
                except Exception as e:
                    logger.warning("[RENDER] validation error for %s variant: %s", vkey, e)
                    valid = False
                if not valid:
                    logger.error(
                        "[RENDER] post-encode validation failed — blocking publish. "
                        "variant=%s path=%s",
                        vkey,
                        vpath,
                    )
                    raise VideoValidationError(f"Post-encode validation failed for {vkey}: {vpath}")

            # Store both the default path and per-platform variants
            story.setdefault("media", {})["rendered_path"] = rendered_path
            story["media"]["rendered_variants"] = rendered_variants
            if render_method == "frame_compositor":
                story["media"]["compositor"] = "frame_compositor"

            if rendered_path:
                stats["rendered"] += 1
                stats["method_breakdown"][render_method] = (
                    stats["method_breakdown"].get(render_method, 0) + 1
                )
                logger.info("[RENDER] %s → %s (%s)", title, rendered_path, render_method)
            else:
                stats["failed"] += 1
                logger.warning("[RENDER] Failed to render '%s'", title)

        context.setdefault("run_stats", {})["render"] = stats
        logger.info(
            "[RENDER] %d rendered, %d failed, %d skipped, compilation=%s",
            stats["rendered"],
            stats["failed"],
            stats["skipped"],
            stats["compilation_built"],
        )
        return context

    # ------------------------------------------------------------------
    # Path A: Compilation
    # ------------------------------------------------------------------

    def _render_compilation(
        self,
        stories: list[dict],
        run_id: str,
        stats: dict,
        flags: dict,
    ) -> dict | None:
        """Build and render a compilation from scored stories.

        Returns a blueprint dict on success, None on failure.
        """
        # Prepare clips for the planner — map story fields to what it expects
        planner_clips = []
        story_by_clip_id = {}
        for story in stories:
            clip_path = self._get_clip_path(story)
            if not clip_path:
                continue

            clip_id = story.get("clip_id") or _slugify(story.get("title", ""), 60)
            duration = story.get("duration_seconds") or get_duration(clip_path) or 0

            planner_clip = {
                "clip_id": clip_id,
                "local_path": clip_path,
                "duration_seconds": duration,
                "final_score": story.get("final_score", story.get("score", 0)),
                "publisher_tier": story.get("publisher_tier", "UNKNOWN"),
                "game_title": story.get("game_title", story.get("title", "")),
                "title": story.get("title", ""),
                "description": story.get("description", ""),
                "streamer_name": story.get("streamer_name", ""),
            }
            planner_clips.append(planner_clip)
            story_by_clip_id[clip_id] = story

        if len(planner_clips) < MIN_CLIPS_FOR_COMPILATION:
            return None

        # Perceptual dedup
        planner_clips = self._dedup_clips(planner_clips)
        if len(planner_clips) < MIN_CLIPS_FOR_COMPILATION:
            logger.warning("[RENDER] Too few clips after dedup (%d)", len(planner_clips))
            return None

        # Build compilation plan
        plan = build_compilation_plan(
            scored_clips=planner_clips,
            compilation_type="short_compilation",
            rules=self._compilation_rules,
            scoring_config=self._scoring_config or {},
        )
        if plan is None:
            logger.warning("[RENDER] Compilation planner returned None")
            return None

        # Normalize and assemble
        output_dir = PROJECT_ROOT / ".tmp" / "rendered" / run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        temp_dir = output_dir / "comp_temp"
        temp_dir.mkdir(parents=True, exist_ok=True)

        # Normalize each clip to 9:16
        normalized_paths = []
        plan_clip_data = []
        for plan_clip in plan["clips"]:
            clip_id = plan_clip["clip_id"]
            story = story_by_clip_id.get(clip_id)
            if not story:
                continue

            clip_path = self._get_clip_path(story)
            if not clip_path:
                continue

            norm_path = str(temp_dir / f"{_slugify(clip_id)}_norm.mp4")
            success = normalize_clip(
                input_path=clip_path,
                output_path=norm_path,
                start=plan_clip.get("start_trim_seconds", 0),
                end=plan_clip.get("end_trim_seconds"),
                target_width=1080,
                target_height=1920,
                target_fps=60,
                mode="pillarbox",
            )
            if success and Path(norm_path).exists():
                normalized_paths.append(norm_path)
                plan_clip_data.append(
                    {
                        "clip_id": clip_id,
                        "story": story,
                        "plan_clip": plan_clip,
                    }
                )
                # Mark story as part of compilation
                story["_in_compilation"] = True
            else:
                logger.warning("[RENDER] Failed to normalize clip %s", clip_id)

        if len(normalized_paths) < MIN_CLIPS_FOR_COMPILATION:
            logger.warning("[RENDER] Too few normalized clips (%d)", len(normalized_paths))
            return None

        # Build transitions list from plan
        transitions = []
        for i, pcd in enumerate(plan_clip_data):
            if i == 0:
                continue
            trans_type = pcd["plan_clip"].get("transition_in", "hard_cut")
            trans_duration = 0.5 if trans_type != "hard_cut" else 0.0
            transitions.append({"type": trans_type, "duration": trans_duration})

        # Concatenate with transitions
        comp_path = str(output_dir / f"compilation_{_slugify(plan.get('theme', 'mixed'))}.mp4")
        success = concat_with_transitions(
            clip_paths=normalized_paths,
            output_path=comp_path,
            transitions=transitions,
            temp_dir=str(temp_dir),
        )
        if not success or not Path(comp_path).exists():
            logger.warning("[RENDER] concat_with_transitions failed")
            return None

        # Add text overlays
        overlays = self._build_overlay_list(plan_clip_data, normalized_paths)
        if overlays:
            overlay_path = str(
                output_dir / f"compilation_{_slugify(plan.get('theme', 'mixed'))}_overlay.mp4"
            )
            overlay_success = add_text_overlay(
                input_path=comp_path,
                output_path=overlay_path,
                overlays=overlays,
            )
            if overlay_success and Path(overlay_path).exists():
                comp_path = overlay_path

        # Post-encode validation — color space gate on compilation output.
        # VMAF skipped: no lossless master (compilation IS the master).
        try:
            comp_valid = validate_platform_variant(
                master=Path(comp_path),
                variant=Path(comp_path),
                platform="compilation",
                run_vmaf=False,
            )
        except Exception as e:
            logger.warning("[RENDER] validation error for compilation: %s", e)
            comp_valid = False
        if not comp_valid:
            logger.error(
                "[RENDER] post-encode validation failed for compilation — "
                "blocking publish. path=%s",
                comp_path,
            )
            raise VideoValidationError(
                f"Post-encode validation failed for compilation: {comp_path}"
            )

        stats["rendered"] += len(plan_clip_data)

        return {
            "compilation_id": plan["compilation_id"],
            "rendered_path": comp_path,
            "clip_count": len(plan_clip_data),
            "theme": plan.get("theme", "mixed"),
            "estimated_duration": plan.get("estimated_total_duration_seconds", 0),
            "diversity_score": plan.get("diversity_score", 1.0),
            "pacing_score": plan.get("pacing_score", 1.0),
            "clip_ids": [pcd["clip_id"] for pcd in plan_clip_data],
        }

    def _dedup_clips(self, clips: list[dict]) -> list[dict]:
        """Remove perceptually duplicate clips using video hashing."""
        hash_store_path = PROJECT_ROOT / ".tmp" / "video_hashes.json"
        store = HashStore(hash_store_path)
        store.load()

        unique = []
        for clip in clips:
            local_path = clip.get("local_path")
            if not local_path:
                unique.append(clip)
                continue

            clip_hash = compute_hash(local_path)
            if clip_hash is None:
                # Can't hash — include it anyway
                unique.append(clip)
                continue

            duplicates = store.find_near_duplicates(clip_hash, threshold=DEDUP_HAMMING_THRESHOLD)
            if duplicates:
                logger.info(
                    "[RENDER] Dedup: dropping '%s' — near-duplicate of %s (distance=%d)",
                    clip.get("title", "?"),
                    duplicates[0].get("clip_id", "?"),
                    duplicates[0].get("hamming_distance", -1),
                )
                continue

            store.add(
                clip_id=clip.get("clip_id", ""),
                hash_hex=clip_hash,
                source_url=clip.get("source_url", ""),
            )
            unique.append(clip)

        store.save()
        return unique

    def _build_overlay_list(
        self,
        plan_clip_data: list[dict],
        normalized_paths: list[str],
    ) -> list[dict]:
        """Build text overlay dicts for the compiled video."""
        templates = (self._overlay_styles or {}).get("overlay_templates", {})
        fonts = (self._overlay_styles or {}).get("fonts", {})

        overlays = []
        running_time = 0.0

        for i, pcd in enumerate(plan_clip_data):
            story = pcd["story"]
            clip_dur = get_duration(normalized_paths[i]) if i < len(normalized_paths) else 0
            if clip_dur <= 0:
                clip_dur = pcd["plan_clip"].get("effective_duration_seconds", 5)

            # Streamer name overlay
            streamer = story.get("streamer_name", "")
            if streamer:
                tmpl = templates.get("streamer_name", {})
                font_key = tmpl.get("font", "montserrat_bold")
                font_file = fonts.get(font_key, {}).get("file", "")
                if font_file:
                    font_path = str(PROJECT_ROOT / font_file)
                    overlays.append(
                        {
                            "text": streamer,
                            "font_file": font_path,
                            "size": tmpl.get("size_px", 48),
                            "color": tmpl.get("color", "white"),
                            "position": tmpl.get("position", "bottom_left"),
                            "margin_x": tmpl.get("margin_x", 40),
                            "margin_y": tmpl.get("margin_y", 120),
                            "start_time": running_time,
                            "end_time": running_time
                            + min(tmpl.get("duration_seconds", 3), clip_dur),
                            "stroke_color": tmpl.get("stroke_color"),
                            "stroke_width": tmpl.get("stroke_width", 0),
                        }
                    )

            # Game title overlay
            game_title = story.get("game_title", "")
            if game_title:
                tmpl = templates.get("game_title", {})
                font_key = tmpl.get("font", "bebas_neue")
                font_file = fonts.get(font_key, {}).get("file", "")
                if font_file:
                    font_path = str(PROJECT_ROOT / font_file)
                    overlays.append(
                        {
                            "text": game_title,
                            "font_file": font_path,
                            "size": tmpl.get("size_px", 36),
                            "color": tmpl.get("color", "#FFD700"),
                            "position": tmpl.get("position", "top_right"),
                            "margin_x": tmpl.get("margin_x", 40),
                            "margin_y": tmpl.get("margin_y", 60),
                            "start_time": running_time,
                            "end_time": running_time
                            + min(tmpl.get("duration_seconds", 3), clip_dur),
                            "stroke_color": tmpl.get("stroke_color"),
                            "stroke_width": tmpl.get("stroke_width", 0),
                        }
                    )

            running_time += clip_dur

        return overlays

    # ------------------------------------------------------------------
    # Path B: Single-clip render (existing logic preserved)
    # ------------------------------------------------------------------

    @staticmethod
    def _get_clip_path(story: dict) -> str | None:
        """Extract clip file path from a story dict. Returns None if missing or file absent."""
        clip = (story.get("media") or {}).get("clip")
        if not clip:
            return None
        clip_path = clip.get("file_path") if isinstance(clip, dict) else clip
        if clip_path and Path(clip_path).exists():
            return clip_path
        return None

    def _try_short_video_maker(self, clip_path: str, hook: str, output_path: str) -> str | None:
        """Try the short-video-maker Docker service."""
        try:
            svm_url = settings.short_video_maker_url
            resp = requests.post(
                f"{svm_url}/api/render",
                json={
                    "clip_path": clip_path,
                    "overlay_text": hook,
                    "output_path": output_path,
                    "aspect_ratio": "9:16",
                },
                timeout=120,
            )
            resp.raise_for_status()
            if Path(output_path).exists():
                return output_path
            return None
        except Exception as e:
            logger.debug("[RENDER] short-video-maker failed: %s", e)
            return None

    @staticmethod
    def _fetch_igdb_cover(cover_url: str, output_dir: Path) -> str | None:
        """Download IGDB cover art image. Returns local path or None."""
        # Upgrade to large cover size
        url = cover_url.replace("t_thumb", "t_cover_big").replace("t_cover_small", "t_cover_big")
        if "t_cover_big" not in url:
            url = url.replace("/igdb/image/upload/", "/igdb/image/upload/t_cover_big/")
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            cover_path = output_dir / "igdb_cover.jpg"
            cover_path.write_bytes(resp.content)
            logger.info("[RENDER] Downloaded IGDB cover art from %s", url)
            return str(cover_path)
        except Exception as e:
            logger.warning("[RENDER] Failed to download IGDB cover: %s", e)
            return None

    def _render_with_ffmpeg(
        self,
        clip_path: str,
        hook: str,
        output_path: str,
        story: dict[str, Any] | None = None,
        spec: RenderSpec | None = None,
    ) -> str | None:
        """Render with FFmpeg using per-platform aspect ratio and filter chain.

        For vertical (9:16) — blurred pillarbox: scale source to fill background,
        blur it, overlay the sharp original centered at native aspect ratio.

        For landscape (16:9) — passthrough: scale to fit with hook text overlay.

        Uses textfile= instead of text= to avoid FFmpeg drawtext quoting issues.
        """
        if spec is None:
            spec = get_render_spec("instagram")  # default: vertical

        target_duration = DEFAULT_TARGET_DURATION
        tw, th = spec.width, spec.height

        # Check for pexels fallback — try IGDB cover art as background
        bg_image_path = None
        if story and spec.conversion == "pillarbox_blur":
            source_tier = ((story.get("media") or {}).get("clip") or {}).get("source_tier", "")
            cover_url = story.get("cover_url")
            if source_tier == "pexels_fallback" and cover_url:
                output_dir = Path(output_path).parent
                output_dir.mkdir(parents=True, exist_ok=True)
                bg_image_path = self._fetch_igdb_cover(cover_url, output_dir)
                if bg_image_path:
                    logger.info("[RENDER] Using IGDB cover art instead of Pexels B-roll")
                else:
                    logger.info("[RENDER] No IGDB cover art — keeping Pexels B-roll")

        # Word-wrap hook text
        wrapped_hook = wrap_hook_text(hook)
        line_count = wrapped_hook.count("\n") + 1
        line_height = 62  # fontsize=52 + 10px line gap
        box_height = line_count * line_height + 20  # padding
        box_y = max(0, 150 - 10)  # top of drawbox (notch-safe y=150)

        # Write hook text to a temp file — avoids all FFmpeg drawtext quoting issues
        hook_file = None
        try:
            hook_file = tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".txt",
                delete=False,
                encoding="utf-8",
            )
            hook_file.write(wrapped_hook)
            hook_file.close()
            hook_file_escaped = hook_file.name.replace(":", "\\:")

            # Build the drawtext suffix (shared by all filter chains)
            drawtext_suffix = (
                f"drawbox=x=0:y={box_y}:w={tw}:h={box_height}:color=black@0.5:t=fill,"
                f"drawtext=textfile='{hook_file_escaped}'"
                f":fontsize=52:fontcolor=white"
                f":x=(w-text_w)/2:y=150"
                f":line_spacing=10"
                f":borderw=2:bordercolor=black@0.7[out]"
            )

            if bg_image_path:
                # Cover art background: blur the cover, overlay clip on top
                filter_complex = (
                    f"[1:v]scale={tw}:{th}:force_original_aspect_ratio=increase,"
                    f"crop={tw}:{th},gblur=sigma=20[bg];"
                    f"[0:v]scale={tw}:-2[sharp];"
                    f"[bg][sharp]overlay=(W-w)/2:(H-h)/2,"
                    f"{drawtext_suffix}"
                )
            elif spec.conversion == "pillarbox_blur":
                # Blurred pillarbox: scale source up to fill, blur as bg,
                # overlay sharp center at native aspect ratio
                filter_complex = (
                    f"[0:v]split[bg][fg];"
                    f"[bg]scale={tw}:{th}:force_original_aspect_ratio=increase,"
                    f"crop={tw}:{th},gblur=sigma=20[blurred];"
                    f"[fg]scale={tw}:-2[sharp];"
                    f"[blurred][sharp]overlay=(W-w)/2:(H-h)/2,"
                    f"{drawtext_suffix}"
                )
            else:
                # Landscape passthrough: scale to fit 16:9
                filter_complex = (
                    f"[0:v]scale={tw}:{th}:force_original_aspect_ratio=decrease,"
                    f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:black,"
                    f"{drawtext_suffix}"
                )

            # Detect audio in the clip (input 0) — never map audio from cover image
            clip_info = probe(clip_path)
            clip_has_audio = (
                clip_info is not None and clip_info.get("audio", {}).get("channels", 0) > 0
            )

            cmd = ["ffmpeg", "-y", "-i", clip_path]
            if bg_image_path:
                # Add cover art as second input; loop it to match clip duration
                cmd += ["-loop", "1", "-i", bg_image_path]
            cmd += [
                "-t",
                str(target_duration),
                "-filter_complex",
                filter_complex,
                "-map",
                "[out]",
            ]
            if clip_has_audio:
                cmd += ["-map", "0:a", "-c:a", "aac", "-b:a", "192k"]
            encode_args = [
                "-c:v",
                "libx264",
                "-crf",
                "17",
                "-pix_fmt",
                "yuv420p",
                "-color_primaries",
                "bt709",
                "-color_trc",
                "bt709",
                "-colorspace",
                "bt709",
                "-movflags",
                "+faststart",
                output_path,
            ]

            # Truncate clips >60s before render (spec: 15-60s target)
            clip_duration = get_duration(clip_path) or 0
            if clip_duration > 60:
                logger.info(
                    "[RENDER] Truncating clip from %.0fs to 60s: %s",
                    clip_duration,
                    clip_path,
                )
                truncated = clip_path.replace(".mp4", "_trunc.mp4")
                trunc_cmd = [
                    "ffmpeg",
                    "-y",
                    "-i",
                    clip_path,
                    "-t",
                    "60",
                    "-c",
                    "copy",
                    truncated,
                ]
                try:
                    subprocess.run(trunc_cmd, capture_output=True, timeout=30)
                    if Path(truncated).exists() and Path(truncated).stat().st_size > 0:
                        clip_path = truncated
                        clip_duration = 60
                        # Update the -i input in the command
                        cmd[cmd.index("-i") + 1] = clip_path
                except (subprocess.TimeoutExpired, Exception) as trunc_err:
                    logger.warning("[RENDER] Truncation failed, using original: %s", trunc_err)

            # Dynamic timeout: 3x clip duration, minimum 300s
            base_timeout = max(300, int((clip_duration or 60) * 3))

            for preset, timeout_mult in [("medium", 1.0), ("ultrafast", 1.5)]:
                actual_timeout = int(base_timeout * timeout_mult)
                full_cmd = cmd + ["-preset", preset] + encode_args
                try:
                    result = subprocess.run(
                        full_cmd,
                        capture_output=True,
                        text=True,
                        timeout=actual_timeout,
                    )
                    if result.returncode == 0 and Path(output_path).exists():
                        if preset != "medium":
                            logger.info("[RENDER] Succeeded with fallback preset=%s", preset)
                        return output_path
                    if result.returncode != 0:
                        logger.warning(
                            "[RENDER] FFmpeg failed (preset=%s, exit %d): %s",
                            preset,
                            result.returncode,
                            result.stderr[:300],
                        )
                except subprocess.TimeoutExpired:
                    logger.warning(
                        "[RENDER] FFmpeg timed out (preset=%s, %ds) — %s",
                        preset,
                        actual_timeout,
                        "retrying with ultrafast" if preset == "medium" else "giving up",
                    )
                    # Clean up partial output before retry
                    Path(output_path).unlink(missing_ok=True)

            return None
        except Exception as e:
            logger.warning("[RENDER] FFmpeg error: %s", e)
            return None
        finally:
            if hook_file:
                Path(hook_file.name).unlink(missing_ok=True)
