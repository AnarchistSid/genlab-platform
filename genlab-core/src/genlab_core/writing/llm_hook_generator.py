"""Generate story-specific hooks via Claude Haiku.

Called by niche hook strategies when ANTHROPIC_API_KEY is set.
Returns None on any failure — callers fall back to template-based hooks.

Bandit-driven style hint (2026-05-17):
  When ``bandit_arms`` contains rows like ``style:question`` for a
  niche, ``pick_hook_style`` Thompson-samples among them and the
  chosen style is injected into the LLM system prompt as a one-line
  hint. The story's ``hook_style`` field records which arm was used
  so the feedback loop can attribute reward (extension to multi-arm
  updates in metric_collector is tracked separately).
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)


# Regex patterns banned in addition to ``_BANNED_PHRASES`` exact-match list.
#
# The "X just <verb>" template — 80-90% saturated across published hooks
# in 4 of 5 niches (audit 2026-05-18).  CLAUDE.md flagged this template
# pattern as forbidden ("No generic templates") but the exact-phrase
# banned list only catches specific suffixes ("nobody saw it coming"
# etc.).  This pattern catches the lead-in itself so the LLM is forced
# to find a different opening structure.
#
# Match condition: "just" appears within the first 4 words, followed by
# any word — i.e. the hook OPENS with the "<entity> just <action>"
# template.  A hook with "just" later in the sentence is fine.
_BANNED_PATTERNS: list[re.Pattern[str]] = [
    # "<entity> just <action>" headline lead-in. Previous version was
    # capped at 3 entity tokens which let multi-word names pass — e.g.
    # "Cyclops & The Thing just broke..." (4 tokens) shipped through.
    # 2026-05-21 audit found six such hooks in the published log. Widen
    # to 8 tokens so even chunky team-list openers ("Mitchell, Allen &
    # Merrill just dropped") get caught.
    re.compile(r"^(\S+\s+){0,8}just\s+\w+", re.IGNORECASE),
]


# Hook style taxonomy. Each style maps to a one-line instruction that
# becomes part of the LLM system prompt. Adding a style here is a
# breaking change for the bandit posterior (new arms need to be
# seeded) — keep the set stable across deploys.
_HOOK_STYLES: dict[str, str] = {
    "question": (
        "Phrase the hook as a direct question to the viewer. Make them "
        "want to click for the answer."
    ),
    "bold_claim": (
        "Start with a confident, declarative statement. Use a "
        "superlative or absolute (most, never, only)."
    ),
    "controversy": (
        "Frame the hook around a tension, dispute, or opinion that "
        "splits viewers. Avoid neutral language."
    ),
    "revelation": (
        "Frame the hook as if revealing a hidden truth or behind-the-"
        "scenes detail. Use 'why', 'how', or 'what nobody told you'."
    ),
    "comparison": (
        "Frame the hook as a direct comparison or contrast — X vs Y, "
        "before vs after, expected vs actual."
    ),
}


def pick_hook_style(niche_id: str) -> str | None:
    """Thompson-sample a hook style for ``niche_id``.

    Reads ``bandit_arms`` rows whose ``arm_id`` starts with ``style:``
    and draws Beta(alpha, beta) for each. Returns the style with the
    highest sample, stripped of the ``style:`` prefix.

    Returns None if:
      - No style arms exist for this niche (cold-start scenario).
      - BacklogClient creation fails.
      - Any unexpected error occurs.

    None is the well-defined "no bandit influence" signal — the caller
    proceeds with a vanilla LLM prompt.
    """
    try:
        from genlab_core.http.backlog_client import BacklogClient
        from genlab_core.learning.arm_loader import load_all_arms
    except ImportError:
        return None

    try:
        client = BacklogClient()
    except Exception:
        return None

    proxy = getattr(client, "bandit_arms", None)
    if proxy is None:
        return None

    arms = load_all_arms(proxy, niche_id)
    # arm_id format is "style:{niche}:{name}" — bandit_arms has
    # UNIQUE(arm_id) so the niche segment is required even though it
    # duplicates the niche_id column. Strip both prefix levels.
    style_arms: dict[str, tuple[float, float]] = {}
    legacy_prefix = "style:"  # for backwards-compat with the simpler form
    niche_prefix = f"style:{niche_id}:"
    for arm_id, (alpha, beta) in arms.items():
        if arm_id.startswith(niche_prefix):
            name = arm_id[len(niche_prefix) :]
        elif arm_id.startswith(legacy_prefix) and ":" not in arm_id[len(legacy_prefix) :]:
            # Legacy "style:question" form (single-niche deployment)
            name = arm_id[len(legacy_prefix) :]
        else:
            continue
        style_arms[name] = (alpha, beta)
    if not style_arms:
        return None

    import random

    best_sample = -1.0
    best_style: str | None = None
    for name, (alpha, beta) in style_arms.items():
        a = alpha if alpha > 0 else 1.0
        b = beta if beta > 0 else 1.0
        try:
            sample = random.betavariate(a, b)
        except (ValueError, OverflowError):
            sample = 0.5
        if sample > best_sample:
            best_sample = sample
            best_style = name

    if best_style not in _HOOK_STYLES:
        # Bandit sampled an unrecognized arm name. Don't inject a
        # malformed hint; treat as cold-start.
        return None
    return best_style


_BANNED_PHRASES = [
    # Generic hype
    "this changes everything",
    "changed everything",
    "nobody saw this coming",
    "nobody expected",
    "nobody saw them",
    "the community is going wild",
    "community is losing it",
    "players need to see this",
    "making waves right now",
    "something big happened",
    "everyone is talking about",
    "you won't believe",
    "no more excuses",
    "must watch",
    "the internet is losing it",
    "fans are not ready",
    "just broke the internet",
    "we just witnessed",
    "about to blow up",
    # Sports-specific
    "this is what clutch looks like",
    "the moment this player",
    "the rivalry continues",
    "the trade that changes",
    "a record that might never",
    "ice in their veins",
    "this player just changed",
    # Movies-specific
    "cinema is back",
    "how did they even film",
    # AI-specific
    "someone just made this",
    "this is an ai video",
    "ai just changed",
    # Anime-specific
    "the anime community",
    "anime fans aren't ready",
    # Over-used patterns
    "chaos erupted",
    "went nuclear",
    "absolutely unhinged",
    "are losing it rn",
    "just became unmissable",
    # Generic LLM tells — every one of these was on the "soft warn"
    # list in the system prompt but never enforced by code, so the
    # LLM kept emitting them anyway. Audit on 2026-05-21 found
    # "literally" + "insane" + "game changer" in published hooks.
    "literally",
    "absolutely insane",
    "absolutely crazy",
    "absolutely wild",
    "mind-blowing",
    "mind blowing",
    "game changer",
    "game-changer",
    "the future is here",
    "everything you need to know",
    "you won't believe what",
    "this could change",
    "wait until you see",
    "hit different",
]

NICHE_STYLE = {
    "sports": {
        "account": "ClutchWire",
        "voice": "electrifying sports fan energy, stats-aware, conversational",
        "audience": "sports fans aged 18-35",
        "example_good": "Jokic dropped 40 in a must-win Game 7",
        "example_bad": "This player just changed everything",
        "top_hooks": [
            "Seahawks betting BIG on their young stars",
            "Rohit did something CSK didn't see coming",
            "Nicol went OFF about that missed pen call",
            "Buzelis dropped 29 on Memphis",
            "Nationals flipped the infield switch",
        ],
    },
    "movies": {
        "account": "SpliceReel",
        "voice": "cinephile but accessible, enthusiastic about craft and storytelling",
        "audience": "movie fans aged 18-40",
        "example_good": "The Brutalist earned every minute of its 3-hour runtime",
        "example_bad": "Cinema is back and this film is proof",
        "top_hooks": [
            "New spy thriller dropped and it's giving Atomic Blonde",
            "Cillian Murphy's back and Shelby family chaos just hit",
            "Brain-dead patient suddenly communicates",
            "Pink Floyd's 1972 Pompeii film just got the 4K treatment",
            "Harriet's spy network went viral",
        ],
    },
    "anime": {
        "account": "FrameDrift",
        "voice": "passionate otaku energy, references anime culture, emotional reactions",
        "audience": "anime fans aged 16-30",
        "example_good": "Gojo's return in JJK S3 just broke Crunchyroll",
        "example_bad": "This anime is about to blow up",
        "top_hooks": [
            "Subaru broke in ways we didn't expect",
            "Witch Hat Atelier got animated and it's GORGEOUS",
            "Tenshi-sama Season 2 went absolutely hard",
            "Needy Girl Overdose anime adaptation got real",
            "Subaru's suffering entered a new dimension",
        ],
    },
    "gaming": {
        "account": "CriticalRush",
        "voice": "hype gamer energy, community-insider, uses gaming slang naturally",
        "audience": "gamers aged 16-30",
        "example_good": "Elden Ring Nightreign sold 5M copies in 72 hours",
        "example_bad": "Players need to see this",
        "top_hooks": [
            "VALORANT players are FUMING rn",
            "Minecraft Dungeons 2 is actually happening",
            "Crimson Desert hit top 3 instantly",
            "Minecraft's going physical in 2027",
            "These clips broke the internet today",
        ],
    },
    "ai_creators": {
        "account": "BlackboxBrief",
        "voice": "tech-savvy creator shocked by what AI can do, accessible urgency",
        "audience": "tech-curious people aged 22-45",
        "example_good": "Sora generated a full Fox News broadcast",
        "example_bad": "Someone just made this with AI",
        "top_hooks": [
            "AI video generation hit a wall we didn't know existed",
            "AI made a full cinematic film overnight",
            "AI replaced my entire management layer",
            "This guy built his entire Product Hunt launch in AI",
            "A short AI-generated sci-fi demo - interactive film",
        ],
    },
}


def generate_hook(
    story: dict[str, Any],
    niche_id: str,
    used_hooks: set[str] | None = None,
    return_style: bool = False,
    return_classifier_score: bool = False,
) -> str | None | tuple[str | None, str | None] | tuple[str | None, str | None, float | None]:
    """Generate a story-specific hook via Claude Haiku.

    Returns None if:
    - ANTHROPIC_API_KEY not set
    - anthropic not installed
    - API call fails
    - Generated hook is in used_hooks

    Args:
        return_style: When True, returns ``(hook, style_name)`` so the
            caller can record which bandit arm was used. ``style_name``
            is None when no bandit influence was applied (cold-start,
            no arms, error). Default False preserves the legacy
            ``str | None`` return for existing callers.
        return_classifier_score: When True (requires ``return_style=True``),
            returns ``(hook, style_name, classifier_score)``. The score
            is the HookClassifier's probability for the winning candidate
            (0.0..1.0, where 0.5 = neutral, >0.5 = predicted to perform
            well). Returns ``None`` for the score when:
              - only one candidate generated (no classifier ranking)
              - classifier load/scoring failed (caught + fallback path)
              - generation failed before classifier was reached
            Default False preserves the legacy return shape.

    2026-06-21 (Lever D1): added classifier-score return path. Pre-D1
    the HookClassifier's predict_proba result was computed for ranking
    candidates but discarded as soon as the winner was picked. The
    score is a learned signal worth keeping — downstream stages and
    the bandit context can use it as an additional feature.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        if return_classifier_score and return_style:
            return (None, None, None)
        return (None, None) if return_style else None

    try:
        import anthropic
    except ImportError:
        logger.debug("anthropic not installed — skipping LLM hook generation")
        if return_classifier_score and return_style:
            return (None, None, None)
        return (None, None) if return_style else None

    style = NICHE_STYLE.get(niche_id, NICHE_STYLE["gaming"])
    title = story.get("title", "")
    summary = (story.get("summary", "") or "")[:300]

    if not title:
        return (None, None) if return_style else None

    # Bandit-driven style hint — only takes effect when style:* arms
    # have been seeded for this niche. Cold-start (no arms) silently
    # falls through to the vanilla prompt.
    chosen_style = pick_hook_style(niche_id)
    style_hint = ""
    if chosen_style and chosen_style in _HOOK_STYLES:
        style_hint = f"\n\nSTYLE TARGET: {_HOOK_STYLES[chosen_style]}"

    banned_text = "\n".join(f"  - {p}" for p in _BANNED_PHRASES)
    used_text = ""
    if used_hooks:
        used_text = (
            "\n\nThese hooks are ALREADY USED — write something COMPLETELY different "
            "(different structure, different words, different angle):\n"
            + "\n".join(f"  - {h}" for h in list(used_hooks)[-15:])
        )

    system = (
        f"You write viral hooks for {style['account']}, a short-form video brand.\n"
        f"Voice: {style['voice']}\n"
        f"Audience: {style['audience']}\n\n"
        "A hook is the text overlay on a trending video reel. It must:\n"
        "- Be 20-60 characters (aim for 40-50)\n"
        "- Reference something SPECIFIC from the story (a name, team, title, event)\n"
        "- Create curiosity or emotional reaction\n"
        "- Contain at least one proper noun from the story\n\n"
        # CRITICAL anti-fabrication rules (added 2026-06-20 after a SpliceReel
        # blueprint titled "Does Pixar's Pressure actually fix what broke
        # Toy Story 4?" shipped for the 2025 submarine thriller *Pressure*
        # — pure hallucination, Pixar/Toy Story 4 nowhere in the source
        # story). The hook MUST be grounded in what's actually in the story.
        "STRICT ANTI-FABRICATION RULES (the hook ships to thousands of viewers — "
        "fabrications make the brand look untrustworthy and ruin engagement):\n"
        "- Use ONLY proper nouns that appear in the Story title or Summary "
        "below. Do NOT introduce other movies, studios, directors, franchises, "
        "team names, players, games, or AI tools that aren't named there.\n"
        "- Do NOT invent sequel numbers (e.g. 'Pressure 2', 'Toy Story 4') "
        "or franchise associations (e.g. 'Pixar's X' when Pixar isn't in "
        "the story).\n"
        "- Do NOT compare to other named works unless that comparison "
        "appears in the source Story or Summary.\n"
        "- Do NOT add factual claims (records broken, statistics, prizes) "
        "that aren't present in the source text.\n"
        "- When unsure whether a fact is in the source, OMIT it — a vaguer "
        "hook grounded in the source beats a punchy one that fabricates.\n\n"
        f'GOOD example: "{style["example_good"]}"\n'
        f'BAD example: "{style["example_bad"]}"\n\n'
        "BANNED phrases (never use these or variations):\n"
        f"{banned_text}\n\n"
        "BANNED OPENING TEMPLATE: Do NOT start the hook with "
        "'<entity> just <verb>' (e.g. 'Lakers just got punched', "
        "'SGA just won MVP', 'Anthropic just dropped a model'). "
        "Find a different opening — question, comparison, revelation, "
        "or contextual setup.\n\n"
        "No news language: BREAKING:, JUST IN:, announces, reveals\n"
        "No markdown. No quotes around your answer.\n"
        "Write ONE hook. Nothing else."
        f"{style_hint}"
    )

    # Add few-shot examples from top performers
    top_hooks = style.get("top_hooks", [])
    if top_hooks:
        examples_text = "\n".join(f'  {i + 1}. "{h}"' for i, h in enumerate(top_hooks))
        system += (
            f"\n\nTOP PERFORMING HOOKS (match this energy and specificity):\n{examples_text}\n"
        )

    # 2026-06-22 episodic RAG — surface recent operator rejection
    # patterns. PR #461 wires operator_review events with
    # feedback_issue (weak_hook, too_generic, etc.); this RAG layer
    # queries that signal and injects a "avoid these patterns" block
    # into the prompt. Fail-OPEN: empty string when no data / DB
    # error / no rejections in window — the prompt is unchanged in
    # those cases so this can ship even before episodic_events
    # accumulates per-niche data.
    try:
        from genlab_core.learning.rejection_rag import get_recent_rejection_context

        rag_context = get_recent_rejection_context(niche_id)
        if rag_context:
            system += "\n\n" + rag_context + "\n"
    except Exception as exc:  # noqa: BLE001 — RAG is augmentation only
        logger.debug("[%s] rejection RAG failed (continuing without): %s", niche_id, exc)

    user = f"Story: {title}\nSummary: {summary}" + used_text

    # Generate 3 candidates and pick the best
    used_lower = {h.lower() for h in used_hooks} if used_hooks else set()
    candidates = []
    try:
        client = anthropic.Anthropic(api_key=api_key)
    except Exception as exc:
        logger.warning("[%s] LLM client init failed: %s", niche_id, exc)
        return (None, chosen_style) if return_style else None

    for _ in range(3):
        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=80,
                temperature=0.7,
                messages=[{"role": "user", "content": user}],
                system=system,
            )
            from genlab_core.intelligence.cost_accumulator import record_anthropic_usage

            record_anthropic_usage("claude-haiku-4-5-20251001", response)  # U-03
            hook = response.content[0].text.strip().strip('"').strip("'")
            hook = hook.replace("\u2019", "'").replace("\u2018", "'")
            hook = hook.replace("\u201c", '"').replace("\u201d", '"')
            if len(hook) > 60:
                hook = hook[:57].rsplit(" ", 1)[0] + "..."
            if len(hook) < 15:
                continue
            if used_lower and hook.lower() in used_lower:
                continue
            if any(bp.lower() in hook.lower() for bp in _BANNED_PHRASES):
                continue
            # Also reject the "X just <verb>" lead-in template
            if any(pat.search(hook) for pat in _BANNED_PATTERNS):
                continue
            candidates.append(hook)
        except Exception as exc:
            logger.debug("Hook candidate generation failed: %s", exc)

    if not candidates:
        return (None, chosen_style) if return_style else None

    # Score candidates and pick the best. Two-layer score:
    #   1. Heuristic on feature vector (always available, hand-tuned)
    #   2. Hook classifier learned from 48h reward signal (per niche;
    #      neutral 0.5 when the niche's classifier isn't trained yet)
    # The classifier shifts ranking by ±25% when confident (away from
    # 0.5); when it's neutral the heuristic is the sole signal. This
    # uses the per-niche models trained by genlab-core/scripts/
    # train_hook_classifier.py — see 2026-05-21 hook_training_data ID
    # bridge fix, which was the prerequisite for these models to exist.
    # Track the classifier score of the winning hook so callers can
    # consume it as a learning signal (Lever D1, 2026-06-21). Pre-D1
    # the score was computed for ranking but discarded with the
    # ``scored`` list scope. None means: no classifier ranking happened
    # (single candidate / classifier load failed / fallback path).
    best_clf_score: float | None = None
    if len(candidates) == 1:
        best = candidates[0]
    else:
        try:
            from genlab_core.learning.hook_classifier import HookClassifier
            from genlab_core.learning.hook_features import build_feature_vector

            # Lazy singleton per niche — model load is ~30ms each call.
            global _hook_classifier_cache  # type: ignore[name-defined]
            try:
                cache = _hook_classifier_cache
            except NameError:
                cache = {}
                globals()["_hook_classifier_cache"] = cache
            clf = cache.get(niche_id)
            if clf is None:
                clf = HookClassifier(niche_id=niche_id)
                cache[niche_id] = clf

            scored = []
            for h in candidates:
                feats = build_feature_vector(h)
                heuristic = (
                    feats.get("word_count", 0) * 0.1
                    + feats.get("has_question", 0) * 2.0
                    + feats.get("has_number", 0) * 1.5
                    + feats.get("has_superlative", 0) * 1.0
                    + feats.get("unique_word_ratio", 0) * 1.0
                )
                clf_score = clf.score_hook(h)  # 0..1, 0.5 = neutral
                # Convert clf delta from neutral into a ±25% multiplier
                # so a confident "this hook will work" shifts ranking
                # while a neutral classifier leaves the heuristic alone.
                multiplier = 1.0 + (clf_score - 0.5) * 0.5
                final = heuristic * multiplier
                scored.append((h, final, clf_score))
            scored.sort(key=lambda x: x[1], reverse=True)
            best = scored[0][0]
            best_clf_score = float(scored[0][2])  # Lever D1: capture the winning hook's score
            logger.debug(
                "[%s] Hook ranked %d candidates with classifier (clf_score of best=%.3f)",
                niche_id,
                len(scored),
                best_clf_score,
            )
            # Lever K (2026-06-21): hook self-critique. After best-of-N
            # selection, ask an LLM "is this hook grounded in the story?"
            # If the critic says no AND we have a second candidate, fall
            # back to #2. Opt-in via env (GENLAB_HOOK_CRITIC_ENABLED=1)
            # for safe shadow rollout. Generalizes PR #411's regex-only
            # LLM-error detection — catches the broader class of
            # "hallucinated entity / unsupported claim" failures.
            try:
                grounded, critique_reason = _critique_hook_grounded(best, story, niche_id)
                if not grounded and len(scored) > 1:
                    logger.warning(
                        "[%s] Hook critic rejected #1 (%s) — falling back to #2",
                        niche_id,
                        critique_reason,
                    )
                    best = scored[1][0]
                    best_clf_score = float(scored[1][2])
            except Exception as critic_exc:
                # The critic MUST NEVER block hook generation — log + use
                # the original best. Pre-Lever-K behavior preserved on
                # critic infra failure.
                logger.debug("[%s] Hook critic failed (non-fatal): %s", niche_id, critic_exc)
        except Exception as exc:
            logger.debug("[%s] Classifier-aware scoring failed: %s", niche_id, exc)
            best = candidates[0]
            # best_clf_score stays None — signals to caller that no classifier signal available

    logger.info(
        "[%s] LLM hook: %s (style=%s)",
        niche_id,
        best,
        chosen_style or "none",
    )
    if return_classifier_score and return_style:
        return (best, chosen_style, best_clf_score)
    if return_style:
        return (best, chosen_style)
    return best


