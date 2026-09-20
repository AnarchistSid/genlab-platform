"""Find exception handlers that hide the cause of a failure.

A fail-open handler is often correct — a monitoring probe shouldn't crash the
pipeline it watches. What turns it into a bug is what it does on the way out:
swallowing the exception, or logging it somewhere nobody reads, so the failure
is invisible until someone goes looking months later.

Every rule here comes from a real production incident:

  * ``except Exception: return {}``  — a media probe returned an empty dict on
    any failure, so "file not found" and "codec unsupported" both surfaced as
    "no video stream found".
  * ``logger.debug(...)``            — a calibration writer failed for 17 days
    behind a debug-level handler while the system reported itself healthy.
  * ``logger.warning("failed: %s", exc)`` without ``exc_info`` — printed
    ``failed: 1``, the args of a KeyError and nothing else. No type, no
    traceback, no clue it was a dict/tuple shape mismatch.

The scan is AST-based, so it reads the code's structure rather than matching
text: a ``pass`` inside a nested ``if`` is still found, and a ``# noqa`` in a
comment doesn't change the verdict.
"""
import ast
import logging
from typing import List, Optional

from inferencesh import BaseApp, BaseAppSetup, File
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_DEBUG_LEVELS = {"debug"}
_LOUD_LEVELS = {"warning", "warn", "error", "exception", "critical", "fatal"}
_EMPTY_RETURNS = {"None", "{}", "[]", "()", "0", "0.0", "''", '""', "False", "set()"}

_SEVERITY_ORDER = {"high": 3, "medium": 2, "low": 1}


class Finding(BaseModel):
    line: int = Field(description="1-indexed line of the `except` clause.")
    rule: str = Field(description="Which check fired.")
    severity: str = Field(description="high | medium | low.")
    handler: str = Field(description="The exception type(s) caught, as written.")
    snippet: str = Field(description="The handler's source, trimmed.")
    why: str = Field(description="What this hides, in one sentence.")
    fix: str = Field(description="The smallest change that keeps the fail-open behaviour but restores the signal.")


class AppSetup(BaseAppSetup):
    """No setup state — the scan is pure and stateless."""


class RunInput(BaseModel):
    """Python source to scan, plus what counts as a finding."""

    source: str = Field(
        "",
        description="Python source code to scan. Leave empty and pass `file` instead.",
    )
    file: Optional[File] = Field(
        None,
        description="A .py file to scan. Used when `source` is empty.",
    )
    min_severity: str = Field(
        "low",
        description="Report findings at this severity or above: low | medium | high.",
    )
    ignore_bare_reraise: bool = Field(
        True,
        description="Skip handlers that re-raise — those propagate the error and hide nothing.",
    )


class RunOutput(BaseModel):
    ok: bool = Field(description="True when no findings at or above min_severity.")
    total_handlers: int = Field(description="How many except clauses were examined.")
    finding_count: int = Field(description="How many were reported.")
    high: int = Field(description="Count of high-severity findings.")
    medium: int = Field(description="Count of medium-severity findings.")
    low: int = Field(description="Count of low-severity findings.")
    findings: List[Finding] = Field(description="Every reported handler, in line order.")
    report: str = Field(description="Human-readable summary.")
    syntax_error: str = Field("", description="Set when the source could not be parsed.")


def _is_narrow(node: ast.ExceptHandler) -> bool:
    """True when the handler names specific exception types.

    A narrow catch is a deliberate contract — ``except (IndexError, KeyError):
    return None`` says "these are expected shapes and None is the documented
    answer". A broad ``except Exception`` around the same body is far more
    likely to be an accident that also swallows everything unforeseen.

    Calibration: scanning a 481-file codebase, treating both alike produced a
    91% hit rate, which is not a prioritisation — it is noise. Narrow catches
    are reported one severity lower so the broad ones stay visible.
    """
    if node.type is None:
        return False
    names = []
    t = node.type
    if isinstance(t, ast.Tuple):
        names = [ast.unparse(e) for e in t.elts]
    else:
        names = [ast.unparse(t)]
    broad = {"Exception", "BaseException"}
    return bool(names) and not any(n.split(".")[-1] in broad for n in names)


def _handler_name(node: ast.ExceptHandler) -> str:
    if node.type is None:
        return "bare except"
    try:
        return ast.unparse(node.type)
    except Exception:
        return "<unparseable>"


def _is_logging_call(node: ast.Call) -> Optional[str]:
    """Return the log level for a logging call, else None."""
    func = node.func
    if isinstance(func, ast.Attribute):
        name = func.attr.lower()
        if name in _DEBUG_LEVELS or name in _LOUD_LEVELS:
            return name
    return None


def _has_exc_info(node: ast.Call) -> bool:
    for kw in node.keywords:
        if kw.arg == "exc_info":
            return True
    # logger.exception() carries the traceback implicitly
    if isinstance(node.func, ast.Attribute) and node.func.attr.lower() == "exception":
        return True
    return False


def _walk_calls(body):
    for stmt in body:
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Call):
                yield sub


def _returns_empty(body) -> Optional[str]:
    for stmt in body:
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Return):
                if sub.value is None:
                    return "None"
                try:
                    text = ast.unparse(sub.value)
                except Exception:
                    continue
                if text in _EMPTY_RETURNS:
                    return text
    return None


def _reraises(body) -> bool:
    return any(isinstance(sub, ast.Raise) for stmt in body for sub in ast.walk(stmt))


