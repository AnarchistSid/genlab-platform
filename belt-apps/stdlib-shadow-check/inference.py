"""Find Python files whose names shadow a standard-library module.

Python puts the running script's own directory first on `sys.path`. So a file
called `queue.py` sitting next to your script is imported instead of the
stdlib `queue` — and not by you. By something several layers down that has no
idea your file exists.

The failure is memorable because it never points at the culprit:

    File ".../urllib3/connectionpool.py", line 79, in ConnectionPool
        QueueCls = queue.LifoQueue
    AttributeError: module 'queue' has no attribute 'LifoQueue'

`urllib3` is not broken. `requests` is not broken. A scratch file named
`queue.py` two directories away is. That trace came from a real ops script;
the fix was renaming one file, and finding it took longer than writing it.

Severity here is about **transitive** risk, not whether the name is famous.
Shadowing `queue`, `types`, `select` or `io` breaks libraries that import them
indirectly, which is why it surfaces as somebody else's stack trace. Shadowing
something rarely imported by dependencies mostly breaks only your own code.

Give it a file list — `find . -name '*.py'` or `ls` output. Nothing is
executed and nothing is read; it matches names only.
"""
import logging
import os
import sys
from typing import List

from inferencesh import BaseApp, BaseAppSetup
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Imported transitively by very common third-party packages, so shadowing them
# breaks code that never mentions your file.
_HIGH_RISK = {
    "queue", "types", "select", "io", "abc", "enum", "copy", "json", "time",
    "socket", "ssl", "signal", "struct", "typing", "logging", "warnings",
    "collections", "functools", "itertools", "operator", "re", "os", "sys",
    "threading", "traceback", "base64", "hashlib", "random", "math", "string",
    "datetime", "subprocess", "platform", "codecs", "pickle", "email", "http",
}
# Common to name a file after, and still a real hazard.
_MEDIUM_RISK = {
    "test", "tests", "config", "parser", "token", "secrets", "uuid", "csv",
    "glob", "shutil", "tempfile", "textwrap", "pathlib", "argparse", "asyncio",
    "contextlib", "dataclasses", "inspect", "keyword", "numbers", "statistics",
    "unittest", "urllib", "xml", "zipfile", "gzip", "sqlite3", "hmac", "html",
}


class Finding(BaseModel):
    path: str = Field(description="The offending file, as supplied.")
    module: str = Field(description="The stdlib module it shadows.")
    severity: str = Field(description="high | medium | low")
    why: str = Field(description="What actually breaks.")
    fix: str = Field(description="The smallest safe rename.")


class AppSetup(BaseAppSetup):
    """Stateless — name matching only."""


class RunInput(BaseModel):
    paths: List[str] = Field(
        default_factory=list,
        description="File paths to check. Output of `find . -name '*.py'` or "
        "`ls` works directly; directories and non-.py entries are ignored.",
    )
    include_low_risk: bool = Field(
        True,
        description="Report stdlib names outside the high/medium lists too. "
        "Set false to see only the ones likely to break someone else's import.",
    )
    ignore_dirs: List[str] = Field(
        default_factory=lambda: ["site-packages", "node_modules", ".venv",
                                 "venv", ".git", "__pycache__", "dist", "build"],
        description="Path fragments to skip — vendored trees legitimately "
        "contain stdlib-named files and are not your problem.",
    )


class RunOutput(BaseModel):
    checked: int = Field(description="Python files considered after filtering.")
    skipped: int = Field(description="Entries ignored (non-.py or in an ignored dir).")
    finding_count: int = Field(description="Shadowing files found.")
    high: int = Field(description="High-severity count.")
    medium: int = Field(description="Medium-severity count.")
    low: int = Field(description="Low-severity count.")
    findings: List[Finding] = Field(description="Every shadowing file, worst first.")
    report: str = Field(description="Human-readable summary.")


def _stdlib_names() -> set:
    """Stdlib module names for the running interpreter.

    sys.stdlib_module_names exists from 3.10; fall back to a conservative
    union of the risk lists so the app still works on older runtimes rather
    than silently reporting nothing.
    """
    names = getattr(sys, "stdlib_module_names", None)
    if names:
        return {n for n in names if not n.startswith("_")}
    return _HIGH_RISK | _MEDIUM_RISK


