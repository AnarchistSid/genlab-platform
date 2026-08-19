"""Base writing strategy with shared LLM + template-fallback content generation.

All non-BB channels share nearly identical writing logic:

1. Load ``templates.yaml`` + optional ``writing.yaml`` from niche config dir.
2. For each story, try LLM via ``write_video_content()`` first.
3. Fall back to template-based caption if LLM is unavailable or fails.
4. Populate per-platform content dicts (instagram, youtube, x_twitter, etc.).

Subclasses must provide:
- ``niche_id``  — channel identifier (used for model routing + logging)
- ``niche_root`` — ``Path`` to the channel root directory (config lives there)

Subclasses may override:
- ``_story_to_video_dict()`` — customize how story dicts map to the video format
- ``_build_caption()`` — customize template-based caption assembly
- ``_model_route_key()`` — the key used to pick the LLM model via ``get_model()``
"""

from __future__ import annotations

import logging
import os
import random
import re
from pathlib import Path
from typing import Any

import yaml

from genlab_core.cache.text_sanitizer import (
    check_for_injection,
    sanitize_text,
)

from .interfaces import WritingStrategy

logger = logging.getLogger(__name__)


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _build_extra_instructions(writing_cfg: dict) -> str:
    """Build extra_instructions string from writing.yaml config.

    Reads banned_phrases, hook_examples, caption_examples, tone_notes.
    Each section is stitched together with double-newline separators so
    the LLM sees them as distinct blocks.
    """
    parts: list[str] = []
    banned = writing_cfg.get("banned_phrases", [])
    if banned:
        parts.append(
            "NICHE-SPECIFIC BANNED PHRASES (in addition to the universal list above):\n"
            + "\n".join(f"  - {p}" for p in banned)
        )
    examples = writing_cfg.get("hook_examples", [])
    if examples:
        parts.append(
            "NICHE HOOK EXAMPLES (style reference — match the voice, don't copy):\n"
            + "\n".join(f"  - {e}" for e in examples)
        )
    caption_examples = writing_cfg.get("caption_examples", [])
    if caption_examples:
        parts.append(
            "NICHE CAPTION EXAMPLES (reaction voice — match the tone + brevity):\n"
            + "\n".join(f"  - {e}" for e in caption_examples)
        )
    tone = writing_cfg.get("tone_notes", "")
    if tone:
        parts.append(f"NICHE TONE: {tone.strip()}")
    return "\n\n".join(parts)


# Improvement A (2026-07-13 audit follow-up): stories that reach the
# writer with thin context (empty summary + no descriptive fields)
# reliably trigger LLM refusal preambles like "I need the Story
# Summary to write a hook for Moana. The...". Historical evidence
# (30-day query 2026-07-13): 10 refusal-hook blueprints, 5 from
# tmdb_trailer with ``summary=""`` overview, 5 from youtube_trending
# with terse descriptions.
#
# ``_MIN_WRITABLE_CONTEXT_CHARS`` is the character-count floor for
# the CONCATENATED context signal — summary OR description_snippet
# is enough; either field alone below this threshold means we skip.
# 40 chars picked because a typical TMDB overview like "Moana sets
# sail on a wayfinding voyage" clears 30 chars, and shorter than
# that offers nothing for the LLM to react to.
_MIN_WRITABLE_CONTEXT_CHARS = 40


def _is_url_dominant(text: str) -> bool:
    """True if `text` is predominantly a URL.

    QB-FIX-02 V4: fetch_reddit_clips historically wrote the Reddit
    permalink as `story["summary"]`. It passed this function's
    length floor (permalinks are >40 chars) but carried zero natural-
    language context — writer produced bare-title hooks like
    "Fortnite", "League of Legends" x5, "Marvel's Spider-Man 2".

    Generalisable check: strip all http(s) URLs; if <40 chars of
    non-URL text remain, treat as URL-dominant and reject even
    though the raw string is long.
    """
    import re as _re

    stripped = _re.sub(r"https?://\S+", "", text).strip()
    return len(stripped) < _MIN_WRITABLE_CONTEXT_CHARS


def _has_writable_context(story: dict) -> bool:
    """True if the story dict has enough content for the LLM to write about.

    Signals (any of these individually meeting the floor is enough):

      * ``summary`` — the primary field, populated by TMDB.overview,
        YouTube description_snippet, RSS content:encoded, etc.
      * ``description_snippet`` — sometimes populated by fetchers as an
        alternative name for the summary.
      * ``description`` — Reddit / niche-specific fetchers.

    A field passes IF (a) it clears ``_MIN_WRITABLE_CONTEXT_CHARS``
    AND (b) it is not URL-dominant (see ``_is_url_dominant``). The
    URL check catches the historical Reddit-permalink-as-summary
    class of bug where the shape passed but semantic content was
    zero.

    Returns False if ALL relevant fields are empty, below the
    minimum-context floor, or URL-dominant. The writer is then
    instructed to skip the story rather than call the LLM (which
    reliably refuses or produces bare-title hooks).
    """
    if not isinstance(story, dict):
        return False
    # Rank fields by likely richness — the writer's actual prompt reads
    # ``summary`` first (see video_content_writer.py:546); the others
    # are fallbacks the fetcher layer sometimes uses.
    for field in ("summary", "description_snippet", "description"):
        value = story.get(field, "") or ""
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        if len(cleaned) < _MIN_WRITABLE_CONTEXT_CHARS:
            continue
        if _is_url_dominant(cleaned):
            continue
        return True
    return False