def _body_is_only_pass(body) -> bool:
    real = [s for s in body if not isinstance(s, ast.Expr) or not isinstance(getattr(s, "value", None), ast.Constant)]
    return all(isinstance(s, ast.Pass) for s in real) and bool(real)


def _snippet(src_lines, node, limit=3) -> str:
    start = node.lineno - 1
    end = min(len(src_lines), getattr(node, "end_lineno", node.lineno))
    lines = [ln.rstrip() for ln in src_lines[start:end][:limit]]
    return "\n".join(lines).strip()


def scan_source(source: str, *, ignore_bare_reraise: bool = True):
    """Return (findings, total_handlers, syntax_error)."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [], 0, f"line {exc.lineno}: {exc.msg}"

    src_lines = source.splitlines()
    findings: List[Finding] = []
    total = 0

    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        total += 1
        body = node.body
        name = _handler_name(node)
        snip = _snippet(src_lines, node)

        if ignore_bare_reraise and _reraises(body):
            continue

        levels = [lvl for call in _walk_calls(body) if (lvl := _is_logging_call(call))]
        loud = [l for l in levels if l in _LOUD_LEVELS]
        quiet = [l for l in levels if l in _DEBUG_LEVELS]

        narrow = _is_narrow(node)

        def add(rule, severity, why, fix):
            # A narrow catch states an intended contract; downgrade one step so
            # the broad, likely-accidental handlers rank above it.
            sev = severity
            if narrow and severity == "high":
                sev = "medium"
            elif narrow and severity == "medium":
                sev = "low"
            if narrow:
                why = why + " (narrow catch — may be a deliberate contract)"
            findings.append(Finding(line=node.lineno, rule=rule, severity=sev,
                                    handler=name, snippet=snip, why=why, fix=fix))

        if _body_is_only_pass(body):
            add("silent_pass", "high",
                "The exception is discarded entirely — no log, no metric, no re-raise. "
                "A failure here is indistinguishable from success.",
                "Log at warning with exc_info=True before passing, even if the flow "
                "genuinely should continue.")
        elif not levels:
            empty = _returns_empty(body)
            if empty is not None:
                add("default_return_no_log", "high",
                    f"Returns {empty} on any exception with no logging, so every distinct "
                    "cause collapses into one indistinguishable empty result.",
                    f"Keep returning {empty}, but log the exception at warning with "
                    "exc_info=True first.")
            else:
                add("handler_without_logging", "medium",
                    "The handler does work but never records that an exception occurred.",
                    "Add a warning-level log so the path is visible in production.")
        elif quiet and not loud:
            add("debug_only_log", "high",
                "The only record of this failure is at debug level, which production "
                "log configs almost always drop — the handler is silent where it matters.",
                "Raise to logger.warning(..., exc_info=True). Keep debug for the "
                "genuinely-expected cases only.")

        for call in _walk_calls(body):
            lvl = _is_logging_call(call)
            if lvl in _LOUD_LEVELS and not _has_exc_info(call) and not narrow:
                add("log_without_exc_info", "low",
                    "Logs at a visible level but without exc_info, so the message carries "
                    "the exception's args and not its type or traceback — 'failed: 1' "
                    "tells you nothing about what 1 was.",
                    "Add exc_info=True, or use logger.exception().")
                break

        if name == "bare except":
            add("bare_except", "medium",
                "A bare `except:` also catches KeyboardInterrupt and SystemExit, so it "
                "can swallow a shutdown signal.",
                "Catch `Exception` instead, which excludes both.")

    findings.sort(key=lambda f: (f.line, -_SEVERITY_ORDER[f.severity]))
    return findings, total, ""


class App(BaseApp):
    async def setup(self, config: AppSetup):
        logger.info("silent-failure-scanner ready (stateless)")

    async def run(self, input_data: RunInput) -> RunOutput:
        source = input_data.source or ""
        if not source and input_data.file is not None:
            with open(input_data.file.path, "r", encoding="utf-8", errors="replace") as fh:
                source = fh.read()

        if not source.strip():
            return RunOutput(ok=True, total_handlers=0, finding_count=0, high=0,
                             medium=0, low=0, findings=[],
                             report="No source provided — nothing to scan.")

        logger.info("scanning %d bytes", len(source))
        findings, total, syntax_error = scan_source(
            source, ignore_bare_reraise=input_data.ignore_bare_reraise
        )
        if syntax_error:
            return RunOutput(ok=False, total_handlers=0, finding_count=0, high=0,
                             medium=0, low=0, findings=[], syntax_error=syntax_error,
                             report=f"Could not parse the source — {syntax_error}")

        floor = _SEVERITY_ORDER.get(input_data.min_severity.lower(), 1)
        kept = [f for f in findings if _SEVERITY_ORDER[f.severity] >= floor]
        counts = {s: sum(1 for f in kept if f.severity == s) for s in ("high", "medium", "low")}

        if not kept:
            report = f"Clean — examined {total} exception handler(s), none hide their cause."
        else:
            lines = [
                f"{len(kept)} of {total} exception handler(s) hide the cause "
                f"({counts['high']} high, {counts['medium']} medium, {counts['low']} low).",
                "",
            ]
            for f in kept:
                lines.append(f"  line {f.line}  [{f.severity}] {f.rule} — catching {f.handler}")
                lines.append(f"      {f.why}")
                lines.append(f"      fix: {f.fix}")
            report = "\n".join(lines)

        logger.info("found %d finding(s) across %d handler(s)", len(kept), total)
        return RunOutput(
            ok=not kept, total_handlers=total, finding_count=len(kept),
            high=counts["high"], medium=counts["medium"], low=counts["low"],
            findings=kept, report=report,
        )
