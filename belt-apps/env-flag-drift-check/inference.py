"""Compare the env vars your code READS against the ones your environment SETS.

A feature flag has two halves that live in different files and are changed by
different people at different times: the `os.environ.get("FEATURE_X")` in the
code, and the `FEATURE_X=1` in the deploy environment. Nothing checks that they
agree, and both halves fail silently when they do not.

Flip a flag whose reader was never deployed and the flag is a no-op. Rename a
reader without updating the environment and the flag reverts to its default
while still appearing set in `.env`. In both cases the deploy succeeds, the
service starts, the dashboards are green, and the feature is simply off.

Three findings here are worth the price of admission on their own:

**Name drift.** `FEATURE_X_ENABLED` sitting in `.env` while the code reads
`FEATURE_X_ENABLE` is not a missing variable you would notice — it is a
variable that exists, reads correctly in review, and is connected to nothing.
Both halves look right in isolation. Rated critical for that reason.

**Default divergence.** The same variable read in two places with two different
fallbacks. Unset, the system is in two states at once, and which one you observe
depends on which code path you happened to exercise.

**Truthiness divergence.** One site tests `== "1"`, another tests
`== "true"`. Setting the variable to `true` enables half the feature. This is
the one people rediscover from first principles every few years, usually at
2am, and it is invisible to every linter because both lines are correct.

## Coverage

Python (`os.environ.get`, `os.getenv`, `os.environ[...]`), JavaScript and
TypeScript (`process.env.X`, `process.env["X"]`), Go (`os.Getenv`,
`os.LookupEnv`), Ruby (`ENV[...]`, `ENV.fetch`), and shell (`$VAR`, `${VAR}`).

Nothing is executed. Source is matched textually, so a variable name built at
runtime by string concatenation is invisible here — that is a real blind spot
and it is stated rather than hidden.
"""
import logging
import re
from typing import Dict, List, Optional, Set, Tuple

from inferencesh import BaseApp, BaseAppSetup
from pydantic import BaseModel, Field

logger = logging.getLogger("env-flag-drift-check")

_MAX_NEAR_MISS_DISTANCE = 2
_MIN_NEAR_MISS_LEN = 5

# Literals that indicate a line is testing a variable for truthiness rather than
# using its value. Divergence across these is the 2am bug.
_TRUTHY_VOCAB = {"1", "0", "true", "false", "yes", "no", "on", "off", "y", "n", "t", "f"}

# Reads WITH an optional default. Group 1 = var, group 2 = default expression.
_READ_WITH_DEFAULT = [
    re.compile(r"""os\.environ\.get\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']\s*(?:,\s*([^)]*))?\)"""),
    re.compile(r"""os\.getenv\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']\s*(?:,\s*([^)]*))?\)"""),
    re.compile(r"""ENV\.fetch\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']\s*(?:,\s*([^)]*))?\)"""),
]
# Reads with NO default -- these raise or return empty when unset.
_READ_REQUIRED = [
    re.compile(r"""os\.environ\[\s*["']([A-Za-z_][A-Za-z0-9_]*)["']\s*\]"""),
    re.compile(r"""ENV\[\s*["']([A-Za-z_][A-Za-z0-9_]*)["']\s*\]"""),
    re.compile(r"""os\.Getenv\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']\s*\)"""),
    re.compile(r"""os\.LookupEnv\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']\s*\)"""),
    re.compile(r"""process\.env\[\s*["']([A-Za-z_][A-Za-z0-9_]*)["']\s*\]"""),
    re.compile(r"""process\.env\.([A-Za-z_][A-Za-z0-9_]*)\b"""),
]
# Shell. Restricted to SCREAMING_CASE of decent length: `$i` and `$PATH` would
# otherwise flood the report with noise that is never a feature flag.
_READ_SHELL = re.compile(r"""\$\{?([A-Z][A-Z0-9_]{3,})\}?""")

_ENV_LINE = re.compile(r"""^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$""")
_STRING_LIT = re.compile(r"""["']([^"']*)["']""")

# Vars every environment provides. Reporting these as "set but never read" or
# "read but never set" is noise, not signal.
_AMBIENT = {
    "PATH", "HOME", "USER", "SHELL", "PWD", "LANG", "TERM", "TMPDIR", "TZ",
    "HOSTNAME", "LOGNAME", "EDITOR", "SHLVL", "OLDPWD", "DISPLAY", "PS1",
    "PYTHONPATH", "VIRTUAL_ENV", "NODE_ENV", "CI", "DEBUG",
}


def _levenshtein(a: str, b: str, cap: int) -> int:
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


class SourceFile(BaseModel):
    name: str = Field(description="Filename or path, echoed in findings")
    content: str = Field(description="Full text of the source file")


class EnvFile(BaseModel):
    name: str = Field(description="Filename, e.g. .env or deploy/prod.env")
    content: str = Field(description="Full text, KEY=value lines")


class Site(BaseModel):
    file: str = Field(description="Source file")
    line: int = Field(description="1-indexed line number")
    default: Optional[str] = Field(None, description="Default expression, if the read has one")