class BaseWritingStrategy(WritingStrategy):
    """Shared writing logic for all video-first channels.

    Parameters
    ----------
    niche_id:
        Channel identifier (``"sports"``, ``"movies"``, ``"anime"``, etc.).
    niche_root:
        Path to the channel root directory containing ``config/``.
    """

    def __init__(self, niche_id: str, niche_root: Path) -> None:
        self._niche_id = niche_id
        self._niche_root = niche_root
        self._templates: dict | None = None
        self._writing_cfg: dict | None = None
        # NARR-01 (2026-08-18): cache niche.yaml so the narration gate
        # can read narration.enabled without re-loading per blueprint.
        # Lazy — filled by _ensure_config on first use.
        self._niche_config: dict | None = None
        logger.info("[%s] %s initialized", self._niche_id, type(self).__name__)

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    def _ensure_config(self) -> None:
        if self._templates is not None:
            return
        self._templates = _load_yaml(self._niche_root / "config" / "templates.yaml")
        writing_path = self._niche_root / "config" / "writing.yaml"
        self._writing_cfg = _load_yaml(writing_path) if writing_path.exists() else {}
        # NARR-01 (2026-08-18): niche.yaml is optional here — it's loaded
        # upstream by the pipeline runner and passed via context. But the
        # narration gate needs it at writer time, so we cache a local
        # copy. Missing file → empty dict (gate returns False, no crash).
        niche_yaml_path = self._niche_root / "config" / "niche.yaml"
        self._niche_config = (
            _load_yaml(niche_yaml_path) if niche_yaml_path.exists() else {}
        )

    def _model_route_key(self) -> str:
        """Return the model-router key for ``get_model()``. Override per niche."""
        return f"write_{self._niche_id}_content"

    # ------------------------------------------------------------------
    # Story-to-video dict conversion (override for niche-specific fields)
    # ------------------------------------------------------------------

    def _story_to_video_dict(self, story: dict, clip_index: dict | None = None) -> dict:
        """Convert a pipeline story dict to the video dict expected by write_video_content.

        External text (title, summary, tags, channel name) is sanitized before
        leaving this boundary. Scraped YouTube/RSS content is treated as
        untrusted — adversarial creators could craft titles containing
        "ignore previous instructions" to hijack the LLM prompt. See
        .claude/rules/security.md for the full rule set.
        """
        story_id = story.get("story_id", "")
        clip_info = {}
        if clip_index:
            clip_info = clip_index.get("clips", {}).get(story_id, {})

        raw_title = story.get("title", "")
        # 2026-07-22 movies backlog-starvation fix: `_has_writable_context`
        # (see line 95) passes stories where ANY of `summary` /
        # `description_snippet` / `description` clears the 40-char floor,
        # but the LLM prompt at `llm_hook_generator.py:400` reads ONLY
        # `summary`. Movies stories from `TrendingVideoFetcher` populate
        # `description_snippet` (via YouTube API) and leave `summary`
        # empty — so the writer sends "Summary: [empty]" to Claude, which
        # sensibly refuses ("I need the Story Summary to write a hook..."),
        # and the refusal preamble lands as a hook that gets archived.
        # Symptom: SpliceReel dark for 6 days, backlog depleted, 6/6 archived
        # blueprints showed refusal-preamble hooks. Class-of-bug: shared
        # contract, N implementers, silent divergence (see MEMORY.md).
        # Fix: same 3-field precedence as the filter.
        raw_summary = (
            story.get("summary")
            or story.get("description_snippet")
            or story.get("description")
            or ""
        )
        # Bug A fix (2026-07-13 audit W1 trace): previously read
        # ``story.get("source")`` — but ``source`` is the source TYPE
        # (``"youtube_trending"``, ``"twitch_trending"``, etc.), NOT
        # the creator's channel name. The bug meant every reel shipped
        # with the writer's ``format_source_attribution`` receiving
        # ``source_channel_title="youtube_trending"``, producing garbage
        # like "🎬 Original: @youtube_trending" — or nothing at all
        # when Bug C (below) dropped it on the floor. Reads
        # ``channel_name`` (populated by TrendingVideoFetcher.to_story
        # + RSS/Twitch/Reddit fetchers) with source_channel_title as
        # secondary fallback for any legacy paths that use that name.
        raw_channel = story.get("channel_name") or story.get("source_channel_title") or ""
        raw_tags = story.get("tags", []) or []

        # Sanitize: strip HTML, collapse whitespace, drop control chars
        clean_title = sanitize_text(raw_title, max_length=500)
        clean_summary = sanitize_text(raw_summary, max_length=1000)
        clean_channel = sanitize_text(raw_channel, max_length=200)
        clean_tags = [sanitize_text(t, max_length=60) for t in raw_tags[:16] if t]

        # Injection detection: if any field trips the heuristics, log and
        # drop THAT field's value. We don't raise because a single injection
        # pattern shouldn't kill the whole pipeline — the LLM prompt will
        # just miss one field, and the downstream hook generator has its
        # own safeguards (HookValidator).
        for field_name, value in (
            ("title", clean_title),
            ("summary", clean_summary),
            ("channel_name", clean_channel),
        ):
            hits = check_for_injection(value)
            if hits:
                logger.warning(
                    "[%s] Injection heuristic hit in %s for story %s: %s",
                    self._niche_id,
                    field_name,
                    story_id[:16],
                    hits,
                )
                if field_name == "title":
                    clean_title = ""
                elif field_name == "summary":
                    clean_summary = ""
                elif field_name == "channel_name":
                    clean_channel = ""

        # Tags are a list and reach the LLM prompt too — injection-check each
        # and drop offenders, mirroring the scalar-field handling above (R-16).
        checked_tags = []
        for tag in clean_tags:
            tag_hits = check_for_injection(tag)
            if tag_hits:
                logger.warning(
                    "[%s] Injection heuristic hit in tag for story %s: %s",
                    self._niche_id,
                    story_id[:16],
                    tag_hits,
                )
                continue
            checked_tags.append(tag)
        clean_tags = checked_tags

        return {
            "video_id": clip_info.get("video_id", story.get("video_id", story_id)),
            "title": clean_title,
            "channel_name": clean_channel,
            "view_count": story.get("view_count", 0),
            "view_velocity": story.get("view_velocity", 0),
            "age_hours": story.get("age_hours", 1),
            "description_snippet": clean_summary[:300],
            "tags": clean_tags,
            # 2026-07-14 writer wire fix (attribution 0.0% regression):
            # ``write_video_content`` calls ``format_source_attribution``
            # with ``{video_id, source, source_channel_title}``. Prior to
            # this fix, ``source`` defaulted to "youtube_trending"
            # (because this dict didn't carry it) and ``video_url`` /
            # ``source_url`` were never passed, so the fallback URL
            # branch in copyright_safety.format_source_attribution
            # (added b997dad2 for scorebat/tmdb/twitch) NEVER FIRED.
            # Result: every non-YouTube story published without a
            # credit line — 0/6 recent posts had attribution.
            #
            # Pass both ``source`` and ``video_url`` through so
            # format_source_attribution can either derive from
            # (video_id, source) template or fall back to the raw URL.
            "source": story.get("source", ""),
            "video_url": (
                story.get("video_url")
                or story.get("source_url")
                or story.get("canonical_url")
                or ""
            ),
        }

    # ------------------------------------------------------------------
    # Template-based caption (override for niche-specific caption logic)
    # ------------------------------------------------------------------

    def _build_caption(self, story: dict) -> str:
        """Build a caption from story data using templates.yaml config."""
        self._ensure_config()

        title = story.get("title", "")
        summary = story.get("summary", "")
        hook = story.get("content", {}).get("hook", "")

        captions_config = (self._templates or {}).get("captions", {})
        cta_library = captions_config.get("cta_library", [])
        hashtag_pool = captions_config.get("hashtag_pool", [])
        hashtags_per_post = captions_config.get("hashtags_per_post", 4)

        # 2026-07-14: guard against captions with no substantive content.
        # Prior behavior: if hook/title/summary were all empty, the caption
        # still assembled as "{cta}\n\n{hashtags}" — pushed downstream as
        # a "valid" caption and shipped uncredited-of-story. Observed on
        # anime blueprint 32719aa2 today: caption was
        # "Caught up yet?\n\n#Anime #AnimeReels\n\n🎬 Original: ..." — no
        # story content whatsoever. Return empty string so the caller
        # (or downstream stage) can detect the missing content and skip.
        has_body = bool(hook) or bool(title) or bool(summary)
        if not has_body:
            logger.warning(
                "[%s] _build_caption called with empty hook/title/summary — "
                "returning empty caption so downstream can detect and skip",
                self._niche_id,
            )
            return ""

        parts: list[str] = []
        if hook:
            parts.append(hook)
        elif title:
            parts.append(title)

        if summary:
            parts.append(summary[:200])

        if cta_library:
            parts.append(random.choice(cta_library))

        if hashtag_pool:
            selected = random.sample(hashtag_pool, min(hashtags_per_post, len(hashtag_pool)))
            parts.append(" ".join(selected))

        target_length = captions_config.get("target_length", 300)
        caption = "\n\n".join(parts)
        return caption[:target_length] if len(caption) > target_length else caption

    # ------------------------------------------------------------------
    # Template-based story writing
    # ------------------------------------------------------------------

    def _write_story_template(self, story: dict) -> dict:
        """Generate content for a single story using template config."""
        content = story.get("content", {})
        caption = self._build_caption(story)
        # 2026-07-14: _build_caption returns "" when hook/title/summary
        # are all empty. Propagate the skip signal so downstream stages
        # don't render + push a defective blueprint.
        if not caption:
            story["_skip_llm"] = True
            logger.warning(
                "[%s] Template writer produced empty caption (thin story); marking _skip_llm=True",
                self._niche_id,
            )
            return story
        content["caption"] = caption
        content["written"] = True

        title = story.get("title", "")
        hook = content.get("hook", "")
        hashtags = re.findall(r"#\w+", caption)
        content["instagram"] = {"caption": caption, "hashtags": hashtags}
        # Use hook as YouTube title (more engaging than raw headline)
        yt_title = hook if hook else title[:40]
        content["youtube"] = {"title": yt_title, "description": caption}
        content["x_twitter"] = {"tweet": caption[:280]}
        content["facebook"] = {"caption": caption[:300]}
        content["tiktok"] = {"caption": caption[:2200]}
        content["threads"] = {"caption": caption[:500]}

        story["content"] = content
        return story

    def _write_story(self, story: dict) -> dict:
        """Backward-compatible alias for ``_write_story_template()``."""
        return self._write_story_template(story)

    # ------------------------------------------------------------------
    # LLM-based story writing
    # ------------------------------------------------------------------

    def _write_story_llm(
        self,
        story: dict,
        llm_client: Any,
        extra_instructions: str,
        existing_hooks: list[str],
        clip_index: dict | None = None,
    ) -> dict:
        """Generate content for a single story via LLM."""
        from genlab_core.writing.video_content_writer import write_video_content

        video = self._story_to_video_dict(story, clip_index)

        # NARR-01 (2026-08-18): compute narration_target_seconds when
        # the niche is narration-enabled. This is the only place the
        # writer learns whether to ask the LLM for a narration_script.
        # Fail-open: any error in the gate → target stays None →
        # writer prompt is byte-identical to pre-NARR-01. No exception
        # bubbles up.
        narration_target_seconds = None
        try:
            from genlab_core.publishing.narration_gate import (
                is_narration_enabled_for,
            )
            if is_narration_enabled_for(self._niche_id, self._niche_config):
                # Base clip duration = video.duration_seconds. Try a
                # few common shapes since the story→video dict path
                # doesn't always propagate this field.
                #
                # NARR-03 (2026-08-18) fix: original code deferred to
                # legacy output when duration was missing. Prod trigger
                # at 15:11 UTC showed every BB story had duration_seconds
                # unpopulated → narration never fired. Change: fall back
                # to a 30s baseline (typical reel midpoint). The
                # writer's word cap uses this to size the script; the
                # post-synth A4 vo_overrun check catches any actual
                # duration mismatch and degrades cleanly.
                dur = (
                    video.get("duration_seconds")
                    or story.get("duration_seconds")
                    or (story.get("media") or {}).get("duration_seconds")
                    or (story.get("media") or {}).get("clip_duration_seconds")
                )
                if isinstance(dur, (int, float)) and dur > 0:
                    narration_target_seconds = float(dur)
                else:
                    narration_target_seconds = 30.0
                    logger.info(
                        "[%s] narration: no duration_seconds in story "
                        "shape — defaulting to 30s baseline; A4 "
                        "vo_overrun probe will catch actual mismatch",
                        self._niche_id,
                    )
        except Exception as exc:  # noqa: BLE001 — fail-open
            logger.warning(
                "[%s] narration gate check raised: %s — falling back to "
                "legacy writer path (no narration_script)",
                self._niche_id, exc,
            )

        result = write_video_content(
            video=video,
            niche_id=self._niche_id,
            llm_client=llm_client,
            existing_hooks=existing_hooks,
            extra_instructions=extra_instructions,
            narration_target_seconds=narration_target_seconds,
        )

        # Optional retry on near-dupe hook — turns the observability
        # signal (log_similarity_signal below) into a recovery action.
        # Adds ~$0.008/blueprint on retry (2× the write_video_content
        # cost) but unlocks blueprints that would otherwise be dropped
        # by push_to_backlog.py:2408 at persist time. Flag-gated
        # `GENLAB_HOOK_NEAR_DUPE_RETRY_ENABLED` — off by default so
        # operator sees NEAR_DUPE rate for 1-2 weeks first before
        # deciding whether the LLM $ is worth the throughput gain.
        result = self._maybe_retry_on_near_dupe(
            first_result=result,
            video=video,
            existing_hooks=existing_hooks,
            extra_instructions=extra_instructions,
            llm_client=llm_client,
        )

        content = story.setdefault("content", {})
        content["hook"] = result.get("hook", "")
        content["caption"] = result.get("instagram_caption", "")
        content["written"] = True
        content["written_by"] = "llm"

        # NARR-06 fix (2026-08-19): propagate narration_script from the
        # writer result into the content dict. Prior to this line, the
        # LLM was emitting narration_script (verified via standalone
        # reproduction — response JSON contained 350+ chars of legit
        # on-topic commentary), but this cherry-pick propagator only
        # copied hook + instagram_caption. narration_script fell on
        # the floor, GenerateAudio saw empty content["narration_script"],
        # marked the blueprint narration_degraded=true with reason
        # script_generation_failed. Two real-fire recurrences over 2
        # days (NARR-04 manual + NARR-05 02:30 UTC) traced to this
        # missing line.
        #
        # Same class-of-bug as source_attribution Bug C (line ~555):
        # writer emits a field, propagator forgets it, downstream
        # consumer sees empty. Fix pattern: explicit assignment here.
        content["narration_script"] = result.get("narration_script", "")

        # Observability: log when the LLM's FINAL hook (post-retry if
        # retry fired) is still near-dupe to a recent one.
        # push_to_backlog.py:2408 drops such hooks at persist time
        # (Jaccard > 0.6) — this WARN surfaces the pattern earlier so
        # operators can measure the drop-at-persist rate. When retry
        # is off, this is the ONLY early-warning surface. When retry
        # is on, this log fires only for the "retry still failed" case.
        emitted_hook = result.get("hook", "")
        if emitted_hook and existing_hooks:
            try:
                from genlab_core.writing.hook_similarity import (
                    log_similarity_signal,
                )
                log_similarity_signal(
                    emitted_hook,
                    existing_hooks,
                    niche_id=self._niche_id,
                )
            except Exception:
                # Never break the writer for observability
                pass

        # Bug C fix (2026-07-13 audit W1 trace): the writer sets
        # ``result["source_attribution"]`` with the audience-facing credit
        # line ("🎬 Original: @channel — url"), but this propagator only
        # cherry-picks specific fields into ``story["content"]`` — the
        # attribution string was falling on the floor here. push_to_backlog
        # then reads ``story["content"]["source_attribution"]`` (empty),
        # ``_credit`` helper no-ops, and every published caption ships
        # without a visible credit line. Layer 4 warned but didn't block
        # (until the 2026-07-13 flip); Layer 5 metric masked this for
        # weeks by counting ``source_channel_id IS NOT NULL`` as
        # attribution — a signal that IS populated regardless of
        # this bug. Removed by PR #776 audit tightening; that made this
        # long-standing failure visible for the first time.
        if result.get("source_attribution"):
            content["source_attribution"] = result["source_attribution"]

        # Carry the bandit-picked hook style up to story level so
        # push_to_backlog can promote it into the blueprint's extra
        # JSONB. publish_all_platforms reads ``fields["hook_style"]``
        # to populate ``bandit_context["extra_arms"]`` so the 48h
        # reward can credit ``style:{niche}:{name}`` alongside the
        # content_type arm. Without this propagation the style:* arms
        # never accumulate plays (2026-05-20 root cause).
        if result.get("hook_style"):
            story["hook_style"] = result["hook_style"]
            content["hook_style"] = result["hook_style"]

        # 2026-07-06 fix (task #531): propagate caption_segments from
        # video_content_writer's ``result`` into ``story["content"]``.
        # video_content_writer generates them (PR #695) but the
        # transformation orchestrator's caption_style stage reads
        # them from ``blueprint_context["caption_segments"]`` — which
        # my post_render_transform wire (PR #705) populates from
        # ``story.get("content", {}).get("caption_segments")``.
        # Without this propagation the ``result["caption_segments"]``
        # value silently drops on the floor here, and every render
        # since PR #705 has been logging ``skipped=['caption_style']``
        # for that reason (0 of 15 blueprints in the last 12h had
        # caption_segments persisted before this fix). The three
        # caption-related bandit dimensions (caption_style,
        # caption_pacing, caption_emphasis_color) were therefore
        # dormant.
        if result.get("caption_segments"):
            content["caption_segments"] = result["caption_segments"]

        # 2026-06-17 fix: when the LLM returns no `instagram_caption`,
        # fall back through facebook_content → youtube_content → hook
        # → title rather than letting IG/Threads/TikTok silently fail.
        #
        # Why this matters: the 2026-06-17 funnel audit found 90 of 156
        # affiliate-matched blueprints (anime/movies/sports) had empty
        # IG captions, because the LLM occasionally omits
        # `instagram_caption` from its JSON response. `_adapt_instagram`
        # then early-returns on the empty caption, `inject_cta` has no
        # caption to mutate, and the IG affiliate CTA never reaches
        # users — even though `affiliate_product` IS attached to the
        # blueprint. Gaming + ai_creators don't see this because their
        # LLM prompts reliably return `instagram_caption`; the affected
        # niches' prompts are looser.
        #
        # The fallback chain is ordered by content quality:
        #   1. instagram_caption — the LLM's explicit IG output (best)
        #   2. facebook_content — same intent, slightly different shape
        #   3. youtube_content — descriptive but longer
        #   4. hook — short but on-message
        #   5. story title — last resort, never empty
        # The result is then trimmed to IG's ~2200-char limit
        # downstream by `_adapt_instagram`'s `enforce_platform_rules`.
        # Track which fallback level fired so we can WARN-log it.
        # PR #275's upstream retry should have already forced the LLM
        # to emit `instagram_caption`; if we're still falling back
        # here, the retry didn't help and the prompt may need
        # further tightening for this niche. The log is the
        # observability signal that closes the feedback loop.
        _ig_raw = result.get("instagram_caption", "")
        _fb_raw = result.get("facebook_content", "")
        _yt_raw = result.get("youtube_content", "")
        _tw_raw = result.get("twitter_content", "")
        _th_raw = result.get("threads_content", "")
        _hook_raw = result.get("hook", "")
        _title_raw = story.get("title", "")

        # 2026-07-14: hard-stop when the LLM produced a hook but ZERO
        # platform body content. Concrete case: sports blueprint
        # e434882d — hook="Why Barcelona couldn't break down Getafe's
        # wall" (good) but ig/fb/tw/th/yt all empty. `_build_blueprint_
        # fields` in push_to_backlog persisted caption="" which QCGates
        # then rejected as "Missing required field: caption/body" —
        # collapsing sports QC to 0% and firing the `qc_collapse`
        # critical alert.
        #
        # The prior fallback chain masks this by copying the hook into
        # ig_caption when everything else is empty — but the hook is
        # 40-60 chars, not a real caption, and downstream platform
        # adapters expect a body. Better to skip the story cleanly.
        _all_platform_content_empty = not (_ig_raw or _fb_raw or _yt_raw or _tw_raw or _th_raw)
        if _all_platform_content_empty:
            story["_skip_llm"] = True
            logger.warning(
                "[BaseWriting] LLM returned no platform body content "
                "(niche=%s, title=%r, hook=%r) — marking _skip_llm=True. "
                "Consider tightening this niche's writing prompt.",
                self._niche_id,
                (story.get("title", "") or "")[:60],
                _hook_raw[:60],
            )
            return story

        ig_caption = _ig_raw or _fb_raw or _yt_raw or _hook_raw or _title_raw
        if not _ig_raw:
            # Determine which fallback rung satisfied (matches the
            # `or`-chain order so the log tells us exactly what the
            # downstream IG caption was built from).
            if _fb_raw:
                fallback_source = "facebook_content"
            elif _yt_raw:
                fallback_source = "youtube_content"
            elif _hook_raw:
                fallback_source = "hook"
            elif _title_raw:
                fallback_source = "story.title"
            else:
                fallback_source = "<all-empty>"
            logger.warning(
                "[BaseWriting] instagram_caption fallback fired (niche=%s, "
                "fallback_source=%s, title=%r) — LLM omitted IG; PR #275 "
                "retry did not recover. Consider tightening this niche's "
                "writing prompt.",
                self._niche_id,
                fallback_source,
                (story.get("title", "") or "")[:60],
            )
        hashtags = re.findall(r"#\w+", ig_caption)
        content["instagram"] = {"caption": ig_caption, "hashtags": hashtags}

        # Use hook as YouTube title (more engaging than raw headline).
        #
        # 2026-06-21 BUG #1 fix: validate the candidate YT title against
        # the LLM-error-response rule. The headline prod symptom was
        # SpliceReel YT short ``QrNDe-egDrg`` shipping with title
        # ``"I need more story details to write an effective hook...."``
        # — the LLM returned a meta-response instead of a hook, and it
        # got published verbatim as the YouTube video title. None of the
        # 9 form-based validator rules caught it (long enough, no
        # markdown, complete sentence, no URL, etc.) — it was
        # syntactically valid but semantically a refusal.
        #
        # New rule 10 in HookValidator detects LLM error/refusal
        # patterns. When the candidate yt_title trips it, fall back to
        # the cleaned story title — guarantees something user-readable
        # ships even when the LLM had a bad day.
        hook = result.get("hook", "")
        yt_title_candidate = hook if hook else result.get("youtube_content", "")[:40]
        # Import locally to keep base_writing's import surface narrow
        # (HookValidator is in intelligence/, not strategies/).
        from genlab_core.intelligence.hook_validator import (
            HookFailure,
            HookValidator,
        )

        _hv = HookValidator()
        _vr = _hv.validate(yt_title_candidate, platform="youtube")
        if HookFailure.LLM_ERROR_RESPONSE in _vr.failures:
            # Fallback: use the story title (always non-empty per pipeline
            # contract) — truncate to YouTube's 100-char title limit.
            yt_title = (story.get("title") or "Untitled")[:100]
            logger.warning(
                "[BaseWriting] YouTube title LLM-error leak intercepted "
                "(niche=%s, title=%r) — falling back to story.title. "
                "If this fires frequently, the writing prompt may need "
                "tightening for this niche.",
                self._niche_id,
                yt_title_candidate[:80],
            )
        else:
            yt_title = yt_title_candidate
        content["youtube"] = {
            "title": yt_title,
            "description": ig_caption,
        }
        content["x_twitter"] = {"tweet": result.get("twitter_content", "")[:280]}
        content["facebook"] = {"caption": result.get("facebook_content", "")[:300]}
        # Use LLM-generated TikTok/Threads content if available, else fall back to IG
        tk_content = result.get("tiktok_content", "") or ig_caption
        th_content = result.get("threads_content", "") or ig_caption
        tk_hashtags = re.findall(r"#\w+", tk_content)
        content["tiktok"] = {"caption": tk_content[:2200], "hashtags": tk_hashtags}
        content["threads"] = {"caption": th_content[:500]}

        story["content"] = content
        return story

    def _maybe_retry_on_near_dupe(
        self,
        *,
        first_result: dict,
        video: dict,
        existing_hooks: list[str],
        extra_instructions: str,
        llm_client: Any,
    ) -> dict:
        """Optionally retry `write_video_content` with an explicit avoid-
        hint when the first-attempt hook is near-dupe to a recent one.

        Returns the retry result when: flag on AND first attempt was
        near-dupe AND retry produced a non-near-dupe hook. Otherwise
        returns `first_result` unchanged.

        Fail-open at every layer: any exception in similarity check or
        retry call is swallowed and the first attempt is returned.

        Cost: on retry, adds one full `write_video_content` call
        (~$0.008 with Haiku). Flag-gated so operator opts in after
        seeing the NEAR_DUPE rate.
        """
        first_hook = first_result.get("hook", "") or ""
        if not first_hook or not existing_hooks:
            return first_result

        try:
            from genlab_core.settings import env_true

            if not env_true("GENLAB_HOOK_NEAR_DUPE_RETRY_ENABLED"):
                return first_result

            from genlab_core.writing.hook_similarity import find_most_similar

            match = find_most_similar(first_hook, existing_hooks)
            if match is None:
                return first_result
        except Exception as exc:
            logger.debug(
                "[hook_similarity] retry-precheck raised (falling back): %s", exc,
            )
            return first_result

        # Retry: augment extra_instructions with an explicit avoid-hint
        # that names both the rejected hook and the specific match.
        # Include the rejected hook in existing_hooks so the second
        # attempt sees BOTH the historical dupe AND its own prior attempt.
        retry_extra = (
            f"{extra_instructions}\n\n"
            f"CRITICAL RETRY: Your previous hook '{first_hook}' was flagged "
            f"as too similar to a recent hook '{match.matched_hook}' "
            f"(Jaccard {match.similarity:.0%}). Write a hook with a "
            f"COMPLETELY different angle, wording, and topic focus. "
            f"Avoid reusing the shared words."
        )
        try:
            from genlab_core.writing.video_content_writer import write_video_content
            retry_result = write_video_content(
                video=video,
                niche_id=self._niche_id,
                llm_client=llm_client,
                existing_hooks=list(existing_hooks) + [first_hook],
                extra_instructions=retry_extra,
            )
        except Exception as exc:
            logger.warning(
                "[hook_similarity] retry write_video_content raised (keeping first): %s",
                exc,
            )
            return first_result

        retry_hook = retry_result.get("hook", "") or ""
        if not retry_hook:
            logger.info(
                "[hook_similarity] RETRY_EMPTY niche=%s (keeping first)",
                self._niche_id,
            )
            return first_result

        try:
            from genlab_core.writing.hook_similarity import find_most_similar
            retry_match = find_most_similar(
                retry_hook, list(existing_hooks) + [first_hook]
            )
        except Exception:
            retry_match = None

        if retry_match is None:
            logger.info(
                "[hook_similarity] RETRY_SUCCESS niche=%s original=%r retry=%r",
                self._niche_id, first_hook[:60], retry_hook[:60],
            )
            return retry_result

        # Retry ALSO produced a near-dupe. Keep the first-attempt
        # result and let the downstream push_to_backlog drop handle it.
        logger.warning(
            "[hook_similarity] RETRY_FAILED niche=%s first=%r retry=%r "
            "(still Jaccard %.0f%% vs %r)",
            self._niche_id,
            first_hook[:60],
            retry_hook[:60],
            100 * retry_match.similarity,
            retry_match.matched_hook[:60],
        )
        return first_result

    # ------------------------------------------------------------------
    # execute() — shared pipeline entry point
    # ------------------------------------------------------------------

    def execute(self, context: Any) -> Any:
        """Generate captions and platform content for all stories."""
        self._ensure_config()

        stories = context.get("stories", [])
        if not stories:
            logger.info("[%s] WritingStrategy: no stories to write", self._niche_id)
            context.setdefault("run_stats", {})["content_writing"] = {
                "status": "no_stories",
                "written_count": 0,
            }
            return context

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        use_llm = bool(api_key)

        if not use_llm:
            logger.info(
                "[%s] No ANTHROPIC_API_KEY — using template-based writing",
                self._niche_id,
            )

        llm_client = None
        extra_instructions = ""
        if use_llm:
            # 2026-06-21 (Lever H): switched from get_model() to
            # get_model_with_budget() so the router cascades to cheaper
            # models when the run's CostAccumulator says we're past the
            # 10%/25% budget thresholds. Pre-H this was a hardcoded
            # budget_ratio=0.0 and the cascade was dead code.
            from genlab_core.cost.model_router import get_model_with_budget
            from genlab_core.writing.llm_client import AnthropicLLMClient

            model = get_model_with_budget(self._model_route_key())
            llm_client = AnthropicLLMClient(api_key=api_key, model=model)
            extra_instructions = _build_extra_instructions(self._writing_cfg or {})
            logger.info("[%s] Using LLM writing with model=%s", self._niche_id, model)

        clip_index = context.get("clip_index")
        existing_hooks: list[str] = []
        written_count = 0
        failed_count = 0
        llm_count = 0

        for story in stories:
            # Skip clipless stories — VideoGate marked them, no video = no reel
            if story.get("_skip_llm"):
                logger.debug(
                    "[%s] Skipping writing for clipless story: %s",
                    self._niche_id,
                    story.get("title", "")[:40],
                )
                continue

            # Improvement A (2026-07-13 audit follow-up): skip stories with
            # thin writable context before the LLM burns tokens on them.
            # 30-day query surfaced 10 blueprints with "I need the Story
            # Summary..." refusal hooks — 5 from tmdb_trailer with empty
            # overview, 5 from youtube_trending with terse descriptions.
            # The pre-render gate (PR #784) catches the resulting bad
            # hooks at render time, but that still costs LLM calls and
            # puts rejected blueprints on the operator's Focus Review.
            # Filter here to skip the whole class before writing.
            if not _has_writable_context(story):
                logger.info(
                    "[%s] Skipping story with thin context (no writable summary/description): %s",
                    self._niche_id,
                    story.get("title", "")[:60],
                )
                story["_skip_llm"] = True
                continue

            try:
                if use_llm and llm_client is not None:
                    self._write_story_llm(
                        story,
                        llm_client,
                        extra_instructions,
                        existing_hooks,
                        clip_index,
                    )
                    hook = story.get("content", {}).get("hook", "")
                    if not hook:
                        # LLM returned empty hook — story is off-topic or unwritable.
                        # Mark for skip so downstream stages ignore it.
                        story["_skip_llm"] = True
                        logger.info(
                            "[%s] LLM returned empty hook, marking skip: %s",
                            self._niche_id,
                            story.get("title", "")[:60],
                        )
                        continue
                    existing_hooks.append(hook)
                    llm_count += 1
                else:
                    self._write_story_template(story)
                written_count += 1
            except Exception:
                logger.exception(
                    "[%s] Failed to write story: %s",
                    self._niche_id,
                    story.get("title", "?"),
                )
                # Fall back to template on LLM failure
                if use_llm:
                    try:
                        self._write_story_template(story)
                        written_count += 1
                        continue
                    except Exception:
                        logger.warning("Template fallback also failed", exc_info=True)
                failed_count += 1

        status = "llm" if llm_count > 0 else "template_based"
        context.setdefault("run_stats", {})["content_writing"] = {
            "status": status,
            "written_count": written_count,
            "failed_count": failed_count,
            "llm_count": llm_count,
        }

        logger.info(
            "[%s] WritingStrategy: wrote %d stories (%d LLM, %d failed)",
            self._niche_id,
            written_count,
            llm_count,
            failed_count,
        )
        return context
