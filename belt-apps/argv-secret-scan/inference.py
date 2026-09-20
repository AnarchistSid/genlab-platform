"""Find secrets passed as command-line arguments, where every logger records them.

A shell script exports a token by putting it on a command line. It works. It is
also now in the process table for any user running `ps`, in `~/.bash_history`, in
journald if the command ran under systemd, and — if `sudo` was involved — written
verbatim to the auth log, because sudo records the full argv of everything it
runs.

That happened in a production pipeline. A deploy verifier ran:

    sudo -u app env DATABASE_URL="$DB_URL" ./venv/bin/python - <<'PY'

The `env` was deliberate and correct: `sudo -u` strips the parent environment, so
without it the script silently fell through to a default DSN and passed its check
against a database that did not exist. The fix solved that problem using the one
mechanism that logs its arguments. The database password ended up in journald,
in a monitoring alert body, and in a screenshot of the dashboard.

Nothing was misconfigured. No scanner fired. The line reads as careful code.

## What this finds

* `env NAME=value cmd` and `sudo ... env NAME=value cmd` — the highest-severity
  shape, because sudo logs argv to the auth log
* `--password=`, `--token=`, `--api-key=`, `-p<value>` style flags
* inline `NAME=value cmd` prefixes on a command
* `curl -u user:pass`, `mysql -p<pass>` short auth flags
* URLs with inline credentials (`scheme://user:pass@host`) anywhere on a line

It reports the *shape*, and by default redacts the value it found — the tool
should not become a second place the secret is written down.

## What it deliberately does not flag

`export NAME=value` and `NAME=value` on their own line are assignments, not argv.
Reading from an env file, a secrets manager, or stdin is the fix, so those are
never findings.
"""

import re
from typing import Any, Dict, List, Optional

from inferencesh import BaseApp, BaseAppSetup
from pydantic import BaseModel, Field

# Name fragments that make a variable or flag secret-bearing.
_SECRET_WORDS = (
    "password", "passwd", "secret", "token", "api_key", "apikey", "access_key",
    "private_key", "credential", "auth", "session", "cookie", "dsn",
    "database_url", "conn_str", "connection_string", "bearer", "signature",
)


def _looks_secret(name: str) -> bool:
    n = name.lower()
    return any(w in n for w in _SECRET_WORDS) or n.endswith("_key")


class Finding(BaseModel):
    line_number: int = Field(description="1-indexed line the finding is on.")
    line: str = Field(description="The line, with the secret value redacted.")
    kind: str = Field(description="Which shape matched, e.g. sudo_env_assignment.")
    name: str = Field(description="The variable or flag carrying the secret.")
    severity: str = Field(description="critical | high | medium")
    why: str = Field(description="Which logs record it.")
    fix: str = Field(description="The safe rewrite.")


class AppSetup(BaseAppSetup):
    """Stateless — pure text analysis, no network."""


class RunInput(BaseModel):
    script: str = Field(
        description="Shell script, Dockerfile, CI config, systemd unit, or a single "
        "command line to scan."
    )
    redact: bool = Field(
        True,
        description="Redact the secret value in the output. Leave on unless you are "
        "scanning a file with placeholder values — this tool should not become a "
        "second place the secret is recorded.",
    )
    extra_secret_names: List[str] = Field(
        default_factory=list,
        description="Additional variable-name fragments to treat as secret-bearing, "
        "e.g. your internal prefix.",
    )


class RunOutput(BaseModel):
    findings: List[Finding] = Field(default_factory=list)
    critical_count: int = Field(description="Findings whose value reaches a sudo/auth log.")
    total_count: int = Field(description="All findings.")
    clean: bool = Field(description="True when nothing was found.")
    verdict: str = Field(description="One line.")


def _redact(line: str, value: str, on: bool) -> str:
    if not on or not value or len(value) < 3:
        return line
    return line.replace(value, "<REDACTED>")