class Finding(BaseModel):
    code: str = Field(description="Stable machine-readable identifier")
    severity: str = Field(description="critical, error, warn or info")
    variable: str = Field(description="Environment variable concerned")
    detail: str = Field(description="What was found")
    fix: str = Field(description="Concrete suggestion")
    sites: List[Site] = Field(description="Where in the source this appears")


class AppSetup(BaseAppSetup):
    ignore_ambient: bool = Field(
        default=True,
        description="Skip PATH/HOME/CI and similar vars every environment provides.",
    )
    report_set_never_read: bool = Field(
        default=True,
        description="Report vars set in env files that no supplied source reads. Turn off "
                    "when passing a partial source set, where absence proves nothing.",
    )
    prefix_filter: str = Field(
        default="",
        description="Only consider variables starting with this prefix, e.g. 'MYAPP_'. "
                    "Sharpens the report on a codebase with many third-party vars.",
    )


class RunInput(BaseModel):
    sources: List[SourceFile] = Field(description="Source files that read environment variables")
    env_files: List[EnvFile] = Field(description="Env files that set them (.env, deploy env, etc.)")


class RunOutput(BaseModel):
    verdict: str = Field(description="aligned, drifted or broken")
    vars_read: int = Field(description="Distinct variables read in source")
    vars_set: int = Field(description="Distinct variables set in env files")
    connected: int = Field(description="Variables both read and set")
    findings: List[Finding] = Field(description="Problems found, worst first")
    read_never_set: List[str] = Field(description="Read in code, set nowhere")
    set_never_read: List[str] = Field(description="Set in env, read nowhere")
    summary: str = Field(description="One-line human-readable result")


