"""Audit systemd unit and timer files for the footguns that report success while failing.

systemd's failure modes are unusually dangerous because most of them are *quiet*.
A unit that cannot possibly work still answers `Result=success`; a timer that
silently skipped three weeks of runs looks identical to one that ran; an alert
path wired to `OnFailure=` can be structurally dead while every config file
involved looks correct in isolation. None of these produce an error. They
produce a **confident wrong answer**, which is worse, because the operator reads
the green and stops looking.

Four of the checks here exist because the exact mistake was made and measured,
not because it seemed plausible:

1. **`systemctl show` returns `Result=success` for a unit that does not exist.**
   Query a typo'd or template-shaped unit name and you get `LoadState=not-found`
   *and* `Result=success` in the same record. A health gate that reads `Result`
   alone — which is the obvious thing to read — passes on a phantom. This was
   found after two scheduled audit jobs were built around
   `foo-pipeline@<instance>.service`, a template that had never existed; both
   would have reported five healthy services and proceeded.

2. **`SuccessExitStatus=` widened to swallow real failures.** A service exits 1,
   systemd records `success`, and `OnFailure=` never fires. This usually starts
   as a legitimate fix — a script inherits CLI exit-code semantics and alerts
   every run — but the remedy is to correct the script's exit codes, not to
   teach systemd that failure is success. Once widened, the unit can never
   report a genuine fault again.

3. **`OnCalendar=` without an explicit timezone** is evaluated in the server's
   local zone, and so are `systemctl list-timers` and `journalctl --since`. On a
   box that is not on UTC, a schedule written with UTC intent fires hours away
   from where its author thinks, and every subsequent reading of the logs is out
   of register in the same direction, so nothing contradicts it.

4. **`Persistent=false` on an infrequent timer** means a missed fire is simply
   gone. A weekly job on a box that was down that day waits another week, in
   silence. The inverse is also a real problem: `Persistent=true` on a
   high-frequency timer produces a catch-up burst after any outage.

## What it returns

Findings with a stable `code`, a severity, what was found, and a concrete fix —
plus a `verdict` and the parsed schedule. Optionally accepts pasted
`systemctl show` output to catch the phantom-unit case, which cannot be seen in
the unit file at all because the defining property is that there is no file.

Pure stdlib parsing. No network, no systemd required — it reads text, so it runs
anywhere, including against units for a host you are not on.
"""
import logging
import re
from typing import Dict, List, Optional, Tuple

from inferencesh import BaseApp, BaseAppSetup
from pydantic import BaseModel, Field

logger = logging.getLogger("systemd-unit-audit")

# Calendar shorthands systemd accepts, mapped to an approximate period in
# seconds. Used only to decide "is this frequent or infrequent", so the
# approximation is fine -- month and year are nominal.
_SHORTHAND_PERIOD: Dict[str, int] = {
    "minutely": 60,
    "hourly": 3600,
    "daily": 86400,
    "weekly": 604800,
    "monthly": 2592000,
    "quarterly": 7776000,
    "semiannually": 15552000,
    "yearly": 31536000,
    "annually": 31536000,
}

# Anything at or below this is "frequent": a catch-up after downtime is a burst,
# so Persistent=true is the wrong default. Anything at or above the infrequent
# threshold loses real work when a fire is missed, so Persistent=true is right.
_FREQUENT_MAX_SEC = 3600
_INFREQUENT_MIN_SEC = 86400

# systemd accepts a trailing timezone on OnCalendar. Without one it uses the
# system zone -- which is the trap, because the line still looks absolute.
_TZ_SUFFIX = re.compile(
    r"\b(UTC|GMT|Z|[A-Za-z]+/[A-Za-z_+\-0-9]+)\s*$"
)

# A time-of-day mentioned in free text (Description=, comments), used to check
# whether the prose agrees with the actual schedule.
_TEXT_TIME = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")

_DOW_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_DOW_INDEX = {n: i for i, n in enumerate(_DOW_NAMES)}


