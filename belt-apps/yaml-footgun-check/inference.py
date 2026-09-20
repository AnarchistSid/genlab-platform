"""Find YAML values that parse successfully into something you did not write.

YAML's implicit typing means an unquoted scalar is not text — it is a guess.
Most of the time the guess is right. When it is wrong, nothing errors: the file
loads, the key exists, and the value is a different type or a different number
than the characters on the line. There is no traceback to follow back to the
quote you forgot.

The canonical example is the Norway Problem:

    country: NO        ->  False

Norway's ISO code is a boolean. So is Ontario's (`ON`), and so is any bare
`yes`/`no`/`on`/`off` in any casing. The rarer one is worse:

    start: 12:30       ->  750
    duration: 6:00     ->  360

YAML 1.1 reads colon-separated digits as sexagesimal — base 60. A time, a
duration, a MAC address or a version range silently becomes an integer that
looks like nothing you typed. And:

    version: 1.10      ->  1.1        (a float; the trailing zero is gone)
    mode: 0755         ->  493        (octal)
    released: 2026-01-05  ->  datetime.date, not a string

## The unifying rule

Rather than a hand-list of traps, the core check is a **round-trip**: parse each
unquoted scalar, render the result back to text, and compare it to the source
characters. If `str(parsed) != source`, then the file says one thing and means
another, and that is worth a human deciding on. This catches the whole family —
including combinations nobody has written a rule for — and it is why the report
always shows both sides.

Canonical `true`/`false` are excluded, because those are intentional and a check
that fires on every boolean gets muted on day one.

Three checks sit outside the round-trip because they cannot be caught by it:

- **duplicate keys**, where YAML silently keeps the last and discards the rest
  with no warning from `safe_load`
- **dates**, which round-trip as text while being `datetime.date` objects
- **float-like strings** such as `1e3`, which stay strings when you expected a
  number — the footgun running the other direction

## Verified, not folklore

Behaviour here was checked against PyYAML rather than copied from lists of YAML
gotchas, several of which are wrong. `y` and `n` are **not** booleans in PyYAML.
`08` is a **string**, not octal — only leading-zero runs of digits 0-7 like
`0755` convert. `1e3` is a **string**, not a float. Findings state the type you
actually get.
"""
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml
from inferencesh import BaseApp, BaseAppSetup
from pydantic import BaseModel, Field

logger = logging.getLogger("yaml-footgun-check")

# Bare bools that are almost always meant as text. `true`/`false` are excluded
# on purpose -- they are the intentional spelling.
_SURPRISE_BOOLS = {"yes", "no", "on", "off", "y", "n"}

# Two-letter codes that are real-world identifiers AND parse as booleans.
# Country codes, US/CA state and province codes, language codes.
_CODE_COLLISIONS = {"no": "Norway", "on": "Ontario", "y": "yes-abbreviation",
                    "n": "no-abbreviation"}

_NULLISH = {"", "~", "null"}

# Strict scientific-notation shape. The loose version of this test ("starts with
# a digit and contains an e") matched UUIDs and "49ers", which is how a linter
# earns its way into a mute list.
_SCI_NOTATION = re.compile(r"^[+-]?\d+(?:\.\d+)?[eE][+-]?\d+$")

# Trailing-zero loss only MATTERS when the text is an identifier. `version: 1.10`
# becoming 1.1 is a real defect; `weight: 0.10` becoming 0.1 is the same number.
# Keyed off the field name rather than the value, because the value cannot tell
# you what it is for -- and a check that fires on every decimal weight in a
# config gets muted wholesale, which costs more than the misses.
_IDENTIFIER_KEY_HINTS = ("version", "ver", "tag", "release", "revision",
                         "build", "sku", "code", "id", "number", "no")


def _is_identifier_field(path: str) -> bool:
    leaf = path.rsplit(".", 1)[-1].split("[")[0].lower()
    return any(h == leaf or h in leaf.split("_") for h in _IDENTIFIER_KEY_HINTS)


class Finding(BaseModel):
    code: str = Field(description="Stable machine-readable identifier")
    severity: str = Field(description="critical, error, warn or info")
    line: int = Field(description="1-indexed line in the source")
    key_path: str = Field(description="Dotted path to the value, where resolvable")
    source_text: str = Field(description="Characters as written in the file")
    parsed_as: str = Field(description="What the loader actually produced")
    parsed_type: str = Field(description="Python type of the parsed value")
    detail: str = Field(description="What happens and why it matters")
    fix: str = Field(description="Concrete suggestion")


class AppSetup(BaseAppSetup):
    report_dates: bool = Field(
        default=True,
        description="Report unquoted dates, which become datetime.date rather than str.",
    )
    report_all_float_text_loss: bool = Field(
        default=False,
        description="Report every float whose text changes, not just identifier-looking "
                    "fields. `weight: 0.10` -> 0.1 is harmless; turn this on only if you "
                    "want to see them all.",
    )
    report_nulls: bool = Field(
        default=False,
        description="Report bare `~`, `null` and empty values. Off by default: these are "
                    "usually deliberate.",
    )