class App(BaseApp):

    async def setup(self, config: AppSetup):
        self.ignore_ambient = config.ignore_ambient
        self.report_unread = config.report_set_never_read
        self.prefix = config.prefix_filter.strip()
        logger.info("env-flag-drift-check ready (ignore_ambient=%s prefix=%r)",
                    self.ignore_ambient, self.prefix)

    def _keep(self, var: str) -> bool:
        if self.prefix and not var.startswith(self.prefix):
            return False
        if self.ignore_ambient and var in _AMBIENT:
            return False
        return True

    def _scan(self, sources: List[SourceFile]):
        """Return reads[var] -> [(site, truthy_literals)]."""
        reads: Dict[str, List[Tuple[Site, Set[str]]]] = {}
        shellish = (".sh", ".bash", ".zsh", ".env", "Dockerfile", "Makefile")

        for src in sources:
            is_shell = src.name.endswith(shellish) or "/bin/" in src.content[:80]
            for lineno, line in enumerate(src.content.splitlines(), 1):
                stripped = line.lstrip()
                if stripped.startswith("#") or stripped.startswith("//"):
                    continue
                hits: List[Tuple[str, Optional[str], int]] = []
                for rx in _READ_WITH_DEFAULT:
                    for m in rx.finditer(line):
                        d = (m.group(2) or "").strip() or None
                        hits.append((m.group(1), d, m.end()))
                for rx in _READ_REQUIRED:
                    for m in rx.finditer(line):
                        hits.append((m.group(1), None, m.end()))
                if is_shell:
                    for m in _READ_SHELL.finditer(line):
                        hits.append((m.group(1), None, m.end()))

                for var, default, endpos in hits:
                    if not self._keep(var):
                        continue
                    # Truthiness literals appearing AFTER the read on this line.
                    lits = {
                        s.strip().lower()
                        for s in _STRING_LIT.findall(line[endpos:])
                        if s.strip().lower() in _TRUTHY_VOCAB
                    }
                    reads.setdefault(var, []).append(
                        (Site(file=src.name, line=lineno, default=default), lits)
                    )
        return reads

    async def run(self, input_data: RunInput) -> RunOutput:
        if not input_data.sources:
            raise RuntimeError("Need at least one source file -- drift is a mismatch "
                               "between code and environment, so both sides are required.")

        logger.info("scanning %d source file(s), %d env file(s)",
                    len(input_data.sources), len(input_data.env_files))

        reads = self._scan(input_data.sources)
        sets: Dict[str, List[str]] = {}
        for ef in input_data.env_files:
            for line in ef.content.splitlines():
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                m = _ENV_LINE.match(s)
                if m and self._keep(m.group(1)):
                    sets.setdefault(m.group(1), []).append(ef.name)

        read_names, set_names = set(reads), set(sets)
        never_set = sorted(read_names - set_names)
        never_read = sorted(set_names - read_names)
        connected = sorted(read_names & set_names)
        findings: List[Finding] = []

        # --- name drift: the headline check ------------------------------
        drifted: Set[str] = set()
        for s in never_read:
            if len(s) < _MIN_NEAR_MISS_LEN:
                continue
            for r in never_set:
                if len(r) < _MIN_NEAR_MISS_LEN or s == r:
                    continue
                dist = _levenshtein(s, r, _MAX_NEAR_MISS_DISTANCE)
                if dist <= _MAX_NEAR_MISS_DISTANCE:
                    drifted.add(s)
                    drifted.add(r)
                    findings.append(Finding(
                        code="name_drift", severity="critical", variable=r,
                        detail=(f"The environment sets {s!r} but the code reads {r!r} "
                                f"(edit distance {dist}). Neither half is wrong on its own, "
                                f"so this passes review: the variable is set, the reader "
                                f"exists, and they are not connected. The flag is a no-op."),
                        fix=f"Pick one spelling. Rename {s!r} in the env file to {r!r}, "
                            f"or update the reader.",
                        sites=[st for st, _ in reads[r]],
                    ))
                    break

        # --- read but never set -------------------------------------------
        for var in never_set:
            if var in drifted:
                continue
            sites = reads[var]
            no_default = [st for st, _ in sites if st.default is None]
            if no_default:
                findings.append(Finding(
                    code="read_required_never_set", severity="error", variable=var,
                    detail=(f"{var!r} is read without a default at {len(no_default)} site(s) "
                            f"and set in no supplied env file. Depending on the accessor "
                            f"this raises at import time or silently yields empty."),
                    fix=f"Set {var} in the environment, or give the read a default.",
                    sites=no_default,
                ))
            else:
                defaults = sorted({st.default for st, _ in sites if st.default})
                findings.append(Finding(
                    code="read_never_set", severity="warn", variable=var,
                    detail=(f"{var!r} is read at {len(sites)} site(s) but set in no supplied "
                            f"env file, so the default applies everywhere "
                            f"(default: {', '.join(defaults) if defaults else 'none'})."),
                    fix="Fine if the default is what you want in every environment. If this "
                        "is a flag you believe is on somewhere, it is not.",
                    sites=[st for st, _ in sites],
                ))

        # --- set but never read -------------------------------------------
        if self.report_unread:
            for var in never_read:
                if var in drifted:
                    continue
                findings.append(Finding(
                    code="set_never_read", severity="warn", variable=var,
                    detail=(f"{var!r} is set in {', '.join(sets[var])} but no supplied source "
                            f"reads it. Either dead configuration, or the reader lives in a "
                            f"file that was not passed in."),
                    fix="Remove it, or confirm the reader is outside this source set.",
                    sites=[],
                ))

        # --- divergence across call sites ---------------------------------
        for var in sorted(read_names):
            sites = reads[var]
            defaults = {st.default for st, _ in sites if st.default is not None}
            if len(defaults) > 1:
                findings.append(Finding(
                    code="default_divergence", severity="error", variable=var,
                    detail=(f"{var!r} is read with {len(defaults)} different defaults: "
                            f"{', '.join(sorted(defaults))}. When unset, the system is in "
                            f"two states at once and which you observe depends on the code "
                            f"path taken."),
                    fix="Read it once into a module-level constant and import that.",
                    sites=[st for st, _ in sites],
                ))

            # Only flag when two sites both clearly test truthiness AND share
            # no accepted literal -- overlapping vocabularies are compatible.
            lit_sites = [(st, lits) for st, lits in sites if lits]
            if len(lit_sites) >= 2:
                groups: List[Set[str]] = []
                for _, lits in lit_sites:
                    if not any(lits & g for g in groups):
                        groups.append(set(lits))
                    else:
                        for g in groups:
                            if lits & g:
                                g |= lits
                if len(groups) > 1:
                    shown = " vs ".join(
                        "/".join(sorted(g)) for g in groups[:3])
                    findings.append(Finding(
                        code="truthiness_divergence", severity="critical", variable=var,
                        detail=(f"{var!r} is tested against incompatible literals across "
                                f"sites: {shown}. A value accepted by one site is ignored "
                                f"by the other, so setting the flag half-enables it."),
                        fix="Parse once with a shared helper that accepts the whole "
                            "vocabulary, and call that everywhere.",
                        sites=[st for st, _ in lit_sites],
                    ))

        rank = {"critical": 0, "error": 1, "warn": 2, "info": 3}
        findings.sort(key=lambda f: (rank.get(f.severity, 4), f.variable))

        sev = {f.severity for f in findings}
        if "critical" in sev:
            verdict = "broken"
        elif sev & {"error", "warn"}:
            verdict = "drifted"
        else:
            verdict = "aligned"

        if verdict == "aligned":
            summary = (f"aligned — {len(read_names)} variable(s) read, {len(connected)} "
                       f"connected to an env file, no drift found")
        else:
            codes = ", ".join(sorted({f.code for f in findings})[:3])
            summary = (f"{verdict} — {len(findings)} finding(s); {len(read_names)} read, "
                       f"{len(set_names)} set, {len(connected)} connected: {codes}")

        logger.info("verdict=%s findings=%d read=%d set=%d connected=%d",
                    verdict, len(findings), len(read_names), len(set_names), len(connected))

        return RunOutput(
            verdict=verdict, vars_read=len(read_names), vars_set=len(set_names),
            connected=len(connected), findings=findings,
            read_never_set=never_set, set_never_read=never_read, summary=summary,
        )
