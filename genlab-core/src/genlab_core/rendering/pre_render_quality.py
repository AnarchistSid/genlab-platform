"""Pre-render quality gate — the last check before FFmpeg burns the hook.

Post-2026-07-13 audit follow-up (Improvement B). Even with attribution
correct (PR #776-#783 arc), today's fire shipped two bad hooks that
downstream layers didn't catch:

  - movies/Moana: ``"I need the Story Summary to write a hook for
    Moana. The..."`` — LLM refusal preamble burned onto the video
  - gaming/GTA V: ``"Grand Theft Auto V"`` — bare title, no take

The writer's own G4 refusal check (video_content_writer.py:588) fired
on the movies hook and set ``content["hook"] = ""`` — but the fallback
path repopulated the field from ``video["title"]``, which happened to
BE the refusal preamble because ``story.title`` was the LLM's raw
output. Bug B (the DB-persist gap) made this invisible in observability
because ``stories.extra`` didn't carry the pre-refusal title separately.

This module sits between the writer's output and the FFmpeg render as
a last line of defense. If a hook reaches this gate broken, we DO NOT
render — the blueprint stays at DRAFTED, operator sees it in Focus
Review with a rejection reason, no reel ships.

## What we check

Three rules, in short-circuit evaluation order (most-severe first):

  1. ``no_llm_refusal_preamble`` — reuses ``_is_llm_refusal`` from the
     writer. Catches "I need the Story Summary...", "I cannot write...",
     "I don't have enough context...". 5/7 of the last 30 days of
     production failures were this shape.
  2. ``min_length_15`` — hooks shorter than 15 chars have no room for
     a take. Cheap check that catches truncation bugs + placeholder
     leftovers.
  3. ``has_verb_signal`` — bare proper-noun hooks like "Grand Theft
     Auto V" ship with no take, no reaction, no story-specificity.
     Verb signal = at least one token matching a common English verb
     shape (ends in -s/-ed/-ing OR in a small imperative-verb list OR
     a lowercase-starting token that isn't a stopword). 1/7 of the
     last 30 days.

Order rationale: min-length fires before bare-title so a super-short
hook is reported as truncation (hook_too_short) rather than a stylistic
issue (hook_bare_title). A hook that is BOTH short AND bare-title is
almost certainly a truncation bug — reporting the length is more
useful to the operator.

## What we don't check here

  - Banned phrases + banned patterns — the writer already enforces
    these (video_content_writer.py:574-587). No need to duplicate.
  - Attribution marker — Layer 4 does this at publish time.
  - Length ≤ 60 chars — writer already truncates.

Not checked: hook must reference the video. Deferring — no cheap
programmatic check for semantic relevance; a bandit-driven quality
score is the durable answer.

## Wire

Called from ``base_visual_render._compose_frame`` right after
``hook_text`` is extracted, BEFORE ``compositor.compose()``. Returning
``ok=False`` causes ``_compose_frame`` to return ``""`` — story stays
at DRAFTED, operator sees it in review.

## Contract

Pure function: no side effects, no I/O, no LLM calls. Fast (~microseconds).
Same input always produces same output. Testable without mocks.
"""

from __future__ import annotations

from dataclasses import dataclass

# Reuse the writer's refusal prefixes to keep both gates in sync.
# Local re-import instead of a top-level ``from writing.video_content_writer``
# because rendering shouldn't depend on writing. If the writer's list drifts,
# we lose sync — that's a test failure (test file pins parity).
_LLM_REFUSAL_PREFIXES: tuple[str, ...] = (
    "i need to stop",
    "i need to flag",
    "i need to pause",  # 2026-07-17: 4 archived blueprints leaked past pre-gate
    "i need the",
    "i need more",
    "i don't have enough",
    "i don't have the",
    "i cannot",
    "i can't help",
    "i can't provide",
    "i can't write",
    "i can't access",  # 2026-07-17: movies blueprint 6e943894 leaked
    "i am unable",
    "i'm unable",
    "i'm sorry",
    "i apologize",
    "i'm afraid",
)


# Common verb shapes / tokens that indicate a hook has an action or
# statement. Presence of ANY one of these signals is enough — we're
# testing for absence of ALL of them (bare title).
#
# NOTE the exclusion of ``-s`` / ``'s``: those are noun-plural /
# possessive too often ("Baldurs Gate", "Sony's Latest Move"). We
# rely on ``-ed`` and ``-ing`` for verb-suffix signals + copula list
# below for the ambiguous cases.
_VERB_SUFFIXES: tuple[str, ...] = ("ed", "ing", "'ll", "'ve", "'d")
_VERB_TOKENS: frozenset[str] = frozenset(
    {
        # copulas — includes ``am`` which is uniquely first-person and
        # was omitted from an earlier draft. Emotional short hooks
        # ("I AM SO MAD ABOUT THIS") rely on ``am`` for their verb
        # signal.
        "am",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        # do
        "do",
        "does",
        "did",
        # have
        "has",
        "have",
        "had",
        # modals
        "can",
        "will",
        "should",
        "could",
        "would",
        "might",
        "may",
        "must",
        # imperatives common in hooks
        "watch",
        "look",
        "see",
        "try",
        "buy",
        "get",
        "use",
        "learn",
        "know",
        "imagine",
        "meet",
        "check",
        "wait",
        "stop",
        "listen",
        # question stems
        "why",
        "how",
        "what",
        "who",
        "when",
        "where",
        # short reactions
        "no",
        "yes",
        "wow",
        "omg",
    }
)


