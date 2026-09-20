"""Find Python datetimes that carry no timezone, and the calls that produce them.

A naive datetime is a wall-clock reading with no record of which clock. It
prints identically to an aware one, compares fine against other naive values,
and is wrong in a way nothing surfaces until it meets a value from somewhere
else. Then you get either a `TypeError` at 3am or, worse, a silent comparison
that is off by your server's UTC offset.

The single worst offender:

    datetime.utcnow()   ->  datetime(2026, 9, 9, 2, 5, 45)   tzinfo=None

It reads UTC time and then throws away the fact that it is UTC. The name says
otherwise, which is why it survives review. It is also **deprecated since Python
3.12**, so it now emits a DeprecationWarning on top of being wrong.

    datetime.now()               -> local time, naive
    datetime.fromtimestamp(0)    -> local time, naive  (05:30 on an IST box)
    datetime.strptime(s, '%Y-%m-%d') -> naive
    date.today()                 -> local calendar day

Mixing the two raises immediately:

    TypeError: can't compare offset-naive and offset-aware datetimes

which is the *good* outcome. The bad outcome is a naive value that never meets
an aware one and is quietly five and a half hours off for the life of the
system.

## How it reads your code

Real AST parsing, not regex, so import style does not matter:

    import datetime;                  datetime.datetime.utcnow()
    from datetime import datetime;    datetime.utcnow()
    from datetime import datetime as dt;  dt.utcnow()

All three resolve to the same finding. Aliases are tracked per file. Nothing is
imported or executed.

Calls that already pass a timezone are left alone — `datetime.now(timezone.utc)`
and `datetime.fromtimestamp(x, tz=UTC)` are correct and produce no finding. The
goal is to report the places that *cannot* be right, not to lecture about every
mention of time.
"""
import ast
import logging
from typing import Dict, List, Optional, Set, Tuple

from inferencesh import BaseApp, BaseAppSetup
from pydantic import BaseModel, Field

logger = logging.getLogger("naive-datetime-check")

# Format directives that make strptime produce an aware datetime.
_TZ_DIRECTIVES = ("%z", "%Z", "%:z")


class SourceFile(BaseModel):
    name: str = Field(description="Filename or path, echoed in findings")
    content: str = Field(description="Full text of the Python source")


class Finding(BaseModel):
    code: str = Field(description="Stable machine-readable identifier")
    severity: str = Field(description="critical, error, warn or info")
    file: str = Field(description="Source file")
    line: int = Field(description="1-indexed line number")
    col: int = Field(description="0-indexed column")
    expression: str = Field(description="The call as written, resolved through aliases")
    detail: str = Field(description="What it produces and why that hurts")
    fix: str = Field(description="Concrete replacement")


class AppSetup(BaseAppSetup):
    report_constructors: bool = Field(
        default=True,
        description="Report datetime(...) literals built without tzinfo.",
    )
    report_date_today: bool = Field(
        default=True,
        description="Report date.today(), whose calendar boundary is the local one. "
                    "Turn off for code that is genuinely local-calendar by design.",
    )
    assume_utc_helper: str = Field(
        default="timezone.utc",
        description="Timezone expression used in suggested fixes, e.g. 'timezone.utc' "
                    "or 'ZoneInfo(\"Europe/London\")'.",
    )


class RunInput(BaseModel):
    sources: List[SourceFile] = Field(description="Python source files to scan")


class RunOutput(BaseModel):
    verdict: str = Field(description="clean, warn or broken")
    files_scanned: int = Field(description="Files successfully parsed")
    files_skipped: List[str] = Field(description="Files that did not parse, with reason")
    findings: List[Finding] = Field(description="Problems found, worst first")
    by_code: Dict[str, int] = Field(description="Finding code -> count")
    summary: str = Field(description="One-line human-readable result")


