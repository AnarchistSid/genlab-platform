"""ffmpeg-filtergraph-lint — find the escaping bug before ffmpeg does.

## What it does

Takes a filtergraph string — the thing you pass to `-vf`, `-af` or
`-filter_complex` — and lexes it the way ffmpeg's own parser does, then
reports where the parse goes wrong and what ffmpeg will print when it does.

No ffmpeg binary, no network, no file access. Pure string analysis.

## The bug it exists for

ffmpeg's filtergraph syntax quotes an option value with `'`. Inside those
quotes there is **no escape for a quote character** — `\'` does not work, the
quote simply ends. So one apostrophe in a caption silently un-balances the
quoting for everything after it, and the next comma, which you intended as an
argument separator inside an expression, is read as a FILTER separator:

    drawtext=text='it\'s here':enable='between(t,16.320,17.5)'
                       ^ quote ends here, not escaped
    ffmpeg: No such filter: '16.320'

The reported "filter name" is a timestamp. Nothing in the message points at
the apostrophe several options earlier, which is why this costs an afternoon
the first time. This app names the apostrophe and predicts the exact error
string so you can match it against what you already saw.

To include a literal apostrophe you must leave quoting, escape it, and
re-enter: `'it'\''s here'`. For anything user-supplied, `textfile=` is the
better answer — it takes the text out of the parser's reach entirely, so
apostrophes, commas, colons, percent signs and backslashes all stop being
special at once instead of one escape rule at a time.

## What else it checks

  * unbalanced quotes across the whole graph
  * a filter name that is not a valid identifier (the symptom above)
  * commas and colons sitting unquoted inside an option value
  * `enable=` expressions left unquoted — the most common source of the above
  * `drawtext` with neither `text=` nor `textfile=`
  * `%` in drawtext text, which is a strftime expansion, not a literal
  * a literal newline in `text=` (some builds draw a glyph for it as well as
    breaking the line)
  * in complex graphs: labels referenced but never defined, defined twice, or
    defined and never consumed

## Scope

This lints the FILTERGRAPH STRING only. It does not audit the surrounding
ffmpeg command — option scope, codecs, containers, overwrite and network
flags are a different job.
"""

import logging
import re
from typing import List, Optional

from inferencesh import BaseApp, BaseAppSetup
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

ERROR = "error"
WARNING = "warning"

# A filter name ffmpeg will accept: letters, digits, underscore.
_VALID_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Options whose values are expressions — they contain commas by nature, so
# they are the ones that MUST be quoted.
_EXPR_OPTIONS = frozenset({"enable", "expr", "eval", "select", "x", "y", "alpha"})


class Finding(BaseModel):
    rule: str = Field(description="Short identifier for the check that fired")
    severity: str = Field(description="error or warning")
    message: str = Field(description="What is wrong, in one sentence")
    position: int = Field(description="0-based character offset in the input graph, or -1")
    filter_index: int = Field(description="0-based index of the filter involved, or -1")
    filter_name: str = Field(default="", description="Filter the finding belongs to")
    option: str = Field(default="", description="Option key involved, if any")
    evidence: str = Field(default="", description="The exact substring the finding is about")
    predicted_ffmpeg_error: str = Field(
        default="", description="What ffmpeg is expected to print, for matching against logs"
    )
    fix: str = Field(default="", description="The concrete change that resolves it")


class ParsedFilter(BaseModel):
    index: int = Field(description="Position in the graph")
    name: str = Field(description="Filter name as the parser sees it")
    inputs: List[str] = Field(default_factory=list, description="Input labels")
    outputs: List[str] = Field(default_factory=list, description="Output labels")
    options: List[str] = Field(default_factory=list, description="Option keys in order")
    valid_name: bool = Field(description="Whether the name is a legal filter identifier")


class AppSetup(BaseAppSetup):
    """No setup state. Declared because the platform expects the class."""


class RunInput(BaseModel):
    filtergraph: str = Field(
        description="The filtergraph string, exactly as passed to -vf / -af / -filter_complex. "
                    "Do not include the flag itself."
    )
    strict: bool = Field(
        default=False,
        description="Treat warnings as failures too, so ok=false on any finding.",
    )
    assume_complex: Optional[bool] = Field(
        default=None,
        description="Force complex-graph label checks on or off. Default: auto-detect "
                    "from the presence of ';' or '[label]' syntax.",
    )