_MIN_HOOK_LENGTH = 15


@dataclass(frozen=True)
class QualityCheck:
    """Result of the pre-render gate. ``ok=True`` means safe to render."""

    ok: bool
    reason: str  # empty on ok; short machine-readable string on fail
    detail: str  # human-readable one-liner for logs / operator surface


def _is_llm_refusal(text: str) -> bool:
    """True if ``text`` starts with a known LLM refusal preamble.

    Case-insensitive prefix match. Kept local to avoid a cross-package
    dependency from rendering → writing.
    """
    if not text or not isinstance(text, str):
        return False
    lowered = text.strip().lower()
    return any(lowered.startswith(prefix) for prefix in _LLM_REFUSAL_PREFIXES)


def _has_verb_signal(text: str) -> bool:
    """True if the hook contains at least one word that looks like a verb.

    Cheap heuristic — no NLP dependency. Three signals:

      1. Any token ends in a verb-ish suffix (-s, -ed, -ing, 's, etc.)
      2. Any token matches a known copula / modal / imperative
      3. Any token starts with lowercase (implies non-title-case, which
         usually means a verb or preposition — genuine title-only hooks
         are ALL Title Case)

    A single positive signal → verb present. Returns True iff verb
    signal found. Bare-title hooks like "Grand Theft Auto V" score 0.
    """
    if not text:
        return False
    tokens = text.strip().split()
    if not tokens:
        return False

    for tok in tokens:
        # Strip trailing punctuation for suffix matching
        clean = tok.rstrip(".,!?:;\"'")
        if not clean:
            continue

        if clean[0].islower():
            # Lowercase-starting token — very likely a verb, preposition,
            # or article. Title-only hooks capitalise every word.
            return True

        lower = clean.lower()
        if lower in _VERB_TOKENS:
            return True

        # Suffix check — but exclude short tokens where the suffix
        # would eat the whole word (e.g. "As" ends in -s but is not
        # a verb; "AI" ends in -i, no match). Require ≥3 chars.
        if len(clean) >= 3:
            for suf in _VERB_SUFFIXES:
                if lower.endswith(suf):
                    return True

    return False


def check_pre_render_quality(hook: str, *, niche_id: str = "") -> QualityCheck:
    """Run all pre-render checks on the hook.

    Returns ``QualityCheck(ok=True, ...)`` if safe to render, or
    ``QualityCheck(ok=False, reason=..., detail=...)`` on any failure.
    Rules are ordered so the FIRST failure wins (short-circuit) — the
    operator sees the most severe reason, not a laundry list.

    ``niche_id`` is passed to enrich the log line only. It has no
    effect on the check outcome — quality is niche-agnostic.
    """
    if not hook or not isinstance(hook, str):
        return QualityCheck(
            ok=False,
            reason="empty_hook",
            detail="Hook is empty — cannot render a reel without a hook.",
        )

    hook_stripped = hook.strip()

    # Rule 1: LLM refusal preamble — most severe, most audience-facing
    # embarrassing failure mode. 5/7 of last-30d bad hooks were this.
    if _is_llm_refusal(hook_stripped):
        return QualityCheck(
            ok=False,
            reason="llm_refusal_preamble",
            detail=(
                f"Hook starts with an LLM refusal preamble: "
                f"{hook_stripped[:60]!r}. Writer's fallback path shipped "
                "an unwritable-story clarification as the hook."
            ),
        )

    # Rule 2: Minimum length
    if len(hook_stripped) < _MIN_HOOK_LENGTH:
        return QualityCheck(
            ok=False,
            reason="hook_too_short",
            detail=(
                f"Hook is {len(hook_stripped)} chars (min {_MIN_HOOK_LENGTH}). "
                f"Truncation bug or placeholder leftover: {hook_stripped!r}"
            ),
        )

    # Rule 3: Bare title — proper-noun-only, no verb signal
    if not _has_verb_signal(hook_stripped):
        return QualityCheck(
            ok=False,
            reason="hook_bare_title",
            detail=(
                f"Hook has no verb signal — ships as a title without a "
                f"take: {hook_stripped!r}. Writer produced this when the "
                "story had no context beyond the source video's title."
            ),
        )

    return QualityCheck(ok=True, reason="", detail="")
