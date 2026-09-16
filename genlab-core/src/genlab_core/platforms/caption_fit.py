"""Fit a caption to a platform character limit without losing the credit line.

Why this exists (2026-09-17)
----------------------------
Threads rejects any caption over 500 chars with
``"Param text must be at most 500 characters long"``. That hard failure dropped
movies on 09-15 and 09-11 and gaming on 09-14. Threads never enforced the cap --
``threads.py`` only mentioned it in a comment -- and it appends the hashtag block
AFTER the Layer 4 attribution check, so the overflow is assembled past the last
gate that looks at the caption.

The naive fix (``caption[:500]``) is worse than the bug. Captions are built
body-first with the credit line and hashtags at the END, so a tail-cut removes
exactly the thing the attribution defense stack exists to guarantee -- and it
does so AFTER Layer 4 has already approved the caption, making it invisible to
every gate. Instagram has shipped that exact defect as ``caption[:2200]``.

So truncation here is ORDERED BY WHAT WE ARE WILLING TO LOSE:

  1. body prose   -- trimmed first, at a word boundary
  2. hashtags     -- dropped whole-line only if trimming the body is not enough
  3. credit line  -- never dropped; it is the invariant the stack protects

If even the credit line alone exceeds the limit, it is returned hard-truncated
and the caller is told via ``CaptionFit.credit_preserved=False`` so the failure
is loud rather than a silently uncredited post (rule #12: one uncredited post is
a real audience-facing failure).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from genlab_core.platforms.caption_validation import validate_caption_has_attribution

logger = logging.getLogger(__name__)

_ELLIPSIS = "…"
# A line that is only hashtags (and whitespace) -- the droppable discovery block.
_HASHTAG_LINE = re.compile(r"^\s*(#\S+\s*)+$")


@dataclass(frozen=True)
class CaptionFit:
    text: str
    changed: bool
    credit_preserved: bool
    dropped_hashtags: bool


def _is_credit(line: str) -> bool:
    """True when the line carries a Layer 4 credit marker.

    Delegates to the Layer 4 validator rather than re-listing the markers, so a
    new marker cannot be recognised by the gate but severed by the truncator.
    """
    ok, _ = validate_caption_has_attribution(line, source_url=None)
    return ok


def _trim_words(text: str, budget: int) -> str:
    """Trim to <= budget chars at a word boundary, with an ellipsis."""
    if budget <= 0:
        return ""
    if len(text) <= budget:
        return text
    if budget <= len(_ELLIPSIS):
        return text[:budget]
    cut = text[: budget - len(_ELLIPSIS)]
    spaced = cut.rsplit(" ", 1)[0]
    # Only honour the word boundary if it keeps most of the budget; otherwise a
    # single long token would collapse the whole line to nothing.
    if len(spaced) >= budget // 2:
        cut = spaced
    return cut.rstrip() + _ELLIPSIS


def fit_caption(caption: str, limit: int, *, platform: str = "") -> CaptionFit:
    """Return ``caption`` fitted to ``limit`` characters, credit line intact."""
    if limit <= 0 or len(caption) <= limit:
        return CaptionFit(caption, False, True, False)

    lines = caption.split("\n")
    credit_idx = {i for i, ln in enumerate(lines) if ln.strip() and _is_credit(ln)}
    tag_idx = {
        i
        for i, ln in enumerate(lines)
        if i not in credit_idx and ln.strip() and _HASHTAG_LINE.match(ln)
    }

    def assemble(keep_tags: bool, body_budget: int | None) -> str:
        out: list[str] = []
        for i, ln in enumerate(lines):
            if i in tag_idx and not keep_tags:
                continue
            if i in credit_idx or i in tag_idx or body_budget is None:
                out.append(ln)
            else:
                out.append(_trim_words(ln, body_budget) if ln.strip() else ln)
        # Collapse blank runs left behind by dropped blocks.
        text = "\n".join(out)
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    fixed_len = sum(len(lines[i]) for i in credit_idx | tag_idx) + len(lines) - 1
    body_lines = [i for i in range(len(lines)) if i not in credit_idx | tag_idx]

    # Step 1: trim body prose, keeping hashtags and credit.
    if body_lines:
        budget = max(0, limit - fixed_len)
        per_line = budget // len(body_lines)
        candidate = assemble(keep_tags=True, body_budget=per_line)
        if len(candidate) <= limit and per_line > 0:
            return CaptionFit(candidate, True, True, False)

    # Step 2: drop the hashtag block wholesale, then re-trim the body.
    if tag_idx:
        fixed_no_tags = sum(len(lines[i]) for i in credit_idx) + len(lines) - 1
        budget = max(0, limit - fixed_no_tags)
        per_line = budget // len(body_lines) if body_lines else 0
        candidate = assemble(keep_tags=False, body_budget=per_line)
        if len(candidate) <= limit:
            logger.warning(
                "[caption-fit] %s caption over %d chars — dropped hashtag block "
                "to preserve credit line",
                platform or "platform",
                limit,
            )
            return CaptionFit(candidate, True, True, True)

    # Step 3: credit line only. Loud, because this means the credit line alone
    # is near the platform limit and the post carries no other content.
    credit_only = "\n".join(lines[i] for i in sorted(credit_idx)).strip()
    if credit_only and len(credit_only) <= limit:
        logger.warning(
            "[caption-fit] %s caption reduced to credit line alone (limit=%d)",
            platform or "platform",
            limit,
        )
        return CaptionFit(credit_only, True, True, bool(tag_idx))

    logger.error(
        "[caption-fit] %s: credit line does not fit in %d chars — publishing "
        "a hard-truncated caption; attribution may be incomplete",
        platform or "platform",
        limit,
    )
    return CaptionFit(caption[:limit], True, False, bool(tag_idx))