def _dotted(node) -> Optional[str]:
    """Render an attribute chain back to `a.b.c`, or None if it is not one."""
    parts: List[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


class _Scanner(ast.NodeVisitor):
    """Resolve datetime calls through whatever import style the file uses."""

    def __init__(self, filename: str, app: "App"):
        self.filename = filename
        self.app = app
        self.findings: List[Finding] = []
        # Names bound to the datetime MODULE, the datetime CLASS, the date CLASS.
        self.mod_aliases: Set[str] = set()
        self.dt_aliases: Set[str] = set()
        self.date_aliases: Set[str] = set()
        self.uses_pytz = False

    # -- imports -------------------------------------------------------
    def visit_Import(self, node: ast.Import):
        for a in node.names:
            if a.name == "datetime":
                self.mod_aliases.add(a.asname or "datetime")
            elif a.name.split(".")[0] == "pytz":
                self.uses_pytz = True
                self._pytz_line = node.lineno
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module == "datetime":
            for a in node.names:
                if a.name == "datetime":
                    self.dt_aliases.add(a.asname or "datetime")
                elif a.name == "date":
                    self.date_aliases.add(a.asname or "date")
        elif (node.module or "").split(".")[0] == "pytz":
            self.uses_pytz = True
            self._pytz_line = node.lineno
        self.generic_visit(node)

    # -- resolution ----------------------------------------------------
    def _resolve(self, dotted: str) -> Optional[str]:
        """Map a written call to a canonical `datetime.utcnow` style name."""
        parts = dotted.split(".")
        root = parts[0]
        if root in self.mod_aliases and len(parts) >= 3:
            if parts[1] == "datetime":
                return "datetime." + parts[2]
            if parts[1] == "date":
                return "date." + parts[2]
        if root in self.dt_aliases and len(parts) == 2:
            return "datetime." + parts[1]
        if root in self.date_aliases and len(parts) == 2:
            return "date." + parts[1]
        return None

    @staticmethod
    def _has_tz(call: ast.Call, positional_index: int) -> bool:
        if any(k.arg in ("tz", "tzinfo") or k.arg is None for k in call.keywords):
            return True
        return len(call.args) > positional_index

    def visit_Call(self, node: ast.Call):
        dotted = _dotted(node.func)
        if dotted:
            name = self._resolve(dotted)
            if name:
                self._check(name, dotted, node)
            elif (dotted in self.dt_aliases and self.app.report_constructors
                  and not self._has_tz(node, 7) and node.args):
                # datetime(2026, 1, 1) built with no tzinfo.
                self._add("naive_constructor", "warn", node, f"{dotted}(...)",
                          "A datetime built without tzinfo is naive. It will not compare "
                          "against aware values, and its meaning depends on who reads it.",
                          f"Pass tzinfo={self.app.tz_expr}.")
        self.generic_visit(node)

    # -- checks ---------------------------------------------------------
    def _check(self, name: str, written: str, node: ast.Call):
        tz = self.app.tz_expr
        if name == "datetime.utcnow":
            self._add("utcnow", "critical", node, f"{written}()",
                      "Returns UTC time with tzinfo=None -- the value is UTC but nothing "
                      "records that, so it compares as naive. Also deprecated since "
                      "Python 3.12 and emits a DeprecationWarning.",
                      f"datetime.now({tz})")
        elif name == "datetime.utcfromtimestamp":
            self._add("utcfromtimestamp", "critical", node, f"{written}(...)",
                      "Returns a naive datetime holding UTC. Same trap as utcnow(), and "
                      "also deprecated since Python 3.12.",
                      f"datetime.fromtimestamp(ts, {tz})")
        elif name in ("datetime.now", "datetime.today"):
            if name == "datetime.today" or not self._has_tz(node, 0):
                self._add("naive_now", "error", node, f"{written}()",
                          "Returns the local wall clock with tzinfo=None. On a server this "
                          "is whatever zone the box happens to be in, which is rarely what "
                          "the surrounding code assumes.",
                          f"datetime.now({tz})")
        elif name == "datetime.fromtimestamp":
            if not self._has_tz(node, 1):
                self._add("naive_fromtimestamp", "error", node, f"{written}(...)",
                          "Converts the epoch value into LOCAL time and drops the zone. "
                          "The same timestamp yields a different reading on every box.",
                          f"datetime.fromtimestamp(ts, {tz})")
        elif name == "datetime.strptime":
            fmt = None
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                if isinstance(node.args[1].value, str):
                    fmt = node.args[1].value
            if fmt is not None and not any(d in fmt for d in _TZ_DIRECTIVES):
                self._add("naive_strptime", "warn", node, f"{written}(..., {fmt!r})",
                          f"The format {fmt!r} has no %z or %Z, so the result is naive "
                          f"regardless of what the input string meant.",
                          f".replace(tzinfo={tz}) if the input is known to be that zone, "
                          f"or add %z to the format and the offset to the input.")
        elif name == "date.today" and self.app.report_date_today:
            self._add("local_date_today", "warn", node, f"{written}()",
                      "Uses the local calendar. Near midnight, or on a box in a different "
                      "zone from your users, this is the wrong day -- which is how daily "
                      "aggregations lose and duplicate rows at the boundary.",
                      f"datetime.now({tz}).date()")

    def _add(self, code, severity, node, expr, detail, fix):
        self.findings.append(Finding(
            code=code, severity=severity, file=self.filename,
            line=node.lineno, col=node.col_offset,
            expression=expr, detail=detail, fix=fix,
        ))


class App(BaseApp):

    async def setup(self, config: AppSetup):
        self.report_constructors = config.report_constructors
        self.report_date_today = config.report_date_today
        self.tz_expr = config.assume_utc_helper.strip() or "timezone.utc"
        logger.info("naive-datetime-check ready (tz_expr=%s constructors=%s today=%s)",
                    self.tz_expr, self.report_constructors, self.report_date_today)

    async def run(self, input_data: RunInput) -> RunOutput:
        if not input_data.sources:
            raise RuntimeError("No source files supplied.")

        logger.info("scanning %d file(s)", len(input_data.sources))
        findings: List[Finding] = []
        skipped: List[str] = []
        parsed = 0

        for src in input_data.sources:
            try:
                tree = ast.parse(src.content, filename=src.name)
            except SyntaxError as exc:
                skipped.append(f"{src.name}: line {exc.lineno}: {exc.msg}")
                logger.warning("%s did not parse: %s", src.name, exc.msg)
                continue
            parsed += 1
            sc = _Scanner(src.name, self)
            sc.visit(tree)
            findings.extend(sc.findings)
            if sc.uses_pytz:
                findings.append(Finding(
                    code="pytz_import", severity="info", file=src.name,
                    line=getattr(sc, "_pytz_line", 1), col=0, expression="import pytz",
                    detail="pytz predates zoneinfo and needs .localize() -- passing a pytz "
                           "zone straight to tzinfo= yields a historical offset like "
                           "LMT+05:53 rather than the modern one.",
                    fix="Use zoneinfo.ZoneInfo from the standard library (3.9+).",
                ))

        rank = {"critical": 0, "error": 1, "warn": 2, "info": 3}
        findings.sort(key=lambda f: (rank.get(f.severity, 4), f.file, f.line))

        by_code: Dict[str, int] = {}
        for f in findings:
            by_code[f.code] = by_code.get(f.code, 0) + 1
        by_code = dict(sorted(by_code.items(), key=lambda kv: -kv[1]))

        sev = {f.severity for f in findings}
        verdict = "broken" if "critical" in sev else (
            "warn" if sev & {"error", "warn"} else "clean")

        problems = sum(1 for f in findings if f.severity != "info")
        if verdict == "clean":
            summary = (f"clean — {parsed} file(s) scanned, no naive datetime calls"
                       + (f" ({len(findings)} note(s))" if findings else ""))
        else:
            top = ", ".join(list(by_code)[:3])
            summary = (f"{verdict} — {problems} finding(s) across {parsed} file(s): {top}")
        if skipped:
            summary += f"; {len(skipped)} file(s) skipped (unparseable)"

        logger.info("verdict=%s findings=%d parsed=%d skipped=%d",
                    verdict, len(findings), parsed, len(skipped))

        return RunOutput(verdict=verdict, files_scanned=parsed, files_skipped=skipped,
                         findings=findings, by_code=by_code, summary=summary)
