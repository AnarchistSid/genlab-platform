"""Find the context keys your code READS that nothing ever WRITES.

A pipeline passes a dict from stage to stage. One stage does
`context.get("blueprints")`; the stage that fills it calls it something else, or
fills it five stages later, or was renamed. The read returns None, the stage
takes its empty-input branch, logs "nothing to do" at INFO, and reports success.

Nothing errors. No type catches it -- both sides are `dict[str, Any]`. No test
catches it, because the test builds the context by hand and puts the key in.
It is found when someone asks why a feature that shipped weeks ago has never
once produced output.

The two halves are in different files, and the only thing connecting them is a
string. This is the check for that string.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Set, Tuple

from inferencesh import BaseApp, BaseAppInput, BaseAppOutput, BaseAppSetup
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

#: Containers treated as a shared context unless the caller overrides.
DEFAULT_CONTAINERS = ["context", "ctx", "state", "payload", "job", "meta", "params"]

#: A key shorter than this is too generic to reason about ("id", "n").
MIN_KEY_LEN = 3


class SourceFile(BaseModel):
    name: str = Field(description="Filename or path, echoed in findings")
    content: str = Field(description="Full text of the source file")


class Site(BaseModel):
    file: str = Field(description="Source file")
    line: int = Field(description="1-indexed line number")
    snippet: str = Field(description="The line, trimmed")


class Finding(BaseModel):
    code: str = Field(description="Stable machine-readable identifier")
    severity: str = Field(description="critical, error, warn or info")
    key: str = Field(description="The context key concerned")
    container: str = Field(description="Which container it was read from")
    detail: str = Field(description="What was found")
    fix: str = Field(description="Concrete suggestion")
    reads: List[Site] = Field(default_factory=list, description="Where it is read")
    writes: List[Site] = Field(default_factory=list, description="Where it is written")


class AppSetup(BaseAppSetup):
    containers: List[str] = Field(
        default_factory=lambda: list(DEFAULT_CONTAINERS),
        description="Variable names treated as a shared context dict.",
    )


class AppInput(BaseAppInput):
    files: List[SourceFile] = Field(
        description=(
            "Every source file that participates in the context. A partial set "
            "produces false 'never written' findings -- include the producers."
        )
    )
    containers: Optional[List[str]] = Field(
        default=None,
        description=f"Override the container names. Default: {', '.join(DEFAULT_CONTAINERS)}",
    )
    report_dead_writes: bool = Field(
        default=True,
        description="Also report keys written but never read.",
    )
    near_miss_distance: int = Field(
        default=2,
        ge=0,
        le=4,
        description=(
            "Report a read with no writer as a NAME DRIFT when some written key "
            "is within this edit distance. 0 disables."
        ),
    )


class AppOutput(BaseAppOutput):
    findings: List[Finding] = Field(description="Most severe first")
    keys_read: int = Field(description="Distinct keys read")
    keys_written: int = Field(description="Distinct keys written")
    files_scanned: int = Field(description="How many files were examined")
    summary: str = Field(description="One line")


def _containers_re(names: List[str]) -> str:
    return "(?:" + "|".join(re.escape(n) for n in names) + ")"


def _read_patterns(c: str) -> List[Tuple[str, str]]:
    """(regex, container-group-name). Each must capture `key`."""
    return [
        # context.get("x") / context.get('x', default)
        (rf"\b(?P<c>{c})\s*\.\s*get\s*\(\s*[\"'](?P<key>[^\"']+)[\"']", "get"),
        # context["x"] NOT followed by an assignment  -> a read
        (rf"\b(?P<c>{c})\s*\[\s*[\"'](?P<key>[^\"']+)[\"']\s*\](?!\s*(?:=[^=]|\+=|\|=))", "index"),
        # context.pop("x")
        (rf"\b(?P<c>{c})\s*\.\s*pop\s*\(\s*[\"'](?P<key>[^\"']+)[\"']", "pop"),
    ]


def _write_patterns(c: str) -> List[Tuple[str, str]]:
    return [
        # context["x"] = ...   (but not ==)
        (rf"\b(?P<c>{c})\s*\[\s*[\"'](?P<key>[^\"']+)[\"']\s*\]\s*(?:=[^=]|\+=|\|=)", "assign"),
        # context.set("x", ...) / .setdefault("x", ...)
        (rf"\b(?P<c>{c})\s*\.\s*set(?:default)?\s*\(\s*[\"'](?P<key>[^\"']+)[\"']", "set"),
        # context.update({"x": ...})
        (rf"\b(?P<c>{c})\s*\.\s*update\s*\(\s*\{{\s*[\"'](?P<key>[^\"']+)[\"']", "update"),
        # JS/TS: context.x = ...
        (rf"\b(?P<c>{c})\s*\.\s*(?P<key>[A-Za-z_]\w*)\s*(?:=[^=]|\+=)", "attr"),
    ]


def _edit_distance(a: str, b: str, cap: int) -> int:
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


class App(BaseApp):
    async def setup(self, config: AppSetup):
        self.default_containers = list(config.containers or DEFAULT_CONTAINERS)
        # module logger, not self.logger: the engine attaches self.logger for
        # run(), and it does not exist yet during setup().
        logger.info("context-key-never-written ready; containers=%s", self.default_containers)

    async def run(self, input_data: AppInput) -> AppOutput:
        containers = input_data.containers or self.default_containers
        crx = _containers_re(containers)
        logger.info(
            "scanning %d file(s) for %d container name(s)", len(input_data.files), len(containers)
        )

        reads: Dict[Tuple[str, str], List[Site]] = {}
        writes: Dict[Tuple[str, str], List[Site]] = {}

        for f in input_data.files:
            for lineno, line in enumerate(f.content.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith("//"):
                    continue
                site = Site(file=f.name, line=lineno, snippet=stripped[:200])
                # WRITES FIRST. `context["x"] = context["x"] + 1` is both, and a
                # key that is written somewhere is not a missing producer.
                for pat, _kind in _write_patterns(crx):
                    for m in re.finditer(pat, line):
                        key = m.group("key")
                        if len(key) >= MIN_KEY_LEN:
                            writes.setdefault((m.group("c"), key), []).append(site)
                for pat, _kind in _read_patterns(crx):
                    for m in re.finditer(pat, line):
                        key = m.group("key")
                        if len(key) >= MIN_KEY_LEN:
                            reads.setdefault((m.group("c"), key), []).append(site)

        written_keys: Set[str] = {k for _, k in writes}
        findings: List[Finding] = []

        for (container, key), sites in sorted(reads.items()):
            if (container, key) in writes:
                continue
            # Written on ANOTHER container? Then it is a plumbing question, not
            # a missing producer, and saying "never written" would be wrong.
            elsewhere = [c for (c, k) in writes if k == key]
            if elsewhere:
                findings.append(
                    Finding(
                        code="written_on_another_container",
                        severity="warn",
                        key=key,
                        container=container,
                        detail=(
                            f"read from `{container}` but only ever written on "
                            f"{', '.join('`' + e + '`' for e in sorted(set(elsewhere)))}"
                        ),
                        fix=(
                            f"Confirm the same object reaches both sites, or write `{key}` "
                            f"on `{container}` where it is produced."
                        ),
                        reads=sites[:10],
                        writes=[s for (c, k), ss in writes.items() if k == key for s in ss][:10],
                    )
                )
                continue

            near = ""
            if input_data.near_miss_distance:
                cands = [
                    (w, _edit_distance(key, w, input_data.near_miss_distance))
                    for w in written_keys
                    if abs(len(w) - len(key)) <= input_data.near_miss_distance
                ]
                cands = [(w, d) for w, d in cands if 0 < d <= input_data.near_miss_distance]
                if cands:
                    near = min(cands, key=lambda x: x[1])[0]

            if near:
                findings.append(
                    Finding(
                        code="name_drift",
                        severity="critical",
                        key=key,
                        container=container,
                        detail=(
                            f"`{container}[\"{key}\"]` is read {len(sites)}x and never written, "
                            f"but `{near}` IS written. One of the two was renamed."
                        ),
                        fix=(
                            f"Rename the read to `{near}`, or the write to `{key}`. Until then "
                            f"the read returns the default on every run and the branch that "
                            f"depends on it never executes."
                        ),
                        reads=sites[:10],
                        writes=[s for (c, k), ss in writes.items() if k == near for s in ss][:10],
                    )
                )
            else:
                findings.append(
                    Finding(
                        code="read_never_written",
                        severity="critical",
                        key=key,
                        container=container,
                        detail=(
                            f"`{container}[\"{key}\"]` is read at {len(sites)} site(s) and "
                            f"written nowhere in the files given."
                        ),
                        fix=(
                            "Either the producer is missing, or it is in a file you did not "
                            "pass. If the producer runs LATER in the pipeline than this read, "
                            "the read is a silent no-op every run -- reorder, or read what the "
                            "earlier stage actually leaves behind."
                        ),
                        reads=sites[:10],
                    )
                )

        if input_data.report_dead_writes:
            read_keys = {k for _, k in reads}
            for (container, key), sites in sorted(writes.items()):
                if key in read_keys:
                    continue
                findings.append(
                    Finding(
                        code="written_never_read",
                        severity="info",
                        key=key,
                        container=container,
                        detail=f"`{container}[\"{key}\"]` is written and never read back.",
                        fix="Dead payload, or the consumer lives outside these files.",
                        writes=sites[:10],
                    )
                )

        rank = {"critical": 0, "error": 1, "warn": 2, "info": 3}
        findings.sort(key=lambda f: (rank.get(f.severity, 9), f.key))

        crit = sum(1 for f in findings if f.severity == "critical")
        summary = (
            f"{crit} key(s) read but never written across {len(input_data.files)} file(s); "
            f"{len(findings)} finding(s) total"
            if findings
            else f"every key read is written somewhere across {len(input_data.files)} file(s)"
        )
        logger.info(summary)

        return AppOutput(
            findings=findings,
            keys_read=len({k for _, k in reads}),
            keys_written=len(written_keys),
            files_scanned=len(input_data.files),
            summary=summary,
        )