class RunOutput(BaseModel):
    ok: bool = Field(description="True when no error-severity finding fired (see strict)")
    summary: str = Field(description="One-line verdict")
    error_count: int = Field(description="Number of error-severity findings")
    warning_count: int = Field(description="Number of warning-severity findings")
    findings: List[Finding] = Field(default_factory=list, description="Everything found, in order")
    filters: List[ParsedFilter] = Field(
        default_factory=list, description="The graph as the parser reads it"
    )
    quote_balanced: bool = Field(description="Whether quoting closes cleanly across the graph")


def _split_top_level(text: str, seps: str) -> List[tuple]:
    """Split on `seps` that are neither quoted nor escaped.

    This is the whole point of the app: it reproduces ffmpeg's own rule, in
    which a backslash escapes the next character OUTSIDE quotes, and inside
    quotes NOTHING is escapable — the region ends at the next quote, full
    stop. A splitter that treats \\' as an escaped quote will agree with the
    author's intention and disagree with ffmpeg, which is exactly the bug.
    """
    parts, buf, start = [], [], 0
    in_quote = False
    i = 0
    while i < len(text):
        c = text[i]
        if in_quote:
            if c == "'":
                in_quote = False
            buf.append(c)
        elif c == "\\":
            buf.append(c)
            if i + 1 < len(text):
                i += 1
                buf.append(text[i])
        elif c == "'":
            in_quote = True
            buf.append(c)
        elif c in seps:
            parts.append(("".join(buf), start, c))
            buf, start = [], i + 1
        else:
            buf.append(c)
        i += 1
    parts.append(("".join(buf), start, ""))
    return parts


def _quote_regions(text: str) -> List[tuple]:
    """(start, end) of every quoted region, by ffmpeg's rule. Unterminated
    regions run to the end of the string."""
    out, i, start = [], 0, None
    while i < len(text):
        c = text[i]
        if start is None:
            if c == "\\":
                i += 2
                continue
            if c == "'":
                start = i
        elif c == "'":
            out.append((start, i))
            start = None
        i += 1
    if start is not None:
        out.append((start, len(text)))
    return out


def _escaped_quote_inside_quotes(graph: str) -> List[int]:
    """Offsets of every `\\'` that sits INSIDE a quoted region.

    The author meant "a literal apostrophe". ffmpeg reads the backslash as an
    ordinary character and the quote as the end of the region. This is the
    single highest-value finding in the app.
    """
    hits = []
    for a, b in _quote_regions(graph):
        for m in re.finditer(r"\\'", graph[a:b + 1]):
            pos = a + m.start()
            if pos > a:  # not the opening quote itself
                hits.append(pos)
    return hits


def _strip_labels(chunk: str):
    """Pull [in] labels off the front and [out] labels off the back."""
    ins = re.findall(r"^\s*((?:\[[^\]]+\]\s*)+)", chunk)
    outs = re.findall(r"((?:\s*\[[^\]]+\])+)\s*$", chunk)
    in_labels = re.findall(r"\[([^\]]+)\]", ins[0]) if ins else []
    out_labels = re.findall(r"\[([^\]]+)\]", outs[0]) if outs else []
    body = chunk
    if ins:
        body = body[len(ins[0]):]
    if outs:
        body = body[: len(body) - len(outs[0])]
    return in_labels, body.strip(), out_labels


