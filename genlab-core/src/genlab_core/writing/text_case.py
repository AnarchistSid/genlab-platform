"""genlab_core.writing.text_case — sentence-case enforcement.

Gen Lab ships **sentence case** across all 5 niches (hooks + captions): a
leading capital on every sentence with a casual tone underneath. All-lowercase
"Twitter shitpost" styling undercuts the niches' editorial credibility (R-50).

The LLM is *asked* for sentence case in the prompt, but until now nothing
enforced it — and two paths reliably emitted lowercase anyway: the
template-fallback hooks (R-51) and BlackboxBrief's own writing config, whose
all-lowercase examples were appended to the prompt with higher salience.

``to_sentence_case`` is a conservative post-pass: it only ever *adds* a
sentence-initial capital. It never lowercases an existing capital, so proper
nouns ("Claude", "LG") and intentional acronyms ("AI", "MKBHD") survive
untouched, and the function is idempotent — safe to run on already-cased text.
"""

from __future__ import annotations

# Characters that end a sentence (when followed by whitespace / end-of-string).
_SENTENCE_ENDERS = frozenset(".!?")


def to_sentence_case(text: str) -> str:
    """Capitalize the first letter of each sentence; leave everything else as-is.

    A new sentence begins at the start of the string and after a ``.``/``!``/``?``
    that is followed by whitespace (or end-of-string). The first alphabetic
    character of each sentence is upper-cased; non-letters between the boundary
    and that first letter (leading emoji, quotes, brackets, digits, spaces) are
    skipped, so e.g. ``"🔥 wait..."`` still capitalizes ``wait``.

    Additive-only: a letter is only ever promoted to uppercase, never lowered,
    so proper nouns / acronyms are preserved and the transform is idempotent.

    Args:
        text: Arbitrary caption / hook text.

    Returns:
        The same text with sentence-initial letters capitalized.
    """
    if not text:
        return text

    chars = list(text)
    n = len(chars)
    capitalize_next = True  # the start of the string begins a sentence

    for i, ch in enumerate(chars):
        if ch.isalpha():
            if capitalize_next:
                chars[i] = ch.upper()
                capitalize_next = False
        elif ch in _SENTENCE_ENDERS:
            # Only a real sentence boundary if whitespace (or EOS) follows —
            # this avoids mangling "example.com", "$3.5m", "U.S.A".
            nxt = chars[i + 1] if i + 1 < n else " "
            if nxt.isspace():
                capitalize_next = True
        # All other characters (digits, emoji, quotes, brackets, whitespace)
        # are pass-through and do not reset whether the next letter is
        # sentence-initial — so a capital still lands after them.

    return "".join(chars)