def _matching_weekdays(spec: str) -> int:
    """How many distinct weekdays a calendar spec can match (7 when unrestricted).

    systemd allows a day-of-week prefix -- "Sun", "Mon..Fri", "Mon,Thu" -- before
    the date field. It changes the interval between fires by up to 7x, so a
    weekly timer read as daily understates its period badly.
    """
    head = spec.strip().split()[0] if spec.strip() else ""
    low = head.lower().rstrip(",")
    if not any(d in low for d in _DOW_NAMES):
        return 7
    days = set()
    for part in low.split(","):
        part = part.strip()
        if ".." in part:
            a, _, b = part.partition("..")
            ai, bi = _DOW_INDEX.get(a[:3]), _DOW_INDEX.get(b[:3])
            if ai is None or bi is None:
                continue
            i = ai
            while True:
                days.add(i)
                if i == bi:
                    break
                i = (i + 1) % 7
        else:
            i = _DOW_INDEX.get(part[:3])
            if i is not None:
                days.add(i)
    return len(days) or 7


_SEVERITY_RANK = {"error": 0, "warn": 1, "info": 2}


class Finding(BaseModel):
    code: str = Field(description="Stable machine-readable identifier")
    severity: str = Field(description="error, warn or info")
    detail: str = Field(description="What was found, quoting the offending directive")
    fix: str = Field(description="Concrete suggestion")


class AppSetup(BaseAppSetup):
    infrequent_threshold_hours: int = Field(
        default=24,
        description="At or above this period, a missed fire is treated as lost work "
                    "and Persistent=true is expected.",
    )
    frequent_threshold_minutes: int = Field(
        default=60,
        description="At or below this period, Persistent=true is treated as a "
                    "thundering-herd risk after downtime.",
    )


class RunInput(BaseModel):
    unit_text: str = Field(
        description="Contents of a .service or .timer file. Paste the whole file, "
                    "including comments -- some checks compare the prose to the schedule."
    )
    unit_name: Optional[str] = Field(
        None,
        description="Filename, e.g. 'backup.timer'. Used to decide which checks apply "
                    "when the text alone is ambiguous.",
    )
    systemctl_show_output: Optional[str] = Field(
        None,
        description="Optional. Paste `systemctl show -p LoadState -p ActiveState "
                    "-p Result -p ExecMainStatus <unit>` output to catch a unit that "
                    "reports success while not existing -- invisible in the file itself.",
    )
    server_timezone: Optional[str] = Field(
        None,
        description="IANA zone the host runs in, e.g. 'Asia/Kolkata'. Supply it to get "
                    "a sharper warning when OnCalendar has no explicit timezone.",
    )


class RunOutput(BaseModel):
    unit_name: str = Field(description="Unit this report is about")
    unit_kind: str = Field(description="service, timer, or other")
    verdict: str = Field(description="clean, warn or broken")
    schedules: List[str] = Field(description="Every OnCalendar / OnBootSec value found")
    inferred_period_seconds: Optional[int] = Field(
        None, description="Approximate seconds between fires, when derivable"
    )
    findings: List[Finding] = Field(description="Problems found, worst first")
    summary: str = Field(description="One-line human-readable result")