class RunInput(BaseModel):
    content: str = Field(description="Full text of the YAML document")
    name: str = Field(default="document.yaml", description="Filename, echoed in output")


class RunOutput(BaseModel):
    name: str = Field(description="Document name")
    verdict: str = Field(description="clean, warn or broken")
    scalars_checked: int = Field(description="Unquoted scalar values examined")
    findings: List[Finding] = Field(description="Problems found, worst first")
    summary: str = Field(description="One-line human-readable result")


def _render(v: Any) -> str:
    """Render a parsed value the way a reader would say it aloud."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


class App(BaseApp):

    async def setup(self, config: AppSetup):
        self.report_dates = config.report_dates
        self.report_nulls = config.report_nulls
        self.report_all_floats = config.report_all_float_text_loss
        logger.info("yaml-footgun-check ready (dates=%s nulls=%s)",
                    self.report_dates, self.report_nulls)

    def _walk(self, node, path: str, findings: List[Finding], seen_counter: List[int]):
        """Walk the composed node tree. Marks give exact line numbers."""
        if isinstance(node, yaml.MappingNode):
            keys: Dict[str, int] = {}
            for k_node, v_node in node.value:
                key = str(getattr(k_node, "value", "?"))
                if key in keys:
                    findings.append(Finding(
                        code="duplicate_key", severity="critical",
                        line=k_node.start_mark.line + 1,
                        key_path=f"{path}.{key}" if path else key,
                        source_text=key, parsed_as="(last occurrence wins)",
                        parsed_type="n/a",
                        detail=(f"Key {key!r} appears more than once in the same mapping "
                                f"(first at line {keys[key]}). safe_load keeps the LAST "
                                f"and discards the earlier one without any warning."),
                        fix="Remove or rename the duplicate. Whichever value you thought "
                            "was in effect, only the last one is.",
                    ))
                else:
                    keys[key] = k_node.start_mark.line + 1
                self._walk(v_node, f"{path}.{key}" if path else key, findings, seen_counter)

        elif isinstance(node, yaml.SequenceNode):
            for i, child in enumerate(node.value):
                self._walk(child, f"{path}[{i}]", findings, seen_counter)

        elif isinstance(node, yaml.ScalarNode):
            # A quoted scalar is explicit -- the author already said "text".
            if node.style is not None:
                return
            self._check_scalar(node, path, findings, seen_counter)

    def _check_scalar(self, node, path: str, findings: List[Finding],
                      seen_counter: List[int]):
        raw = node.value
        low = raw.strip().lower()
        line = node.start_mark.line + 1
        seen_counter[0] += 1

        try:
            parsed = yaml.safe_load(raw) if raw.strip() else None
        except Exception:
            return

        ptype = type(parsed).__name__
        rendered = _render(parsed)

        def add(code, sev, detail, fix):
            findings.append(Finding(
                code=code, severity=sev, line=line, key_path=path or "(root)",
                source_text=raw, parsed_as=rendered, parsed_type=ptype,
                detail=detail, fix=fix,
            ))

        # --- nulls (opt-in; usually deliberate) --------------------------
        if low in _NULLISH or parsed is None:
            if self.report_nulls and low not in ("",):
                add("implicit_null", "info",
                    f"{raw!r} loads as null, not the string {raw!r}.",
                    'Quote it if you meant the literal text.')
            return

        # --- dates round-trip as text but are not text -------------------
        if ptype == "date" or ptype == "datetime":
            if self.report_dates:
                add("implicit_date", "warn",
                    f"{raw!r} becomes a {ptype} object, not a string. Anything doing "
                    f"string operations on it -- comparison, concatenation, regex -- "
                    f"will fail or behave unexpectedly.",
                    f'Quote it as "{raw}" if you want text.')
            return

        # --- bools ---------------------------------------------------------
        if isinstance(parsed, bool):
            if low in ("true", "false"):
                return                     # intentional spelling
            if low in _SURPRISE_BOOLS:
                collide = _CODE_COLLISIONS.get(low)
                extra = (f" {raw!r} is also a real identifier ({collide}), which is how "
                         f"the Norway Problem gets its name." if collide else "")
                add("implicit_bool", "error",
                    f"{raw!r} is not text -- it loads as the boolean {rendered}.{extra}",
                    f'Quote it as "{raw}" to keep it a string, or write '
                    f'{"true" if parsed else "false"} if you meant the boolean.')
            return

        # --- the round-trip: source says one thing, value means another ---
        if rendered != raw.strip():
            if ":" in raw and isinstance(parsed, int):
                add("sexagesimal", "critical",
                    f"{raw!r} is read as base-60 and becomes the integer {rendered}. "
                    f"YAML 1.1 treats colon-separated digits as sexagesimal, so times, "
                    f"durations, MAC addresses and version ranges silently turn into "
                    f"numbers unrelated to what you typed.",
                    f'Quote it as "{raw}".')
            elif isinstance(parsed, float):
                if _is_identifier_field(path):
                    add("float_precision_loss", "error",
                        f"{raw!r} is parsed as the float {rendered} -- the text does not "
                        f"survive, and the field name says this is an identifier. "
                        f"1.10 and 1.1 become the same value, so version comparisons "
                        f"and lookups silently collapse.",
                        f'Quote it as "{raw}".')
                elif self.report_all_floats:
                    add("float_text_loss", "info",
                        f"{raw!r} renders back as {rendered}. Harmless for a quantity -- "
                        f"the same number either way -- but worth quoting if this is "
                        f"really an identifier.",
                        f'Quote it as "{raw}" only if the trailing zero carries meaning.')
            elif isinstance(parsed, int) and raw.strip().lower().startswith("0x"):
                add("hex_int", "warn",
                    f"{raw!r} is read as hexadecimal and becomes {rendered}.",
                    f'Quote it as "{raw}" if it is an identifier rather than a number.')
            elif isinstance(parsed, int) and raw.strip().startswith("0"):
                add("octal_int", "error",
                    f"{raw!r} has a leading zero and is read as OCTAL, becoming "
                    f"{rendered}. File modes are the classic case: 0755 is 493.",
                    f'Quote it as "{raw}". Note that 08 and 09 are NOT octal -- they '
                    f'stay strings -- so this bug appears and disappears with the digits.')
            else:
                add("value_changed_by_parsing", "warn",
                    f"{raw!r} does not survive parsing: it becomes {rendered} "
                    f"({ptype}).",
                    f'Quote it as "{raw}" if you meant the literal text.')
            return

        # --- reverse footgun: looks numeric, stays a string ---------------
        if isinstance(parsed, str) and _SCI_NOTATION.match(raw.strip()):
            if True:
                add("float_like_string", "warn",
                    f"{raw!r} looks like scientific notation but stays a STRING. "
                    f"PyYAML needs a decimal point and a signed exponent (1.0e+3) "
                    f"before it reads as a float.",
                    "Write 1.0e+3 for a number, or leave it quoted for text -- but do "
                    "not assume arithmetic will work on it.")

    async def run(self, input_data: RunInput) -> RunOutput:
        content = input_data.content
        logger.info("checking %s (%d bytes)", input_data.name, len(content))
        findings: List[Finding] = []

        for i, line in enumerate(content.splitlines(), 1):
            stripped = line.split("#", 1)[0]
            if "\t" in stripped and stripped.strip():
                findings.append(Finding(
                    code="tab_indentation", severity="error", line=i,
                    key_path="(line)", source_text=line[:60], parsed_as="n/a",
                    parsed_type="n/a",
                    detail="Tab character outside a quoted string. YAML forbids tabs for "
                           "indentation; parsers reject them or misread the structure.",
                    fix="Replace tabs with spaces.",
                ))

        # A document that fails to parse is still worth reporting on when the
        # line-level scan above already found the cause. Raising "could not
        # parse" would discard the tab_indentation finding that explains it --
        # strictly less useful than naming the reason.
        node, parse_error = None, None
        try:
            node = yaml.compose(content)
        except yaml.YAMLError as exc:
            parse_error = exc

        if parse_error is not None:
            if not findings:
                raise RuntimeError(
                    f"{input_data.name}: could not parse as YAML -- {parse_error}")
            findings.append(Finding(
                code="parse_failed", severity="critical",
                line=getattr(getattr(parse_error, "problem_mark", None), "line", 0) + 1,
                key_path="(document)", source_text="", parsed_as="n/a", parsed_type="n/a",
                detail=(f"The document does not parse, so values below the failure point "
                        f"were never checked. Parser said: {parse_error}"),
                fix="Fix the finding(s) above and re-run for a complete report.",
            ))

        counter = [0]
        if node is not None:
            self._walk(node, "", findings, counter)

        rank = {"critical": 0, "error": 1, "warn": 2, "info": 3}
        findings.sort(key=lambda f: (rank.get(f.severity, 4), f.line))

        sev = {f.severity for f in findings}
        if "critical" in sev:
            verdict = "broken"
        elif sev & {"error", "warn"}:
            verdict = "warn"
        else:
            verdict = "clean"

        if verdict == "clean":
            summary = (f"clean — {counter[0]} unquoted scalar(s) checked, none change "
                       f"meaning when parsed")
        else:
            codes = ", ".join(sorted({f.code for f in findings})[:3])
            summary = (f"{verdict} — {len(findings)} finding(s) in {counter[0]} unquoted "
                       f"scalar(s): {codes}")

        logger.info("verdict=%s findings=%d scalars=%d", verdict, len(findings), counter[0])
        return RunOutput(name=input_data.name, verdict=verdict,
                         scalars_checked=counter[0], findings=findings, summary=summary)