# ────────────────────────────────────────────────────────────────────
# Lever K (2026-06-21): hook self-critique
# ────────────────────────────────────────────────────────────────────


_HOOK_CRITIC_SYSTEM_PROMPT = """You evaluate whether a generated hook is grounded in the source story.

A hook is GROUNDED when:
- Every proper noun in the hook appears in the source title or summary
- Every factual claim is supported by the source
- The hook describes the actual story, not a fabricated/hallucinated one

A hook is NOT grounded when:
- It introduces entities (people, products, places) not in the source
- It makes claims the source doesn't support
- It's a generic meta-response ("I need more story details...")
- It's about a different topic than the source

Respond with ONLY a JSON object: {"grounded": true|false, "reason": "<one short phrase>"}
"""


def _critique_hook_grounded(hook: str, story: dict, niche_id: str) -> tuple[bool, str]:
    """Lever K: ask Claude Haiku if the hook is grounded in the story.

    Returns (grounded, reason). On any failure → (True, "critique_failed") —
    failing OPEN preserves the pre-Lever-K behavior when the critic infra
    is unavailable (no API key, network error, JSON parse error).

    Opt-in via ``GENLAB_HOOK_CRITIC_ENABLED=1`` env var. Default disabled
    so the mechanism can ship + be tested in shadow mode before being
    activated in production. Once shadow data shows the critic catches
    real failures without false positives, flip the env var per-niche.

    Cost: ~1 extra Haiku call per hook generation when enabled (~$0.0002
    per blueprint × ~5 blueprints/day × 5 niches = ~$0.005/day at the
    most-permissive activation).
    """
    if os.environ.get("GENLAB_HOOK_CRITIC_ENABLED", "0") != "1":
        return (True, "critic_disabled")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return (True, "no_api_key")

    try:
        import anthropic
    except ImportError:
        return (True, "anthropic_not_installed")

    try:
        title = (story.get("title") or "")[:300]
        summary = (story.get("summary") or "")[:500]
        user = f"Source title: {title}\nSource summary: {summary}\n\nHook to evaluate: {hook}"

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=80,
            temperature=0.0,  # Deterministic — same hook+story → same verdict
            system=_HOOK_CRITIC_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
        )

        # Cost tracking — match the pattern in other LLM call sites
        try:
            from genlab_core.intelligence.cost_accumulator import record_anthropic_usage

            record_anthropic_usage("claude-haiku-4-5-20251001", response)
        except Exception:
            pass  # Cost tracking failures must NEVER block the critic

    except Exception as exc:
        # API error, network error, etc. — fail open BEFORE we've even
        # got a response to parse. Pre-Lever-K behavior preserved.
        logger.debug("[%s] Hook critic API call failed: %s", niche_id, exc)
        return (True, "critique_failed")

    # Parse the response separately so JSON errors are distinguishable
    # from API errors. Both fail open, but the reason string lets
    # operators tell whether the critic is broken (api) vs misbehaving
    # (returning malformed JSON).
    try:
        raw = response.content[0].text.strip() if response.content else ""

        # Be tolerant of LLM wrapping the JSON in code fences.
        if raw.startswith("```"):
            raw = raw.strip("`").strip()
            if raw.startswith("json"):
                raw = raw[4:].strip()

        parsed = json.loads(raw)
        grounded = bool(parsed.get("grounded", True))
        reason = str(parsed.get("reason", "")).strip()[:80] or "no_reason"
        logger.info(
            "[%s] Hook critic: grounded=%s reason=%s hook=%s",
            niche_id,
            grounded,
            reason,
            hook[:60],
        )
        return (grounded, reason)
    except (json.JSONDecodeError, KeyError, AttributeError, IndexError) as exc:
        # Malformed LLM response — fail open. Logged at debug so a
        # flaky critic doesn't drown the logs.
        logger.debug("[%s] Hook critic returned malformed response: %s", niche_id, exc)
        return (True, "critique_malformed")
    except Exception as exc:
        # Catch-all for anything else (very rare). Fail open.
        logger.debug("[%s] Hook critic post-call exception: %s", niche_id, exc)
        return (True, "critique_failed")