class App(BaseApp):

    async def setup(self, config: AppSetup):
        self.infrequent_min = max(1, config.infrequent_threshold_hours) * 3600
        self.frequent_max = max(1, config.frequent_threshold_minutes) * 60
        logger.info(
            "systemd-unit-audit ready (infrequent>=%ds frequent<=%ds)",
            self.infrequent_min, self.frequent_max,
        )

    async def run(self, input_data: RunInput) -> RunOutput:
        text = input_data.unit_text or ""
        if not text.strip():
            raise RuntimeError(
                "unit_text is empty. Paste the contents of a .service or .timer file."
            )

        name = (input_data.unit_name or "").strip()
        directives = self._parse(text)
        kind = self._kind(name, directives)
        logger.info("auditing unit_name=%r kind=%s directives=%d",
                    name or "<unnamed>", kind, len(directives))

        findings: List[Finding] = []
        schedules = [v for k, v in directives if k == "oncalendar"]
        schedules += [v for k, v in directives if k in ("onbootsec", "onunitactivesec")]

        period = self._period(schedules)

        self._check_success_exit_status(directives, findings)
        self._check_oncalendar_timezone(directives, input_data.server_timezone, findings)
        self._check_persistent(directives, period, kind, findings)
        self._check_prose_vs_schedule(text, directives, findings)
        self._check_oneshot(directives, findings)
        self._check_randomized_delay(directives, period, findings)
        self._check_show_output(input_data.systemctl_show_output, findings)

        findings.sort(key=lambda f: _SEVERITY_RANK.get(f.severity, 3))

        if any(f.severity == "error" for f in findings):
            verdict = "broken"
        elif any(f.severity == "warn" for f in findings):
            verdict = "warn"
        else:
            verdict = "clean"

        errs = sum(1 for f in findings if f.severity == "error")
        warns = sum(1 for f in findings if f.severity == "warn")
        if verdict == "clean":
            summary = f"{name or 'unit'}: clean — no silent-failure patterns found"
        else:
            summary = (
                f"{name or 'unit'}: {verdict} — {errs} error(s), {warns} warning(s); "
                f"worst is {findings[0].code}"
            )

        logger.info("verdict=%s findings=%d", verdict, len(findings))

        return RunOutput(
            unit_name=name or "(unnamed)",
            unit_kind=kind,
            verdict=verdict,
            schedules=schedules,
            inferred_period_seconds=period,
            findings=findings,
            summary=summary,
        )

    # ---------------- parsing ----------------

    @staticmethod
    def _parse(text: str) -> List[Tuple[str, str]]:
        """Return (lowercased_key, raw_value) for every active directive.

        Commented lines are deliberately excluded. A commented directive is not
        in force, and treating one as active is its own class of mistake -- a
        config read that matches a comment reports a value the system has never
        used.
        """
        out: List[Tuple[str, str]] = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith(";"):
                continue
            if line.startswith("[") and line.endswith("]"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            out.append((key.strip().lower(), value.strip()))
        return out

    @staticmethod
    def _kind(name: str, directives: List[Tuple[str, str]]) -> str:
        low = name.lower()
        if low.endswith(".timer"):
            return "timer"
        if low.endswith(".service"):
            return "service"
        keys = {k for k, _ in directives}
        if keys & {"oncalendar", "onbootsec", "onunitactivesec"}:
            return "timer"
        if keys & {"execstart", "type", "successexitstatus"}:
            return "service"
        return "other"

    def _period(self, schedules: List[str]) -> Optional[int]:
        """Approximate seconds between fires. Coarse by design."""
        best: Optional[int] = None
        for s in schedules:
            p = self._period_one(s)
            if p is not None and (best is None or p < best):
                best = p
        return best

    @staticmethod
    def _period_one(spec: str) -> Optional[int]:
        s = spec.strip().lower()
        for word, secs in _SHORTHAND_PERIOD.items():
            if s == word or s.startswith(word + " "):
                return secs
        # OnCalendar=*-*-* HH:MM:SS  /  with lists and ranges in the minute field
        m = re.search(r"(\d{1,2}|\*)(?:\.\.(\d{1,2}))?:([0-9,\.\*/]+)", s)
        if not m:
            return None
        hour_field, hour_to, minute_field = m.group(1), m.group(2), m.group(3)
        # minute repetitions: "00,30" -> 2 per hour; "*/15" -> 4 per hour
        if "/" in minute_field:
            try:
                step = int(minute_field.split("/")[-1])
                per_hour = max(1, 60 // max(1, step))
            except ValueError:
                per_hour = 1
        elif "," in minute_field:
            per_hour = len([p for p in minute_field.split(",") if p.strip()])
        elif minute_field.strip() == "*":
            per_hour = 60
        else:
            per_hour = 1
        if hour_field == "*":
            hours_per_day = 24
        elif hour_to is not None:
            try:
                hours_per_day = max(1, int(hour_to) - int(hour_field) + 1)
            except ValueError:
                hours_per_day = 1
        else:
            hours_per_day = 1
        fires_per_day = max(1, per_hour * hours_per_day)
        period = max(1, 86400 // fires_per_day)
        # A day-of-week prefix ("Sun", "Mon..Fri") restricts which days match at
        # all, so the gap between fires is longer than the within-day rate
        # suggests. Missing this understates the period of a weekly timer by 7x,
        # which is exactly the case where Persistent matters most.
        days = _matching_weekdays(s)
        if days and days < 7:
            period = period * 7 // days
        return period

    # ---------------- checks ----------------

    def _check_success_exit_status(self, directives, findings):
        for key, value in directives:
            if key != "successexitstatus":
                continue
            codes = [c for c in re.split(r"[\s,]+", value) if c]
            nonzero = [c for c in codes if c not in ("0", "SIGTERM", "TERM")]
            if not nonzero:
                continue
            has_onfailure = any(k == "onfailure" for k, _ in directives)
            findings.append(Finding(
                code="success_exit_status_masks_failure",
                severity="error",
                detail=(
                    f"SuccessExitStatus={value} treats exit code(s) "
                    f"{', '.join(nonzero)} as success. systemd will record "
                    f"Result=success when the process exits with them, so a genuine "
                    f"fault is indistinguishable from a clean run and no health check "
                    f"reading Result can ever detect it."
                ),
                fix=(
                    "Fix the exit codes in the program instead. A script should exit 0 "
                    "for 'no work available' or 'partial success' and non-zero only for "
                    "a genuine incident. If you must keep this, any health gate on the "
                    "unit has to read ExecMainStatus, not Result."
                ),
            ))
            if has_onfailure:
                findings.append(Finding(
                    code="onfailure_defeated_by_success_exit_status",
                    severity="error",
                    detail=(
                        f"This unit declares OnFailure= but SuccessExitStatus={value} "
                        f"prevents those exit codes from ever counting as failure. The "
                        f"alert path is structurally dead for them."
                    ),
                    fix=(
                        "Narrow SuccessExitStatus to 0, or drop the OnFailure= wiring "
                        "so it does not imply coverage it cannot deliver."
                    ),
                ))

    def _check_oncalendar_timezone(self, directives, server_tz, findings):
        for key, value in directives:
            if key != "oncalendar":
                continue
            if _TZ_SUFFIX.search(value):
                continue
            if value.strip().lower() in _SHORTHAND_PERIOD:
                continue
            where = (
                f"The host runs {server_tz}, so this fires at {server_tz} wall-clock time"
                if server_tz else
                "This fires at the host's local wall-clock time, whatever that is"
            )
            findings.append(Finding(
                code="oncalendar_no_explicit_timezone",
                severity="warn",
                detail=(
                    f"OnCalendar={value} carries no timezone. {where}, not UTC. "
                    f"`systemctl list-timers` and `journalctl --since` also parse in "
                    f"local time, so a UTC-intent reading of this schedule and of its "
                    f"logs are wrong in the same direction and will not contradict "
                    f"each other."
                ),
                fix=(
                    f"Append an explicit zone: `OnCalendar={value} UTC`. Derive any "
                    f"stated time from the directive rather than writing it alongside."
                ),
            ))

    def _check_persistent(self, directives, period, kind, findings):
        if kind != "timer" and not any(k == "oncalendar" for k, _ in directives):
            return
        persistent = None
        for key, value in directives:
            if key == "persistent":
                persistent = value.strip().lower() in ("1", "yes", "true", "on")
        if period is None:
            return
        if period >= self.infrequent_min and not persistent:
            human = f"{period // 86400}d" if period >= 86400 else f"{period // 3600}h"
            findings.append(Finding(
                code="infrequent_timer_not_persistent",
                severity="warn",
                detail=(
                    f"This timer fires roughly every {human} and Persistent is "
                    f"{'false' if persistent is False else 'unset (defaults to false)'}. "
                    f"If the machine is down or the timer is inactive at the scheduled "
                    f"instant, the run is skipped silently and the next chance is a "
                    f"full period away."
                ),
                fix=(
                    "Set Persistent=true so a missed fire is caught up on next boot. "
                    "Reserve Persistent=false for frequent timers where a catch-up "
                    "burst would be worse than a skip."
                ),
            ))
        elif period <= self.frequent_max and persistent:
            human = f"{period // 60}m" if period >= 60 else f"{period}s"
            findings.append(Finding(
                code="frequent_timer_persistent_burst",
                severity="warn",
                detail=(
                    f"This timer fires roughly every {human} with Persistent=true. "
                    f"After any outage systemd runs the accumulated misses, which for a "
                    f"high-frequency unit is a burst against whatever it talks to."
                ),
                fix="Set Persistent=false for timers at this frequency; a skip is cheaper than a burst.",
            ))

    def _check_prose_vs_schedule(self, text, directives, findings):
        """A stated time that disagrees with the schedule it describes.

        Labels drift from the thing they label, and the label is what gets read.
        """
        cal = [v for k, v in directives if k == "oncalendar"]
        if not cal:
            return
        sched_times = set()
        for c in cal:
            for m in _TEXT_TIME.finditer(c):
                sched_times.add(f"{int(m.group(1)):02d}:{m.group(2)}")
        if not sched_times:
            return
        prose = []
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("#") or line.startswith(";"):
                prose.append(line)
            elif line.lower().startswith("description="):
                prose.append(line)
        stated = set()
        for line in prose:
            for m in _TEXT_TIME.finditer(line):
                stated.add(f"{int(m.group(1)):02d}:{m.group(2)}")
        drift = stated - sched_times
        if drift and stated:
            findings.append(Finding(
                code="stated_time_disagrees_with_schedule",
                severity="warn",
                detail=(
                    f"The description or comments mention {', '.join(sorted(drift))} "
                    f"but OnCalendar fires at {', '.join(sorted(sched_times))}. One of "
                    f"them is wrong, and the prose is the one people read."
                ),
                fix=(
                    "Derive the stated time from OnCalendar rather than typing it "
                    "beside the directive, or delete it so there is a single source."
                ),
            ))

    @staticmethod
    def _check_oneshot(directives, findings):
        d = dict(directives)
        typ = d.get("type", "").strip().lower()
        restart = d.get("restart", "").strip().lower()
        if typ == "oneshot" and restart in ("always", "on-failure"):
            findings.append(Finding(
                code="oneshot_with_restart",
                severity="warn",
                detail=(
                    f"Type=oneshot with Restart={restart}. A oneshot that restarts can "
                    f"loop on a persistent fault, and for timer-driven work the timer "
                    f"already provides the retry cadence."
                ),
                fix="Drop Restart= on timer-driven oneshot units and let the timer schedule retries.",
            ))

    @staticmethod
    def _check_randomized_delay(directives, period, findings):
        for key, value in directives:
            if key != "randomizeddelaysec":
                continue
            secs = None
            m = re.match(r"^(\d+)\s*(s|sec|m|min|h|hour)?", value.strip().lower())
            if m:
                n = int(m.group(1))
                unit = (m.group(2) or "s")[0]
                secs = n * {"s": 1, "m": 60, "h": 3600}.get(unit, 1)
            if secs and period and secs >= period:
                findings.append(Finding(
                    code="randomized_delay_exceeds_interval",
                    severity="warn",
                    detail=(
                        f"RandomizedDelaySec={value} is at least as long as the ~{period}s "
                        f"interval between fires, so runs can overlap or reorder."
                    ),
                    fix="Keep RandomizedDelaySec well under the schedule interval.",
                ))

    @staticmethod
    def _check_show_output(show: Optional[str], findings):
        """The phantom-unit case, which is invisible in the file by definition."""
        if not show or not show.strip():
            return
        props: Dict[str, str] = {}
        for raw in show.splitlines():
            line = raw.strip()
            if "=" in line:
                k, _, v = line.partition("=")
                props[k.strip().lower()] = v.strip()
        load = props.get("loadstate", "").lower()
        result = props.get("result", "").lower()
        exec_status = props.get("execmainstatus", "").strip()

        if load and load != "loaded":
            findings.append(Finding(
                code="unit_not_found_but_reports_success",
                severity="error",
                detail=(
                    f"LoadState={load}"
                    + (f" while Result={result}" if result else "")
                    + ". systemd answers Result=success for units it has never loaded, "
                      "so a health check reading Result alone passes on a unit that does "
                      "not exist. Usually a typo, a renamed unit, or a template instance "
                      "whose template was never installed."
                ),
                fix=(
                    "Confirm the unit name with `systemctl list-units '<prefix>*' --all`. "
                    "In any gate, print LoadState beside Result and treat "
                    "LoadState != loaded as an instrument error, never as a result."
                ),
            ))

        if result == "success" and exec_status and exec_status not in ("0", ""):
            findings.append(Finding(
                code="result_success_with_nonzero_exit",
                severity="error",
                detail=(
                    f"Result=success but ExecMainStatus={exec_status}. The process "
                    f"exited non-zero and systemd still called it success — almost "
                    f"always because SuccessExitStatus= was widened to include that code."
                ),
                fix=(
                    "Read ExecMainStatus rather than Result for this unit, and narrow "
                    "SuccessExitStatus so genuine faults can surface again."
                ),
            ))