class App(BaseApp):
    async def setup(self, config: AppSetup):
        logger.info("[filtergraph-lint] ready — static analysis, no ffmpeg, no network")

    async def run(self, input_data: RunInput) -> RunOutput:
        graph = input_data.filtergraph
        logger.info("[filtergraph-lint] %d chars", len(graph))
        findings: List[Finding] = []
        filters: List[ParsedFilter] = []

        if not graph.strip():
            return RunOutput(
                ok=False, summary="empty filtergraph", error_count=1, warning_count=0,
                quote_balanced=True,
                findings=[Finding(rule="empty_graph", severity=ERROR, position=-1,
                                  filter_index=-1, message="No filtergraph supplied.")],
            )

        # ── quote parity ──────────────────────────────────────────────────
        regions = _quote_regions(graph)
        balanced = not (regions and regions[-1][1] == len(graph)
                        and graph[regions[-1][1] - 1: regions[-1][1]] != "'")
        unterminated = [r for r in regions if r[1] >= len(graph) and graph[-1:] != "'"]
        if unterminated:
            balanced = False
            findings.append(Finding(
                rule="unterminated_quote", severity=ERROR, position=unterminated[0][0],
                filter_index=-1,
                message="A quoted value is never closed; everything after it is swallowed.",
                evidence=graph[unterminated[0][0]:unterminated[0][0] + 40],
                predicted_ffmpeg_error="Unable to parse option value / No such filter",
                fix="Close the quote, or move the value into a textfile= file.",
            ))

        # ── the apostrophe ────────────────────────────────────────────────
        for pos in _escaped_quote_inside_quotes(graph):
            findings.append(Finding(
                rule="escaped_quote_inside_quotes", severity=ERROR, position=pos,
                filter_index=-1,
                message="\\' inside a quoted value does not escape anything — ffmpeg has no "
                        "escape for a quote inside quotes, so the quoted region ENDS here and "
                        "every separator after it is read as graph syntax.",
                evidence=graph[max(0, pos - 24):pos + 24],
                predicted_ffmpeg_error="No such filter: '<the next comma-separated token>'",
                fix="Leave quoting, escape, re-enter: 'it'\\''s here'. For user-supplied text "
                    "use textfile= instead, which removes every escaping rule at once.",
            ))

        # ── lex into filters ──────────────────────────────────────────────
        is_complex = (
            input_data.assume_complex
            if input_data.assume_complex is not None
            else (";" in graph or "[" in graph)
        )
        defined, consumed = {}, []
        idx = 0
        for chain, chain_off, _ in _split_top_level(graph, ";"):
            if not chain.strip():
                continue
            for piece, off, _ in _split_top_level(chain, ","):
                if not piece.strip():
                    continue
                pos = chain_off + off
                ins, body, outs = _strip_labels(piece)
                name = body.split("=", 1)[0].strip()
                args = body.split("=", 1)[1] if "=" in body else ""
                opts = []
                for a, _o, _s in _split_top_level(args, ":"):
                    if "=" in a:
                        opts.append(a.split("=", 1)[0].strip())
                valid = bool(_VALID_NAME.match(name))
                filters.append(ParsedFilter(index=idx, name=name, inputs=ins, outputs=outs,
                                            options=opts, valid_name=valid))
                if not valid:
                    findings.append(Finding(
                        rule="invalid_filter_name", severity=ERROR, position=pos,
                        filter_index=idx, filter_name=name,
                        message=f"{name!r} is not a legal filter name, so the parser is reading "
                                "a value as graph structure — the split happened earlier than "
                                "you intended.",
                        evidence=piece[:60],
                        predicted_ffmpeg_error=f"No such filter: '{name}'",
                        fix="Quote the value that contains the separator, or move the text into "
                            "a file with textfile=.",
                    ))

                # per-option checks
                for a, aoff, _s in _split_top_level(args, ":"):
                    if "=" not in a:
                        continue
                    key, val = a.split("=", 1)
                    key = key.strip()
                    quoted = val.startswith("'") and val.endswith("'") and len(val) > 1
                    if "," in val and not quoted:
                        findings.append(Finding(
                            rule="unquoted_comma_in_value", severity=ERROR, position=pos,
                            filter_index=idx, filter_name=name, option=key,
                            message=f"{key}= holds an unquoted comma, which ffmpeg reads as the "
                                    "end of this filter.",
                            evidence=a[:60],
                            predicted_ffmpeg_error="No such filter: "
                                                   f"'{val.split(',', 1)[1].split(':')[0]}'",
                            fix=f"Write it as {key}='{val}'.",
                        ))
                    if key in _EXPR_OPTIONS and "(" in val and "," in val and not quoted:
                        findings.append(Finding(
                            rule="expression_option_unquoted", severity=ERROR, position=pos,
                            filter_index=idx, filter_name=name, option=key,
                            message=f"{key}= is an expression containing a comma and is not "
                                    "quoted. Expression options always need quoting.",
                            evidence=a[:60],
                            predicted_ffmpeg_error="No such filter",
                            fix=f"{key}='{val}'",
                        ))
                    if name == "drawtext" and key == "text":
                        if "%" in val and "\\%" not in val:
                            findings.append(Finding(
                                rule="drawtext_percent_is_strftime", severity=WARNING,
                                position=pos, filter_index=idx, filter_name=name, option=key,
                                message="drawtext expands % as strftime; a literal percent sign "
                                        "will not survive.",
                                evidence=a[:60], fix="Escape it as \\% or use textfile=.",
                            ))
                        if "\n" in val or "\\n" in val:
                            findings.append(Finding(
                                rule="drawtext_newline_in_text", severity=WARNING,
                                position=pos, filter_index=idx, filter_name=name, option=key,
                                message="A newline in text= breaks the line on most builds AND "
                                        "draws a glyph for the character on some, leaving a tofu "
                                        "box at the break.",
                                evidence=a[:60],
                                fix="Emit one drawtext per line with its own y, which also lets "
                                    "each line carry its own box.",
                            ))
                if name == "drawtext" and not ({"text", "textfile"} & set(opts)):
                    findings.append(Finding(
                        rule="drawtext_without_text", severity=ERROR, position=pos,
                        filter_index=idx, filter_name=name,
                        message="drawtext has neither text= nor textfile=, so it draws nothing "
                                "and still exits 0.",
                        evidence=piece[:60],
                        predicted_ffmpeg_error="(none — it succeeds and draws nothing)",
                        fix="Supply text= or textfile=.",
                    ))

                for lbl in ins:
                    consumed.append(lbl)
                for lbl in outs:
                    if lbl in defined:
                        findings.append(Finding(
                            rule="duplicate_label", severity=ERROR, position=pos,
                            filter_index=idx, filter_name=name, evidence=f"[{lbl}]",
                            message=f"Label [{lbl}] is defined by more than one filter.",
                            predicted_ffmpeg_error=f"Duplicate output pad label '{lbl}'",
                            fix="Rename one of them.",
                        ))
                    defined[lbl] = idx
                idx += 1

        if is_complex:
            for lbl in dict.fromkeys(consumed):
                if lbl not in defined and not re.match(r"^\d+:[vas]", lbl):
                    findings.append(Finding(
                        rule="undefined_label", severity=ERROR, position=-1, filter_index=-1,
                        evidence=f"[{lbl}]",
                        message=f"[{lbl}] is consumed but never produced, and is not an input "
                                "stream specifier like [0:v].",
                        predicted_ffmpeg_error=f"Invalid file index / No such filter output '{lbl}'",
                        fix="Define it, or correct the name.",
                    ))
            for lbl, owner in defined.items():
                if lbl not in consumed:
                    findings.append(Finding(
                        rule="unconsumed_label", severity=WARNING, position=-1,
                        filter_index=owner, evidence=f"[{lbl}]",
                        message=f"[{lbl}] is produced and never used. If it is the graph's "
                                "output, map it; otherwise it is dead work.",
                        fix="-map '[%s]' or remove the branch." % lbl,
                    ))

        errors = [f for f in findings if f.severity == ERROR]
        warns = [f for f in findings if f.severity == WARNING]
        ok = not errors and (not warns or not input_data.strict)
        if ok:
            summary = f"clean — {len(filters)} filters parsed"
            if warns:
                summary += f", {len(warns)} warning(s)"
        else:
            first = (errors or warns)[0]
            summary = f"{len(errors)} error(s), {len(warns)} warning(s) — first: {first.rule}"
        logger.info("[filtergraph-lint] %s", summary)

        return RunOutput(
            ok=ok, summary=summary, error_count=len(errors), warning_count=len(warns),
            findings=findings, filters=filters, quote_balanced=balanced,
        )