class App(BaseApp):
    async def setup(self, setup: AppSetup):
        pass

    async def run(self, input_data: RunInput) -> RunOutput:
        extra = tuple(s.lower() for s in input_data.extra_secret_names if s.strip())

        def secret(name: str) -> bool:
            return _looks_secret(name) or any(e in name.lower() for e in extra)

        findings: List[Finding] = []
        for i, raw in enumerate(input_data.script.splitlines(), start=1):
            line = raw.rstrip()
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            has_sudo = re.search(r"\bsudo\b", line) is not None

            # 1. `env NAME=value` / `NAME=value cmd` used as a command prefix.
            for m in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)=(\S+)", line):
                name, value = m.group(1), m.group(2)
                if not secret(name):
                    continue
                before = line[: m.start()]
                # An assignment on its own line, or `export X=…`, is not argv.
                if re.match(r"^\s*(export\s+)?$", before) and not has_sudo:
                    continue
                is_env_prefix = re.search(r"\benv\s+$", before) or re.search(
                    r"\b(env|sudo)\b", before
                )
                if not is_env_prefix:
                    continue
                findings.append(
                    Finding(
                        line_number=i,
                        line=_redact(line, value, input_data.redact),
                        kind="sudo_env_assignment" if has_sudo else "env_assignment",
                        name=name,
                        severity="critical" if has_sudo else "high",
                        why=(
                            "sudo writes the full argv of every command it runs to the "
                            "auth log, and journald records it for anything under systemd. "
                            "It is also visible in `ps` to any user on the box."
                            if has_sudo
                            else "Visible in the process table to any user running `ps`, "
                            "and recorded by journald for anything under systemd."
                        ),
                        fix=(
                            "Have the child read the value itself: "
                            "`sudo -u user bash -c 'set -a; . /path/.env; set +a; exec cmd'` "
                            "or a systemd `EnvironmentFile=`. Never as an argv element."
                        ),
                    )
                )

            # 2. secret-bearing long flags: --password=…, --api-key …
            for m in re.finditer(r"(--[A-Za-z0-9][A-Za-z0-9-]*)[= ]+(\S+)", line):
                flag, value = m.group(1), m.group(2)
                if not secret(flag.lstrip("-").replace("-", "_")):
                    continue
                if value.startswith(("$", "-", '"$', "'$")) and "=" not in value:
                    pass  # still argv even when a variable — the expansion is logged
                findings.append(
                    Finding(
                        line_number=i,
                        line=_redact(line, value, input_data.redact),
                        kind="secret_flag",
                        name=flag,
                        severity="critical" if has_sudo else "high",
                        why="Flag values are argv: process table, shell history, and "
                        "journald all record them." + (" sudo logs argv too." if has_sudo else ""),
                        fix="Pass via stdin, an env file the child reads, or a "
                        "credentials file with 0600 permissions.",
                    )
                )

            # 3. short auth flags that take user:pass or a bare secret
            for m in re.finditer(r"(?:^|\s)-([upP])\s*([^\s]+)", line):
                flag, value = m.group(1), m.group(2)
                if flag == "u" and ":" not in value:
                    continue  # `-u user` alone is not a secret
                if value.startswith("$"):
                    continue
                findings.append(
                    Finding(
                        line_number=i,
                        line=_redact(line, value.split(":")[-1] if ":" in value else value,
                                     input_data.redact),
                        kind="short_auth_flag",
                        name=f"-{flag}",
                        severity="critical" if has_sudo else "high",
                        why="Short auth flags (curl -u user:pass, mysql -p<pass>) are argv: "
                        "visible in `ps` to every user on the host."
                        + (" sudo logs argv too." if has_sudo else ""),
                        fix="curl: use --netrc or a config file. mysql/psql: use a "
                        "~/.my.cnf or ~/.pgpass with 0600 permissions, or an env file.",
                    )
                )

            # 3. credentials inline in a URL
            for m in re.finditer(r"\b([a-z][a-z0-9+.-]*)://([^:/\s]+):([^@/\s]+)@", line):
                scheme, user, pw = m.group(1), m.group(2), m.group(3)
                if pw.startswith("$"):
                    continue
                findings.append(
                    Finding(
                        line_number=i,
                        line=_redact(line, pw, input_data.redact),
                        kind="url_inline_credentials",
                        name=f"{scheme}://{user}:…",
                        severity="critical" if has_sudo else "high",
                        why="A URL carrying user:password is argv here, and tends to be "
                        "echoed by clients into their own logs as well.",
                        fix="Move the URL into an env file or secrets manager and read "
                        "it inside the process that needs it.",
                    )
                )

        crit = sum(1 for f in findings if f.severity == "critical")
        if not findings:
            verdict = "No secrets found on command lines."
        elif crit:
            verdict = (
                f"{crit} CRITICAL: a secret is passed as argv under sudo, so its value "
                f"is written to the auth log and journald verbatim."
            )
        else:
            verdict = f"{len(findings)} finding(s): secrets visible in the process table."

        return RunOutput(
            findings=findings,
            critical_count=crit,
            total_count=len(findings),
            clean=not findings,
            verdict=verdict,
        )