def generate_platform_hooks(
    story: dict[str, Any],
    niche_id: str,
    base_hook: str,
    used_hooks: set[str] | None = None,
) -> dict[str, str]:
    """Generate platform-specific hook variants from a base hook.

    Returns {"instagram": "...", "youtube": "...", "twitter": "...", "facebook": "..."}.
    Falls back to base_hook for all platforms if LLM call fails.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {p: base_hook for p in ("instagram", "youtube", "twitter", "facebook")}

    try:
        import anthropic
    except ImportError:
        return {p: base_hook for p in ("instagram", "youtube", "twitter", "facebook")}

    title = story.get("title", "")

    system = (
        "Adapt this hook for different social media platforms.\n"
        "Each variant must reference the same story but match the platform's style:\n"
        "- Instagram: emotional, scroll-stopping, 40-50 chars\n"
        "- YouTube: question that creates curiosity, \u226440 chars (this is the video title)\n"
        "- Twitter/X: hot take, conversational, provocative, \u226460 chars\n"
        "- Facebook: shareable, asks a question or makes a bold claim, \u226460 chars\n\n"
        "Respond EXACTLY in this format (one per line):\n"
        "instagram: <hook>\n"
        "youtube: <hook>\n"
        "twitter: <hook>\n"
        "facebook: <hook>"
    )

    user = f"Base hook: {base_hook}\nStory: {title}"

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            temperature=0.6,
            messages=[{"role": "user", "content": user}],
            system=system,
        )
        from genlab_core.intelligence.cost_accumulator import record_anthropic_usage

        record_anthropic_usage("claude-haiku-4-5-20251001", response)  # U-03

        text = response.content[0].text.strip()
        result = {}
        for line in text.split("\n"):
            line = line.strip()
            for platform in ("instagram", "youtube", "twitter", "facebook"):
                if line.lower().startswith(f"{platform}:"):
                    hook = line.split(":", 1)[1].strip().strip('"').strip("'")
                    if 10 <= len(hook) <= 60:
                        result[platform] = hook

        # Fill missing platforms with base_hook
        for p in ("instagram", "youtube", "twitter", "facebook"):
            result.setdefault(p, base_hook)

        return result
    except Exception as exc:
        logger.debug("Platform hook generation failed: %s", exc)
        return {p: base_hook for p in ("instagram", "youtube", "twitter", "facebook")}
