"""LLM-as-judge re-ranking for trending video candidates.

Why this exists
---------------
``trending_video_fetcher`` sorts candidates by ``view_velocity`` alone.
view_velocity catches viral signal but is blind to **niche-fit**: a
trending gaming video might be a Twitch streamer's reaction to news
(off-topic for a gameplay channel) while a slightly-slower-trending
gameplay clip would convert better. The 2026-06-22 audit found this
was the highest-leverage smartness gap — better source clips
multiply hook quality, QC pass rate, and engagement.

This module re-ranks the top-N velocity-sorted candidates using a
single Haiku call that picks the top-K most niche-fit videos. The
re-ranking is OPT-IN via ``GENLAB_VIDEO_NICHE_FIT_ENABLED=1`` so
the existing velocity-only path remains available as the safe
fallback during shadow rollout (Lever C/K/REPLY_CRITIC pattern).

Cost: one Haiku call per fetch operation (NOT per video). With 5
niches × 2 fetches/day = 10 calls/day × ~$0.0005 = **~$0.005/day**.

Failure modes (all fail-OPEN — re-ranking is augmentation only):
- env unset → return original list unchanged
- Anthropic circuit open → return original list + warn
- LLM returns unparseable indices → return original list + warn
- LLM picks indices out of bounds → clamp + log + return original
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)


# Hard ceiling on input size — sending more candidates increases token
# cost linearly without proportional gain. Top-20 from velocity sort is
# more than enough signal for the LLM to identify the best 10.
_MAX_CANDIDATES_TO_RANK = 20
_DEFAULT_TOP_K = 10

# Niche-fit advice the LLM uses to judge candidates. Per-niche so the
# prompt is concrete; generic "is this niche-appropriate?" prompts get
# generic answers. Each entry is a 1-2 line specification that the
# operator's content brief implicitly assumes.
_NICHE_FIT_BRIEF: dict[str, str] = {
    "gaming": (
        "Gameplay clips, esports highlights, viral gaming moments. "
        "AVOID: streamer reactions to news, tutorials, unboxings, "
        "general PC/setup content."
    ),
    "sports": (
        "Live game highlights, viral plays, dramatic athletic moments. "
        "AVOID: pre-game interviews, analysis shows, fitness/training "
        "content, fan reactions."
    ),
    "movies": (
        "Movie trailers, viral film clips, box office reactions. "
        "AVOID: behind-the-scenes interviews, actor talk shows, "
        "general entertainment news."
    ),
    "anime": (
        "Trending fight scenes, viral episode moments, anime clips. "
        "AVOID: live-action adaptations, manga reviews, conventions, "
        "MMA/UFC/boxing/wrestling (which our relevance filter "
        "explicitly rejects)."
    ),
    "ai_creators": (
        "AI tool demos, creator explainers, viral AI-generated content. "
        "AVOID: corporate keynotes, tech news with no demo, generic "
        "AI hype content with no creator angle."
    ),
}


def _is_enabled() -> bool:
    """Opt-in check — matches the Lever C/K shadow-rollout pattern."""
    return os.environ.get("GENLAB_VIDEO_NICHE_FIT_ENABLED", "0") == "1"


def _build_prompt(candidates: list[Any], niche_id: str, top_k: int) -> tuple[str, str]:
    """Build (system, user) prompts for the Haiku ranker.

    Splits prompts to give the model: (a) the niche brief as
    instructions in the system prompt (cacheable), (b) the
    indexed candidate list in the user prompt (fresh per call).
    """
    brief = _NICHE_FIT_BRIEF.get(niche_id, "Pick clips that match the niche audience.")
    system = (
        "You are a content scout for a short-form video channel. Your "
        "job is to identify which trending videos best match the "
        "channel's niche from a list of candidates.\n\n"
        f"Niche brief for '{niche_id}':\n{brief}\n\n"
        f"You will receive {len(candidates)} candidates. Return the "
        f"top {top_k} that BEST FIT this niche, in order of fit "
        "(best first). Output ONLY the indices as a comma-separated "
        "list. Example: 3,7,0,12,4,9,11,2,15,8\n\n"
        "Do NOT include explanations, prefixes, or markdown. Just "
        "the comma-separated indices."
    )
    lines = []
    for i, v in enumerate(candidates):
        title = getattr(v, "title", "") or ""
        desc = getattr(v, "description_snippet", "") or ""
        channel = getattr(v, "channel_name", "") or ""
        # Trim each field — keeps the prompt size bounded
        line = f"[{i}] title={title[:120]} | channel={channel[:60]} | desc={desc[:160]}"
        lines.append(line)
    user = "Candidates:\n" + "\n".join(lines)
    return system, user


def _parse_indices(text: str, total: int, top_k: int) -> list[int]:
    """Parse LLM output → ordered list of indices.

    Tolerates: whitespace, extra punctuation, numbers spelled out
    ('1, 2, 3' or '1,2,3'), trailing newlines. Filters out-of-range
    or duplicate indices.
    """
    # Match all integers in the response. Bias toward expected format
    # (comma-separated) but tolerate variation.
    raw = re.findall(r"\d+", text)
    seen: set[int] = set()
    out: list[int] = []
    for s in raw:
        try:
            i = int(s)
        except ValueError:
            continue
        if 0 <= i < total and i not in seen:
            seen.add(i)
            out.append(i)
            if len(out) >= top_k:
                break
    return out


def rerank_for_niche_fit(
    videos: list[Any],
    niche_id: str,
    *,
    top_k: int = _DEFAULT_TOP_K,
) -> list[Any]:
    """Re-rank the top-N velocity-sorted videos by LLM niche-fit judgment.

    Returns a new list where the top-K most niche-fit candidates come
    first (in LLM-judged order), followed by remaining candidates in
    their original velocity-sorted order. Total length unchanged.

    When ``GENLAB_VIDEO_NICHE_FIT_ENABLED`` is not "1", returns the
    input unchanged (zero cost, zero behavior change).

    Args:
        videos: TrendingVideo list, ALREADY velocity-sorted (the
            caller does that; this just re-ranks the front).
        niche_id: Niche identifier (gaming, sports, etc.).
        top_k: How many videos to re-rank from the front. Capped at
            len(videos). Default 10.

    Returns:
        Re-ranked list (new list — input not mutated).
    """
    if not _is_enabled():
        return videos

    if not videos:
        return videos

    # Only re-rank up to MAX candidates; the rest stays in original order
    n_to_rank = min(len(videos), _MAX_CANDIDATES_TO_RANK)
    candidates = videos[:n_to_rank]
    tail = videos[n_to_rank:]
    effective_top_k = min(top_k, n_to_rank)

    try:
        import anthropic

        from genlab_core.http.circuit_breaker import ANTHROPIC_CB, CircuitOpenError
        from genlab_core.intelligence.cost_accumulator import record_anthropic_usage
    except ImportError as exc:
        logger.warning("[niche-fit] dependency import failed: %s", exc)
        return videos

    try:
        from genlab_core.llm.prompt_cache import with_prompt_cache

        client = anthropic.Anthropic()
        system, user = _build_prompt(candidates, niche_id, effective_top_k)

        def _llm_call():
            # U-01: route through prompt-cache helper. The niche-fit
            # ranker system prompt is ~700 chars today (below the
            # 4000-char default threshold) so the helper passes it
            # through as plain string — no behavior change. Wired now
            # so the call site is future-proof.
            return client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=80,
                system=with_prompt_cache(system),
                messages=[{"role": "user", "content": user}],
            )

        resp = ANTHROPIC_CB.call(_llm_call)
        record_anthropic_usage("claude-haiku-4-5-20251001", resp)
        text = resp.content[0].text.strip()
    except CircuitOpenError:
        logger.warning("[niche-fit] Anthropic circuit open — using velocity order")
        return videos
    except Exception as exc:  # noqa: BLE001 — fail-open on any LLM error
        logger.warning("[niche-fit] LLM ranking raised (using velocity): %s", exc)
        return videos

    chosen_indices = _parse_indices(text, total=n_to_rank, top_k=effective_top_k)
    if not chosen_indices:
        logger.warning(
            "[niche-fit] LLM returned no parseable indices: %r — using velocity order",
            text[:120],
        )
        return videos

    # Build reordered list: LLM picks first, then unchosen candidates
    # in their original velocity order, then the original tail.
    chosen_set = set(chosen_indices)
    reordered_front = [candidates[i] for i in chosen_indices]
    unchosen = [v for i, v in enumerate(candidates) if i not in chosen_set]
    result = reordered_front + unchosen + tail

    logger.info(
        "[niche-fit] %s: re-ranked top-%d from %d candidates → first 3 = %r",
        niche_id,
        len(chosen_indices),
        n_to_rank,
        [getattr(v, "title", "")[:50] for v in result[:3]],
    )
    return result