def scan(paths: List[str], ignore_dirs: List[str], include_low: bool):
    stdlib = _stdlib_names()
    # A file inside a package is far safer than a loose script. `foo/types.py`
    # with `foo/__init__.py` beside it is only importable as `foo.types`, and
    # `foo/` is never itself on sys.path in normal use. A loose `types.py` in a
    # script directory IS on sys.path, first, and wins over the stdlib.
    #
    # Scanning a real 1,980-file codebase, treating both alike reported two
    # "high" findings that were both package-internal and effectively safe.
    # Reporting those at the same severity as the scratch file that actually
    # broke a production script would make the tool noise.
    package_dirs = {
        os.path.dirname(p.strip()) for p in paths
        if p and os.path.basename(p.strip()) == "__init__.py"
    }
    findings: List[Finding] = []
    checked = skipped = 0
    for raw in paths:
        p = (raw or "").strip()
        if not p:
            continue
        if not p.endswith(".py") or any(d and d in p for d in ignore_dirs):
            skipped += 1
            continue
        checked += 1
        mod = os.path.basename(p)[:-3]
        if mod == "__init__" or mod not in stdlib:
            continue
        in_package = os.path.dirname(p) in package_dirs
        if in_package:
            # Still worth knowing — it breaks if anyone runs the file directly
            # or puts its directory on sys.path — but it is not the footgun.
            sev = "low"
            why = (f"`{mod}` shadows a stdlib module, but this file sits inside "
                   "a package (there is an __init__.py beside it), so it is "
                   "imported as a submodule and its directory is not normally "
                   "on sys.path. Risk only if the file is run directly or its "
                   "directory is added to sys.path.")
        elif mod in _HIGH_RISK:
            sev = "high"
            why = (f"`{mod}` is imported transitively by common third-party "
                   "packages. Shadowing it breaks code that never mentions "
                   "your file, and the traceback will point at the library.")
        elif mod in _MEDIUM_RISK:
            sev = "medium"
            why = (f"`{mod}` is a stdlib module and a very common file name. "
                   "Shadowing it breaks any import of the real one from a "
                   "process whose sys.path includes this directory.")
        else:
            sev = "low"
            why = (f"`{mod}` shadows a stdlib module that is less commonly "
                   "imported by dependencies — most likely to affect only "
                   "your own code.")
        if sev == "low" and not include_low:
            continue
        findings.append(Finding(
            path=p, module=mod, severity=sev, why=why,
            fix=f"rename to something non-stdlib, e.g. `{mod}_utils.py` or "
                f"`my_{mod}.py`, or move it out of any directory that lands "
                "on sys.path",
        ))
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (order[f.severity], f.path))
    return findings, checked, skipped


class App(BaseApp):
    async def setup(self, config: AppSetup):
        logger.info("stdlib-shadow-check ready (%d stdlib names known)", len(_stdlib_names()))

    async def run(self, input_data: RunInput) -> RunOutput:
        findings, checked, skipped = scan(
            input_data.paths, input_data.ignore_dirs, input_data.include_low_risk
        )
        counts = {s: sum(1 for f in findings if f.severity == s)
                  for s in ("high", "medium", "low")}
        logger.info("checked %d file(s), %d finding(s)", checked, len(findings))

        if not checked:
            report = "No .py files to check after filtering."
        elif not findings:
            report = f"Clean — {checked} Python file(s), none shadow a stdlib module."
        else:
            lines = [f"{len(findings)} of {checked} Python file(s) shadow a stdlib "
                     f"module ({counts['high']} high, {counts['medium']} medium, "
                     f"{counts['low']} low).", ""]
            for f in findings:
                lines.append(f"  [{f.severity}] {f.path}  shadows `{f.module}`")
                lines.append(f"      {f.why}")
                lines.append(f"      fix: {f.fix}")
            report = "\n".join(lines)

        return RunOutput(
            checked=checked, skipped=skipped, finding_count=len(findings),
            high=counts["high"], medium=counts["medium"], low=counts["low"],
            findings=findings, report=report,
        )
