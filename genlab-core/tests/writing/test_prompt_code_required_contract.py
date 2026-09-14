"""Pin: every field the PROMPT calls REQUIRED is enforced in CODE, and vice versa.

NARR-04 (2026-09-13). `narration_script` was marked
``← REQUIRED for narration-enabled niches`` in the writer's prompt and was
absent from ``_REQUIRED_LLM_FIELDS``. Nothing enforced it. When the model
omitted the field, ``missing`` stayed empty, the retry never fired, no log
recorded the omission, ``""`` propagated to GenerateAudio, and TTS read the
caption aloud instead of a script. Intermittent success across five days was
voluntary model compliance, not a working feature.

The defect is not the missing element — it is that a prompt string and a
frozenset expressed the same contract with nothing keeping them in sync.
"REQUIRED" in a prompt is a comment; only the checker binds. So this pins the
INVARIANT (the two sides agree), not a snapshot of either side. Adding a field
to the prompt without enforcing it fails here, and so does the reverse.

Class: shared-contract / N-implementers / silent divergence.
"""

from __future__ import annotations

import re
from pathlib import Path

from genlab_core.writing.video_content_writer import _REQUIRED_LLM_FIELDS

_SRC = Path(__import__("genlab_core.writing.video_content_writer", fromlist=["__file__"]).__file__)

# The prompt declares its contract two ways, and BOTH count:
#   a header — "ALL SIX KEYS ARE REQUIRED and must have non-empty string values"
#   a key list — "  - hook\n", "  - instagram_caption  ← REQUIRED, never empty..."
# The "← REQUIRED" marker is emphasis on the two historically-omitted fields
# (instagram_caption, threads_content) and on the two CONDITIONAL fields
# (narration_script, reveal). Parsing only the marker would miss four of the
# six base keys, so the key list is the declaration of record.
_PROMPT_KEY = re.compile(r'"\s{2,}-\s*([a-z_]+)\b')

# Fields the prompt marks REQUIRED that are KNOWN to be unenforced in code.
# This is a ratchet, not an excuse: the list documents an open gap and the
# tests below still fail the moment a NEW one appears.
#
#   reveal — Layer 3 S4b question_reveal variant (video_content_writer.py:1034).
#     Marked "← REQUIRED for question_reveal" and enforced nowhere: the exact
#     shape that made narration_script fail silently for five days. Found by
#     this pin on its first run. Filed for its own fix; NOT fixed here, because
#     enforcing it needs the variant condition threaded to the call site the
#     way narration_target_seconds now is, and that is a separate change.
_KNOWN_UNENFORCED: frozenset[str] = frozenset({"reveal"})

# The per-call conditional set at the call site, e.g.
#   extra_required_fields=(frozenset({"narration_script"}) if ... else frozenset())
_CONDITIONAL = re.compile(r"extra_required_fields\s*=\s*\(?\s*frozenset\(\{([^}]*)\}")


def _source() -> str:
    return _SRC.read_text(encoding="utf-8")


def _prompt_required_fields(src: str) -> set[str]:
    """Fields the prompt declares as output keys — the contract the model sees."""
    return set(_PROMPT_KEY.findall(src))


def _conditionally_enforced(src: str) -> set[str]:
    out: set[str] = set()
    for blob in _CONDITIONAL.findall(src):
        out |= set(re.findall(r'"([a-z_]+)"', blob))
    return out


def test_prompt_marks_at_least_the_known_required_fields() -> None:
    """Guard the guard: if the prompt format changes, this test must not
    silently start comparing two empty sets and pass."""
    found = _prompt_required_fields(_source())
    assert found, (
        "No output-key lines parsed out of the writer prompt. The prompt "
        "format changed and this contract pin has gone blind — fix the regex "
        "rather than deleting the test."
    )
    assert "instagram_caption" in found, (
        f"Expected instagram_caption among prompt-required fields, got {sorted(found)}"
    )


def test_every_prompt_required_field_is_enforced_in_code() -> None:
    """The direction that broke: prompt says REQUIRED, code does not check."""
    src = _source()
    prompt_required = _prompt_required_fields(src)
    enforced = set(_REQUIRED_LLM_FIELDS) | _conditionally_enforced(src)

    unenforced = sorted(prompt_required - enforced - _KNOWN_UNENFORCED)
    assert not unenforced, (
        f"These fields are marked REQUIRED in the writer prompt but are not in "
        f"_REQUIRED_LLM_FIELDS nor in any per-call extra_required_fields set: "
        f"{unenforced}. The model may omit them and nothing will retry, log, or "
        f"fail — which is exactly how narration_script produced five days of "
        f"silent empty scripts. Enforce it, or stop calling it REQUIRED."
    )


def test_every_code_required_field_is_declared_in_the_prompt() -> None:
    """The reverse direction: code demands a field the prompt never asked for.

    That shape costs a retry and a WARNING on every single story, because the
    model was never told to produce it.
    """
    src = _source()
    prompt_required = _prompt_required_fields(src)

    undeclared = sorted(set(_REQUIRED_LLM_FIELDS) - prompt_required)
    assert not undeclared, (
        f"These fields are enforced in _REQUIRED_LLM_FIELDS but never marked "
        f"REQUIRED in the prompt: {undeclared}. The model is not being asked "
        f"for them, so every story pays a retry and a WARNING."
    )


def test_narration_script_is_conditionally_enforced_not_globally() -> None:
    """The specific fix, and the trap in the obvious version of it.

    `narration_script` must be enforced PER CALL, gated on the same condition
    the prompt branches on (`narration_target_seconds is not None`). Putting it
    in the module-level frozenset would make the four non-narration niches
    retry twice and WARN on every story, forever — trading a silent failure on
    one niche for a loud one on four.
    """
    src = _source()
    assert "narration_script" not in _REQUIRED_LLM_FIELDS, (
        "narration_script must NOT be in the module-level _REQUIRED_LLM_FIELDS: "
        "it is required only when the caller passes narration_target_seconds. "
        "Globally requiring it makes every non-narration niche retry and warn "
        "on every story."
    )
    assert "narration_script" in _conditionally_enforced(src), (
        "narration_script is marked REQUIRED in the prompt but no per-call "
        "extra_required_fields set enforces it."
    )
    assert "narration_target_seconds is not None" in src, (
        "The conditional enforcement must branch on narration_target_seconds — "
        "the same variable the prompt branches on — so the two cannot drift."
    )
