"""Detect spliced/transposed text in LLM output.

Measured on belt 2026-09-21. The `anthropic/claude-haiku-4-5` app, called
through `belt app run` in its default wait-for-completion mode, returned text
with fragments spliced INTO words:

    "ev itaporates from oceans"            evaporates, split
    "transforms water fromeans, lakes"     from oceans, glued and transposed
    "droplets accum theseulate"            accumulate, split by "these"
    "A dying no her matchblewoman meets"   noblewoman, split by "her match"

Rate, same prompt, ten runs each:

    belt CLI, haiku, wait-for-completion (the path in use)   7/10 corrupt
    MCP client (HTTP API), haiku                             0/3  corrupt
    belt CLI, sonnet, wait-for-completion                    0/10 corrupt
    belt CLI --no-wait + `task get`, haiku                   0/8  corrupt

So it is the CLI's stream reassembly, raced by a fast model: Sonnet is slow
enough never to trigger it. `fallback.py` now submits with `--no-wait` and
reads the settled task, which is the fix. This module is the gate that sits
behind it, because a writer that can emit "ev itaporates" must read its own
output before anything downstream trusts it.

## Why a vendored wordlist

The signature is a token that is not a word but whose head or tail IS one
("from" + "aporates"). That needs a dictionary. `/usr/share/dict/words`
exists on the Mac and **does not exist on the prod VPS** — no wordlist
package is installed — so a system-dictionary detector would have been live
in dev and silently dead in production, which is rule #17's exact shape. The
list ships beside this module instead.
"""

from __future__ import annotations

import functools
import gzip
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_WORDS_PATH = Path(__file__).with_name("data") / "english_words.txt.gz"

_SUFFIXES = ("s", "es", "ed", "d", "ing", "ly", "er", "est", "'s", "'t", "n't", "ion", "ions")

#: Function words take no inflections. Without this, "theest" (from
#: "the|est known") strips its "-est" to "the" and passes as a real word --
#: the suffix rule would swallow exactly the corruption it exists to surface.
_NO_INFLECTION: frozenset[str] = frozenset(
    {"the", "and", "for", "was", "her", "his", "its", "but", "not", "are",
     "you", "all", "can", "has", "had", "who", "our", "out", "one", "two",
     "she", "him", "them", "this", "that", "with", "from", "into", "than"}
)

#: Real words the vendored list lacks. Each splits into two real words and so
#: looks exactly like a glued pair. Written down rather than suppressed by a
#: broader rule -- the broader rule ("veto anything that splits into two real
#: words") was tried and it hid "thisends" and "theseprecipitation" too.
DICT_GAPS: frozenset[str] = frozenset(
    {"groundwater", "vapour", "vapours", "snowmelt", "watershed", "watersheds",
     "bioluminescence", "runoff", "meltwater", "rainfall", "snowfall",
     "streamflow", "sublimation", "anglerfish", "seawater", "freshwater",
     "worldwide", "crunchyroll", "framedrift", "anime", "otaku", "isekai",
     "shonen", "seinen", "mecha", "waifu", "sakuga", "simulcast", "seiyuu"}
)


class LLMOutputCorrupt(RuntimeError):
    """Generated text carries splice artifacts and must not be used."""


@functools.lru_cache(maxsize=1)
def _words() -> frozenset[str]:
    try:
        with gzip.open(_WORDS_PATH, "rt", encoding="utf-8") as fh:
            return frozenset(w.strip() for w in fh if w.strip())
    except OSError as exc:
        # LOUD. A missing list means the gate cannot work, and a gate that
        # quietly passes everything is worse than no gate -- callers would
        # believe output had been checked.
        logger.error(
            "output-integrity wordlist unreadable at %s (%s) — the corruption "
            "gate is INOPERATIVE and every generation will be reported clean",
            _WORDS_PATH, exc,
        )
        return frozenset()


def wordlist_available() -> bool:
    """False when the gate cannot actually check anything."""
    return bool(_words())


def _known(tok: str) -> bool:
    w = _words()
    if not w:
        return True
    t = tok.lower().strip("'")
    if t in w:
        return True
    for suf in _SUFFIXES:
        stem = t[: -len(suf)]
        if not t.endswith(suf) or stem in _NO_INFLECTION:
            continue
        if len(stem) >= 2 and (stem in w or (stem + "e") in w or stem.rstrip("i") + "y" in w):
            return True
    if t.endswith("ies") and (t[:-3] + "y") in w:
        return True
    return bool(len(t) > 4 and t[-1] == t[-2] and t[:-1] in w)


def artifacts(text: str, allow: frozenset[str] = frozenset()) -> list[str]:
    """Tokens that look like a word with a foreign fragment spliced in."""
    found: list[str] = []
    for tok in re.findall(r"[A-Za-z][A-Za-z']{3,}", text or ""):
        low = tok.lower()
        # Proper nouns are skipped: a model may legitimately coin a name, and
        # every corrupt sample still leaves lowercase artifacts to catch.
        if tok[0].isupper() or low in allow or low in DICT_GAPS or _known(tok):
            continue
        for i in range(3, len(low) - 2):
            head, tail = low[:i], low[i:]
            if len(tail) >= 2 and _known(head) != _known(tail):
                found.append(tok)
                break
    return found


def is_corrupt(text: str, allow: frozenset[str] = frozenset()) -> bool:
    return bool(artifacts(text, allow))


def assert_clean(text: str, *, where: str = "", allow: frozenset[str] = frozenset()) -> str:
    """Return ``text``, or raise ``LLMOutputCorrupt`` naming the artifacts."""
    found = artifacts(text, allow)
    if found:
        raise LLMOutputCorrupt(
            f"llm_output_corrupt{':' + where if where else ''} — splice artifacts "
            f"{found[:6]} in {len(text)} chars of generated text"
        )
    return text
