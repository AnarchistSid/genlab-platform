"""R-70 part 2 — PR 1 of the sequenced extraction plan.

Concrete shared base for the per-channel visual render strategies in
``SpliceReel/sr_strategies/visual_render.py``,
``ClutchWire/cw_strategies/visual_render.py``, and
``FrameDrift/fd_strategies/visual_render.py``.

What this class ships
---------------------
* **One** concrete method — ``_get_whisper_config`` — verified
  byte-identical across the 3 channels at session-#2 time. Every
  other method is left abstract / ``NotImplementedError`` for the
  pilot migrations (PRs 2-5 of the sequence in
  ``docs/r70-part2-design-phase.md``) to fill in once a body-level
  diff confirms what's truly shared vs channel-specific.

* **One** required instance attribute — ``_visuals_config`` — that
  ``_ensure_config()`` (channel-specific) must populate. The base's
  ``_get_whisper_config`` reads from it; without an ``_ensure_config``
  implementation, the AttributeError surfaces at first use.

This is deliberately a thin skeleton. Per the design doc:

  Risk: very low. Doesn't change any channel behavior.

No channel migrates to this base in PR 1. PR 2 (SR pilot) is the
first migration; the gate for that migration is "all SR tests pass
unchanged."

See ``docs/r70-part2-design-phase.md`` for the full multi-PR plan
and the empirical-divergence baseline that grounded these choices.
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from pathlib import Path
from typing import Any

from genlab_core.media.frame_compositor import FrameCompositor
from genlab_core.rendering.pre_render_quality import check_pre_render_quality
from genlab_core.strategies.interfaces import VisualRenderStrategy

logger = logging.getLogger(__name__)


class BaseVisualRenderStrategy(VisualRenderStrategy):
    """Shared concrete + abstract scaffold for niche visual render.

    Subclasses MUST set ``self._visuals_config`` (a parsed
    ``visuals.yaml`` dict) inside their ``_ensure_config()``
    implementation before any inherited method runs.

    Subclasses MUST also set ``self._log_prefix`` (a niche tag like
    ``"[movies]"``, ``"[sports]"``, ``"[anime]"``) in ``__init__`` —
    the shared methods that log on failure use this so the
    operator-facing message keeps its per-channel signature.
    """

    # Populated by ``_ensure_config`` in subclasses.
    _visuals_config: dict | None

    # Set by subclass ``__init__``. Used by the shared methods
    # (notably ``_compose_frame``) when they log failures. Default
    # provided so the base never blows up if a subclass forgets it
    # — but the default is intentionally generic so a missing set
    # is visible in logs.
    _log_prefix: str = "[visual_render]"

    # Path to the channel's ``config/visuals.yaml`` used by the shared
    # methods (``_compose_frame`` reads it for ``FrameCompositor``).
    # Subclasses set this in ``__init__``; the absent-attribute
    # AttributeError is the right surfacing of a misconfigured base
    # consumer.
    _visuals_yaml_path: Path

    def __init__(self) -> None:
        # Subclasses override and add their own niche-specific state,
        # but they MUST call ``super().__init__()`` to keep this
        # attribute initialization deterministic.
        self._visuals_config = None

    # ── Abstract: each subclass owns its config-loading shape ──────

    @abstractmethod
    def _ensure_config(self) -> None:
        """Load + cache ``visuals.yaml`` (and any other niche configs).

        Must populate ``self._visuals_config`` so the shared
        ``_get_whisper_config`` and other base methods can read from
        it. SpliceReel also reads ``sources.yaml`` here; CW + FD only
        read ``visuals.yaml``. The channel-specific YAML-loading
        decisions belong to the subclass, not the base.
        """
        ...

    # ── Concrete: the one body that's truly shared (verified) ──────

    def _get_whisper_config(self) -> dict:
        """Get whisper_sync config from visuals.yaml.

        Verified byte-identical across SR/CW/FD at extraction time
        (session-#2, 2026-06-11). If a future channel needs a
        different lookup path or default, OVERRIDE this method in
        the subclass — don't generalize the base.
        """
        self._ensure_config()
        animation = (self._visuals_config or {}).get("animation", {})
        wbw = animation.get("word_by_word", {})
        return wbw.get("whisper_sync", {"enabled": False})

    # ── Abstract: pilot migrations decide the shared shape ─────────
    #
    # Each of the methods below is reserved for the migration PRs.
    # Until PR 2 surfaces what's truly shared in each method's body,
    # the base requires subclasses to provide the implementation —
    # nothing's silently inherited that the design phase didn't sign
    # off on.

    @abstractmethod
    def prepare_whisper_words(self, clip_path: Path, story: dict) -> list:
        """Run Whisper on the clip's audio and return aligned word
        timings. Sport/movie/anime channels all have this method
        with similar shape but distinct log prefixes and slightly
        different fallback paths — the body-level diff in PR 2 will
        decide whether the shared body is extractable."""
        ...

    def _compose_frame(self, clip_path: Path, story: dict, context: dict) -> str:
        """Compose video through FrameCompositor. Returns empty string
        on failure.

        R-70 part 2 PR 4: the precondition for extraction held — the
        three channels' bodies were byte-identical except for the log
        prefix string (``[movies]`` / ``[sports]`` / ``[anime]``),
        which the design plan flagged as the exact extractable
        difference. Subclasses set ``self._log_prefix`` in
        ``__init__``; this body uses it verbatim.

        Subclasses MUST also set ``self._visuals_yaml_path`` (an
        absolute ``Path`` to their ``config/visuals.yaml``) in
        ``__init__`` — the body reads it to instantiate the
        compositor.
        """
        run_dir = context.get("run_dir", "")
        if not run_dir:
            return ""

        hook_text = (story.get("content") or {}).get("hook", story.get("title", ""))
        sid = story.get("story_id", "unknown")

        # Improvement B (2026-07-13 audit follow-up): pre-render quality
        # gate — the last check before FFmpeg burns the hook into the
        # video. Catches the 2 recurring failure classes today's fire
        # exposed:
        #   - LLM refusal preambles ("I need the Story Summary...")
        #   - Bare title hooks ("Grand Theft Auto V")
        # These slipped through the writer's own gates because the
        # writer's fallback path repopulates the hook from
        # ``video["title"]`` when the LLM fails, which itself may
        # BE the failure. See genlab_core/rendering/pre_render_quality.py
        # for the rule inventory + rationale.
        #
        # ``check_pre_render_quality`` is imported at module top-level
        # so ``patch("...base_visual_render.check_pre_render_quality")``
        # works for tests that want to bypass the gate.

        # ``_niche_id`` is a concrete-subclass attribute (each niche
        # sets it in __init__). Base + stub tests may not have it — use
        # a defensive fetch so the gate stays niche-agnostic. The value
        # only enriches the log line; it has no effect on the outcome.
        # 2026-07-22: pass ``title`` so rules 4 (hook_equals_title) + 5
        # (hook_title_truncation) can catch the writer-bug-544cf0e9
        # shape. Legacy signature (title="") preserved for callers that
        # don't have story context.
        _qc = check_pre_render_quality(
            hook_text,
            niche_id=getattr(self, "_niche_id", ""),
            title=story.get("title", ""),
        )
        if not _qc.ok:
            logger.warning(
                "%s pre-render quality gate rejected story %s (%s): %s",
                self._log_prefix,
                sid[:16],
                _qc.reason,
                _qc.detail,
            )
            # Return empty path → base_visual_render caller treats this
            # as "no render" → blueprint stays at DRAFTED, operator
            # reviews in Focus Review with the log line as context.
            story.setdefault("media", {})["render_error"] = f"pre_render_quality:{_qc.reason}"
            return ""

        output_dir = Path(run_dir) / "visuals" / sid
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(output_dir / f"{sid[:16]}_reel.mp4")

        try:
            from genlab_core.media.frame_compositor import probe_video

            visuals_yaml = str(self._visuals_yaml_path)
            compositor = FrameCompositor.from_visuals_yaml(visuals_yaml)
            try:
                info = probe_video(str(clip_path))
                dur = min(info.duration_seconds, 60) if info.duration_seconds > 0 else 55
            except Exception as exc:
                # 2026-07-14 class-of-bug fix: was silent ``dur = 55``.
                # ffprobe failure on the source clip is a real signal
                # — either the download was corrupt or ffmpeg is
                # misconfigured. Elevate to WARNING so future audits
                # can grep journalctl for the fingerprint.
                logger.warning(
                    "%s ffprobe failed on %s (%s) — defaulting duration=55s",
                    self._log_prefix,
                    clip_path,
                    exc,
                )
                dur = 55
            # G9 audit follow-up (2026-07-13): burn a small "Original:
            # @creator" watermark into the video canvas — survives
            # platform-side caption edits that strip attribution. Prefer
            # the source channel handle (short, brand-oriented) over the
            # full channel_name; fall back to the latter when handle
            # isn't populated.
            #
            # 2026-07-14 split-signal fix: as a final fallback, parse
            # `content.source_attribution` (the writer's canonical
            # caption credit line) for the @handle. Prior state had
            # different data sources for caption credit vs frame
            # watermark → same class-of-bug as the L5-attribution
            # split-signal fixed on 2026-07-13. See
            # [[class-of-bug-metric-proxies-mask-audience-facing-failures]].
            _source_credit = (
                story.get("source_channel_handle")
                or story.get("channel_name")
                or story.get("source_channel_title")
                or ""
            )
            if not _source_credit:
                _sa = (story.get("content") or {}).get("source_attribution") or ""
                # Writer's canonical shape: "🎬 Original: @handle — url"
                # (see writing/attribution.py format_source_attribution).
                # Extract the @handle if present so caption + watermark
                # cite the same creator.
                import re as _re

                m = _re.search(r"@([A-Za-z0-9_.\-]+)", _sa)
                if m:
                    _source_credit = "@" + m.group(1)
            # Layer 3 S4b (2026-07-17): pull reveal text from writer
            # output when variant_type=question_reveal. Empty string
            # for all other variants → compositor skips reveal filter
            # entirely (default behavior preserved). Compositor only
            # renders reveal in portrait layout (MVP scope); passing
            # non-empty reveal_text to landscape/square logs INFO but
            # falls back to hook-only rendering.
            _reveal_text = (story.get("content") or {}).get("reveal") or ""

            # Layer 3 S6 (2026-07-22): split_screen variant compositor.
            # Flag-gated OFF by default — enables opting a single niche
            # in for canary testing before flipping globally. Reads
            # variant_payload for left/right labels; falls back to
            # generic A/B when not present. Any exception or
            # missing-flag path falls through to the default compose().
            import os as _os
            _variant_type = (story.get("variant_type") or "").lower()
            _split_flag = _os.environ.get("GENLAB_SPLIT_SCREEN_COMPOSITOR_ENABLED", "0") == "1"
            _storytime_flag = _os.environ.get("GENLAB_STORYTIME_COMPOSITOR_ENABLED", "0") == "1"

            if _variant_type == "split_screen" and _split_flag:
                _vp = story.get("variant_payload") or {}
                _left = str(_vp.get("left_label") or "A")[:24]
                _right = str(_vp.get("right_label") or "B")[:24]
                composite_path = compositor.compose_split_screen(
                    source_video_path=str(clip_path),
                    hook_text=hook_text,
                    output_path=output_path,
                    left_label=_left,
                    right_label=_right,
                    duration_seconds=dur,
                    source_credit=_source_credit,
                )
            elif _variant_type == "storytime" and _storytime_flag:
                # Layer 3 S7 phase E (2026-07-22): storytime variant compositor
                # — TTS narration replaces source audio. Word-timed caption
                # overlays deferred to phase F follow-up. narration_text
                # comes from variant_payload (populated by
                # build_storytime_payload from summary / description_snippet).
                _vp = story.get("variant_payload") or {}
                _narration = (_vp.get("narration_text") or "").strip()
                if _narration:
                    composite_path = compositor.compose_storytime(
                        source_video_path=str(clip_path),
                        hook_text=hook_text,
                        output_path=output_path,
                        narration_text=_narration,
                        duration_seconds=dur,
                        source_credit=_source_credit,
                    )
                else:
                    # Payload missing narration → degrade to default compose()
                    # rather than raising. Rare but possible when payload
                    # was populated in a legacy run before the S7 field
                    # contract was tightened.
                    logger.warning(
                        "%s storytime variant missing narration_text — falling "
                        "back to default compose() for story %s",
                        self._log_prefix,
                        sid[:16],
                    )
                    composite_path = compositor.compose(
                        source_video_path=str(clip_path),
                        hook_text=hook_text,
                        output_path=output_path,
                        duration_seconds=dur,
                        source_credit=_source_credit,
                        reveal_text=_reveal_text,
                    )
            else:
                composite_path = compositor.compose(
                    source_video_path=str(clip_path),
                    hook_text=hook_text,
                    output_path=output_path,
                    duration_seconds=dur,
                    source_credit=_source_credit,
                    reveal_text=_reveal_text,
                )
            if not composite_path:
                # 2026-07-14 (media audit F10): persist a render_error so
                # `check_stale_drafted` + `push_to_backlog._derive_render_
                # failure_reason` can distinguish "compositor returned
                # empty" from "not yet attempted." Prior state left this
                # blueprint at DRAFTED with NO render_error → recreated
                # the 2026-05-21 "4 niches stuck since" incident that
                # comment 313-317 below was written to prevent.
                story.setdefault("media", {})["render_error"] = "compositor_returned_empty"
                logger.warning(
                    "%s compositor returned empty path for story %s — "
                    "render_error persisted, blueprint stays DRAFTED",
                    self._log_prefix,
                    sid[:16],
                )
                return ""

            # Task #466 wire (2026-07-06): run the intelligent-
            # transformation orchestrator on the composite. Fail-open —
            # returns the base composite path unchanged if anything
            # goes wrong (flag off, config disabled, missing assets,
            # any error). Every subclass that inherits _compose_frame
            # gets this for free: ClutchWire (sports), SpliceReel
            # (movies), FrameDrift (anime — no-op today because its
            # visuals.yaml has intelligent_transform.enabled: false).
            try:
                from genlab_core.media.post_render_transform import (
                    apply_post_render_transformations,
                )

                niche_id = getattr(self, "_niche_id", None) or (
                    self._log_prefix.strip().strip("[]") or ""
                )
                niche_root = self._visuals_yaml_path.parent.parent
                content = story.get("content") or {}
                blueprint_context = {
                    "hook": hook_text,
                    "caption_segments": content.get("caption_segments"),
                    "title": story.get("title", ""),
                    "summary": story.get("summary", ""),
                }
                # Task #581 (2026-07-08): apply_post_render_transformations
                # now returns (path, arm_ids_by_dimension). The dict lets
                # `register_pending_feedback` route reward per-dimension
                # so the 255 transformation arms actually accumulate
                # observations (they were all α=β=1 pre-fix — the reward
                # wire ended here silently).
                composite_path, _arm_ids = apply_post_render_transformations(
                    composite_path,
                    niche_id=niche_id,
                    niche_root=niche_root,
                    visuals_yaml_path=str(self._visuals_yaml_path),
                    blueprint_context=blueprint_context,
                    video_duration_s=float(dur),
                )
                # Write to the story dict at the top-level so
                # `push_to_backlog._build_blueprint_fields` (see
                # `pipeline/stages/push_to_backlog.py`) can copy it into
                # `fields["arm_ids_by_dimension"]`. Empty dict is normal
                # (flag off, no arms picked, or transformation rejected)
                # and stays out of pending_feedback naturally.
                if _arm_ids:
                    story["arm_ids_by_dimension"] = dict(_arm_ids)
                # 2026-08-18: persist the reject-reason set by
                # apply_post_render_transformations (mutated into
                # blueprint_context) so post-hoc audits can grep
                # `extra.transform_reject_reason` on any blueprint
                # with empty arm_ids_by_dimension and know why. Gaming
                # Aug 16 incident: 4 platform copies shipped with
                # arm_ids={} and no marker survived; had to run the
                # run-report → journal → source-code chase to guess.
                reject_reason = blueprint_context.get(
                    "_transform_reject_reason"
                )
                if reject_reason:
                    story.setdefault("media", {})[
                        "transform_reject_reason"
                    ] = reject_reason
            except Exception as exc:
                # Extra defense: apply_post_render_transformations
                # promises no-raise, but if some import path breaks,
                # never let it kill the base render.
                logger.warning(
                    "%s post_render_transform wire raised (%s) — returning base composite",
                    self._log_prefix,
                    exc,
                )

            # 2026-08-18 canary wire: generate a branded first-frame
            # intro (~0.8s) and prepend to the composite for IG/FB
            # feed engagement. Flag-gated per-niche via
            # GENLAB_HOOK_THUMBNAIL_NICHES; fail-open (any error keeps
            # the base composite unchanged).
            try:
                from genlab_core.media.hook_thumbnail import (
                    generate_hook_thumbnail,
                    is_enabled_for,
                    prepend_intro_to_composite,
                )

                if is_enabled_for(niche_id) and hook_text.strip():
                    composite_dir = Path(composite_path).parent
                    intro_path = str(composite_dir / f"{sid[:16]}_intro.mp4")
                    intro_ok, intro_cost = generate_hook_thumbnail(
                        hook_text, niche_id, intro_path,
                    )
                    if intro_ok:
                        merged_path = str(
                            composite_dir / f"{sid[:16]}_reel_with_intro.mp4"
                        )
                        if prepend_intro_to_composite(
                            composite_path, intro_path, merged_path,
                        ):
                            logger.info(
                                "%s intro prepended niche=%s cost=%s",
                                self._log_prefix, niche_id,
                                f"${intro_cost:.4f}" if intro_cost else "unknown",
                            )
                            composite_path = merged_path
                        else:
                            logger.warning(
                                "%s intro concat failed — keeping base composite",
                                self._log_prefix,
                            )
                        # Best-effort cleanup of the standalone intro
                        try:
                            Path(intro_path).unlink(missing_ok=True)
                        except Exception:  # noqa: BLE001
                            pass
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "%s hook_thumbnail wire raised (%s) — returning base composite",
                    self._log_prefix, exc,
                )

            # 2026-08-18 canary wire (task #193): AI-news data-viz
            # chart intro (~2.5s). Only fires when the hook_thumbnail
            # wire above did NOT prepend an intro (mutually exclusive:
            # one intro type per reel to keep first-3s attention on
            # ONE visual anchor). Flag-gated via
            # GENLAB_CHART_BROLL_NICHES; fail-open (any error keeps
            # the base composite unchanged). Extraction cost ~$0.001
            # via Haiku; ffmpeg render is zero-cost.
            already_has_intro = composite_path.endswith(
                "_reel_with_intro.mp4"
            )
            try:
                from genlab_core.media.chart_broll import (
                    is_enabled_for as chart_broll_is_enabled_for,
                    render_chart_broll,
                )
                from genlab_core.media.chart_data_extract import (
                    extract_chart_data,
                )
                from genlab_core.media.hook_thumbnail import (
                    prepend_intro_to_composite,
                )

                if (
                    chart_broll_is_enabled_for(niche_id)
                    and not already_has_intro
                    and (story.get("summary") or "").strip()
                ):
                    chart_data = extract_chart_data(
                        summary=story.get("summary", "") or "",
                        story_title=story.get("title", "") or "",
                    )
                    if chart_data is not None:
                        composite_dir = Path(composite_path).parent
                        chart_path = str(
                            composite_dir / f"{sid[:16]}_chart.mp4"
                        )
                        chart_ok = render_chart_broll(
                            title=chart_data.title,
                            bars=chart_data.bars,
                            niche_id=niche_id,
                            output_path=chart_path,
                        )
                        if chart_ok:
                            merged_path = str(
                                composite_dir
                                / f"{sid[:16]}_reel_with_chart.mp4"
                            )
                            if prepend_intro_to_composite(
                                composite_path, chart_path, merged_path,
                            ):
                                logger.info(
                                    "%s chart intro prepended niche=%s "
                                    "bars=%d title=%r",
                                    self._log_prefix, niche_id,
                                    len(chart_data.bars),
                                    chart_data.title[:60],
                                )
                                composite_path = merged_path
                            else:
                                logger.warning(
                                    "%s chart concat failed — keeping base",
                                    self._log_prefix,
                                )
                            try:
                                Path(chart_path).unlink(missing_ok=True)
                            except Exception:  # noqa: BLE001
                                pass
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "%s chart_broll wire raised (%s) — returning base composite",
                    self._log_prefix, exc,
                )

            return composite_path
        except Exception as e:
            logger.error("%s FrameCompositor failed: %s", self._log_prefix, e)
            # Persist the exception string onto the story so push_to_backlog
            # can write it to ``blueprint.error_message``. Without this, the
            # stuck DRAFTED blueprints carry NULL error_message and no
            # diagnostic trail — the 4-niches-stuck-since-2026-05-21
            # production incident took weeks to root-cause for exactly
            # this reason. Truncated to 400 chars so the DB column stays
            # readable on the dashboard. Caller behaviour unchanged
            # (still returns ``""``).
            story.setdefault("media", {})["render_error"] = str(e)[:400]
            return ""

    @abstractmethod
    def _build_pexels_queries(self, story: dict) -> list[str]:
        """Build Pexels B-roll search queries. Substantively divergent
        per channel (sport-specific terms vs cinematic vs anime); the
        design phase deliberately left this as per-channel — the base
        forces subclasses to be explicit."""
        ...

    @abstractmethod
    def _render_story(self, story: dict) -> dict:
        """Orchestrate the per-story render pipeline. Small 21-23 line
        divergence in production today; PR 3 decides whether the
        orchestration shape is lift-able."""
        ...

    @abstractmethod
    def execute(self, context: Any) -> Any:
        """Stage entrypoint — already abstract on the parent
        ``VisualRenderStrategy(ABC)``. Re-declared here as an explicit
        reminder that even the orchestration top-level isn't auto-
        inherited until a pilot migration surfaces what's truly
        shared."""
        ...
