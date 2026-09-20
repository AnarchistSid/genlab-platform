"""Check that the JSON contract a prompt *declares* is the one the code *enforces*.

A prompt says "these six keys are REQUIRED". The parser checks five of them.
The model omits the sixth on roughly two runs in five, nothing retries, nothing
logs, and an empty string propagates into whatever consumes it. The feature
appears to work most days, which is the worst possible failure rate: often
enough to look shipped, rarely enough that nobody catches it.

That happened in a production content pipeline. A field was marked
``← REQUIRED`` in the prompt and was missing from the code's required-field set.
Five days of intermittent success turned out to be voluntary model compliance.
Three separate wrong diagnoses — a deploy, a source change, an upstream skip —
were investigated before anyone compared the two lists.

"REQUIRED" in a prompt is a comment. It instructs the model and binds nothing.
The only binding version lives in your checker, and nothing keeps the two in sync.

This app compares them:

  * parses the key list a prompt declares (several common formats)
  * flags a declared count that disagrees with the list ("ALL SIX KEYS" above
    seven bullets — a real instance, and the model reads that contradiction)
  * given ``enforced_fields``, reports declared-but-unenforced (the silent
    failure) and enforced-but-undeclared (a retry on every call)
  * given a sample ``response_json``, reports which declared keys are missing,
    which are present but empty, and which are extra

Empty-string values are reported separately from absent keys: a key present
with ``""`` passes a naive ``"k" in response`` check and is usually the bug.

CPU only, no network, no model call. Pure text analysis.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from inferencesh import BaseApp, BaseAppSetup
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# "  - field_name", "* field_name", "1. field_name", optionally followed by a
# marker like "← REQUIRED" or "(required)".
_BULLET = re.compile(
    r"^\s*(?:[-*•]|\d+[.)])\s*[\"'`]?([A-Za-z_][A-Za-z0-9_]*)[\"'`]?\s*(.*)$"
)
# "key": in a JSON-ish schema block.
_JSON_KEY = re.compile(r"[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']\s*:")
_REQUIRED_MARK = re.compile(r"(?:←|<-|->|→)?\s*REQUIRED|\(required\)", re.I)

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
# "ALL SIX KEYS ARE REQUIRED", "all 7 fields are required", "return 6 keys"
_COUNT_CLAIM = re.compile(
    r"\b(?:all|exactly|return|respond\s+with)\s+(\d+|"
    + "|".join(_NUMBER_WORDS)
    + r")\s+(?:keys?|fields?)\b",
    re.I,
)


class DeclaredField(BaseModel):
    name: str = Field(description="Field name as declared in the prompt.")
    marked_required: bool = Field(
        description="True if the line carried an explicit REQUIRED marker."
    )


class AppSetup(BaseAppSetup):
    """Stateless — pure text analysis, no model call."""


class RunInput(BaseModel):
    prompt: str = Field(
        description="The prompt text that declares the output contract. Paste the "
        "whole prompt or just the output-format block."
    )
    enforced_fields: List[str] = Field(
        default_factory=list,
        description="Field names your CODE actually checks for. This is the "
        "comparison that matters: a field the prompt calls REQUIRED and your "
        "checker omits will fail silently whenever the model drops it.",
    )
    response_json: Optional[str] = Field(
        None,
        description="Optional sample model response (a JSON object) to check "
        "against the declared contract.",
    )


class RunOutput(BaseModel):
    declared_fields: List[DeclaredField] = Field(
        default_factory=list, description="Output keys the prompt declares."
    )
    declared_count_claim: Optional[int] = Field(
        None, description='Count asserted in prose, e.g. "ALL SIX KEYS ARE REQUIRED".'
    )
    count_claim_matches: Optional[bool] = Field(
        None, description="False when the prose count disagrees with the key list."
    )

    declared_but_not_enforced: List[str] = Field(
        default_factory=list,
        description="THE SILENT FAILURE: the prompt calls these required, your code "
        "does not check them. When the model omits one, nothing retries or logs.",
    )
    enforced_but_not_declared: List[str] = Field(
        default_factory=list,
        description="THE LOUD FAILURE: your code demands these, the prompt never asks "
        "for them, so every call pays a retry.",
    )

    response_missing: List[str] = Field(
        default_factory=list, description="Declared keys absent from the sample response."
    )
    response_empty: List[str] = Field(
        default_factory=list,
        description="Declared keys PRESENT but empty. These pass a naive "
        "'key in response' check and are usually the actual bug.",
    )
    response_extra: List[str] = Field(
        default_factory=list, description="Keys in the response the prompt never declared."
    )

    contract_aligned: bool = Field(
        description="True when prompt and code agree and no count contradiction exists."
    )
    findings: List[str] = Field(
        default_factory=list, description="Human-readable findings, most severe first."
    )
    verdict: str = Field(description="One line.")


def _parse_declared(prompt: str) -> List[DeclaredField]:
    """Pull the declared key list out of a prompt.

    Bullets first — that is how most output-format blocks are written. If a
    prompt instead embeds a JSON skeleton, fall back to its keys.
    """
    seen: Dict[str, bool] = {}
    for line in prompt.splitlines():
        m = _BULLET.match(line)
        if not m:
            continue
        name, tail = m.group(1), m.group(2) or ""
        # A prose bullet is not a field. Field-ish lines are a bare identifier
        # optionally followed by a marker/description, not a sentence.
        if len(name) < 2 or " " in name:
            continue
        seen[name] = seen.get(name, False) or bool(_REQUIRED_MARK.search(tail))
    if not seen:
        for name in _JSON_KEY.findall(prompt):
            seen.setdefault(name, True)
    return [DeclaredField(name=k, marked_required=v) for k, v in seen.items()]


def _parse_count_claim(prompt: str) -> Optional[int]:
    m = _COUNT_CLAIM.search(prompt)
    if not m:
        return None
    tok = m.group(1).lower()
    return int(tok) if tok.isdigit() else _NUMBER_WORDS.get(tok)


class App(BaseApp):
    async def setup(self, setup: AppSetup):
        logger.info("llm-json-contract-check ready")

    async def run(self, input_data: RunInput) -> RunOutput:
        declared = _parse_declared(input_data.prompt)
        names = [d.name for d in declared]
        logger.info("declared %d field(s): %s", len(names), names)

        claim = _parse_count_claim(input_data.prompt)
        claim_ok = None if claim is None else (claim == len(names))

        enforced = [f.strip() for f in input_data.enforced_fields if f.strip()]
        not_enforced = sorted(set(names) - set(enforced)) if enforced else []
        not_declared = sorted(set(enforced) - set(names)) if enforced else []

        missing: List[str] = []
        empty: List[str] = []
        extra: List[str] = []
        if input_data.response_json:
            try:
                parsed = json.loads(input_data.response_json)
                if isinstance(parsed, dict):
                    for n in names:
                        if n not in parsed:
                            missing.append(n)
                        elif not str(parsed[n]).strip():
                            empty.append(n)
                    extra = sorted(set(parsed.keys()) - set(names))
                else:
                    logger.warning("response_json is not a JSON object; skipping")
            except json.JSONDecodeError as exc:
                logger.warning("response_json did not parse: %s", exc)

        findings: List[str] = []
        if not_enforced:
            findings.append(
                f"SILENT FAILURE RISK: {', '.join(not_enforced)} declared in the prompt "
                f"but not enforced in code. If the model omits one, nothing retries, "
                f"nothing logs, and an empty value propagates."
            )
        if empty:
            findings.append(
                f"PRESENT BUT EMPTY: {', '.join(empty)}. These pass a naive "
                f"'key in response' check — the check most parsers actually do."
            )
        if missing:
            findings.append(f"MISSING from the response: {', '.join(missing)}.")
        if not_declared:
            findings.append(
                f"RETRY ON EVERY CALL: {', '.join(not_declared)} enforced in code but "
                f"never requested in the prompt."
            )
        if claim_ok is False:
            findings.append(
                f"COUNT CONTRADICTION: the prompt says {claim} keys but lists "
                f"{len(names)}. The model reads that contradiction too."
            )
        if extra:
            findings.append(f"Undeclared keys in the response: {', '.join(extra)}.")

        aligned = not (not_enforced or not_declared or claim_ok is False)
        if not names:
            verdict = "No output keys parsed. Pass the prompt's output-format block."
        elif aligned and not findings:
            verdict = f"Contract aligned across {len(names)} declared field(s)."
        elif aligned:
            verdict = f"Prompt and code agree; {len(findings)} response-level finding(s)."
        else:
            verdict = f"Contract MISALIGNED — {len(findings)} finding(s), most severe first."

        return RunOutput(
            declared_fields=declared,
            declared_count_claim=claim,
            count_claim_matches=claim_ok,
            declared_but_not_enforced=not_enforced,
            enforced_but_not_declared=not_declared,
            response_missing=missing,
            response_empty=empty,
            response_extra=extra,
            contract_aligned=aligned,
            findings=findings,
            verdict=verdict,
        )
