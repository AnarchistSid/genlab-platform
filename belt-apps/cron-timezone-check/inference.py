"""Show when a cron schedule actually fires, in both timezones, and flag the days it does not.

Three things about cron are true, widely known in isolation, and almost never
held in mind at the same time:

1. **Cron fires on local wall-clock time**, not UTC. So does `systemctl
   list-timers`, and so does `journalctl --since`. If the box is on
   `Asia/Kolkata` and you read `02:30` as UTC, every conclusion you draw is
   5h30m out of register with reality.
2. **Some wall-clock times do not exist.** On the spring-forward date, a zone
   that jumps 02:00 to 03:00 has no 02:30. A daily 02:30 job has no valid
   instant to run at, and what happens next is scheduler-specific: Vixie cron
   skips it, systemd runs it at the boundary, and neither tells you.
3. **Some wall-clock times happen twice.** On the fall-back date, 01:30 occurs
   at two distinct instants. A job scheduled then may run twice.

The first one is not exotic and it is the one that bites hardest, because it
produces a *confident wrong answer* rather than an error. Reading a timer table
in the wrong zone yields a coherent story about a schedule that shifted, or a
job that missed its window, and nothing in the output contradicts it. This app
exists because that mistake was made four times in a single day and one of the
resulting findings was believed before it was retracted.

## What it returns

The next N fires as **paired local and UTC timestamps**, so the two readings sit
side by side and cannot be conflated. Plus findings for:

- `utc_local_offset` — the server is not on UTC, quantifying how far off a
  UTC reading of local output would be
- `dst_gap_skip` — a scheduled time that does not exist on a given date
- `dst_overlap_double` — a scheduled time that occurs twice on a given date
- `dom_dow_or_semantics` — day-of-month and day-of-week both restricted, which
  cron treats as OR, not the AND nearly everyone expects
- `never_fires` — a date specification with no valid instant, ever
- `high_frequency` — schedules where a catch-up after downtime means a burst

Scanning covers a full year ahead by default, because DST transitions are the
whole point and a 7-day window would miss them for eleven months of the year.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from inferencesh import BaseApp, BaseAppSetup
from pydantic import BaseModel, Field

logger = logging.getLogger("cron-timezone-check")

UTC = timezone.utc

_MONTHS = {n: i for i, n in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_DOWS = {n: i for i, n in enumerate(
    ["sun", "mon", "tue", "wed", "thu", "fri", "sat"], start=0)}

# Cron's own shorthands. @reboot has no clock semantics at all, which is worth
# saying out loud rather than silently returning zero fires.
_ALIASES = {
    "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *", "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *", "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}


class CronParseError(ValueError):
    pass


def _expand(field: str, lo: int, hi: int, names: Optional[Dict[str, int]] = None) -> Set[int]:
    """Expand one cron field into the set of values it matches."""
    out: Set[int] = set()
    for part in field.split(","):
        part = part.strip().lower()
        if not part:
            raise CronParseError(f"empty element in {field!r}")
        step = 1
        if "/" in part:
            part, _, step_s = part.partition("/")
            if not step_s.isdigit() or int(step_s) < 1:
                raise CronParseError(f"bad step {step_s!r} in {field!r}")
            step = int(step_s)
            part = part or "*"

        def num(tok: str) -> int:
            tok = tok.strip()
            if names and tok in names:
                return names[tok]
            if not tok.lstrip("-").isdigit():
                raise CronParseError(f"unrecognised value {tok!r} in {field!r}")
            return int(tok)

        if part == "*":
            start, end = lo, hi
        elif "-" in part[1:]:
            a, _, b = part.partition("-")
            start, end = num(a), num(b)
        else:
            start = end = num(part)

        if start > end:
            raise CronParseError(f"reversed range {part!r} in {field!r}")
        for v in range(start, end + 1, step):
            if not (lo <= v <= hi):
                raise CronParseError(f"value {v} out of range {lo}-{hi} in {field!r}")
            out.add(v)
    if not out:
        raise CronParseError(f"{field!r} matches nothing")
    return out


class CronSpec:
    """A parsed 5-field cron expression.

    `dom_restricted` / `dow_restricted` are kept as separate flags because the
    day matching rule depends on them, and it is not the rule most people
    expect -- see `matches_date`.
    """

    def __init__(self, expr: str):
        raw = expr.strip()
        self.expr = raw
        low = raw.lower()
        if low == "@reboot":
            raise CronParseError("@reboot has no clock schedule; it fires at boot")
        if low in _ALIASES:
            raw = _ALIASES[low]
        fields = raw.split()
        if len(fields) != 5:
            raise CronParseError(
                f"expected 5 fields (minute hour day-of-month month day-of-week), got {len(fields)}")
        self.minutes = _expand(fields[0], 0, 59)
        self.hours = _expand(fields[1], 0, 23)
        self.doms = _expand(fields[2], 1, 31)
        self.months = _expand(fields[3], 1, 12, _MONTHS)
        dows = _expand(fields[4], 0, 7, _DOWS)
        self.dows = {0 if d == 7 else d for d in dows}   # 7 and 0 both mean Sunday
        self.dom_restricted = fields[2].strip() != "*"
        self.dow_restricted = fields[4].strip() != "*"

    def matches_date(self, d) -> bool:
        """Vixie cron's day rule: OR when both day fields are restricted.

        `0 0 13 * 5` fires on the 13th *and* on every Friday -- not only on
        Friday the 13th. This surprises nearly everyone, so when both fields
        are restricted the caller raises `dom_dow_or_semantics`.
        """
        if d.month not in self.months:
            return False
        dom_ok = d.day in self.doms
        dow_ok = ((d.weekday() + 1) % 7) in self.dows   # Python Mon=0 -> cron Sun=0
        if self.dom_restricted and self.dow_restricted:
            return dom_ok or dow_ok
        return dom_ok and dow_ok

    def times(self):
        return [(h, m) for h in sorted(self.hours) for m in sorted(self.minutes)]


def _nonexistent(naive: datetime, tz) -> bool:
    """True if this wall-clock time is skipped by a DST forward jump."""
    aware = naive.replace(tzinfo=tz)
    return aware.astimezone(UTC).astimezone(tz).replace(tzinfo=None) != naive


def _ambiguous(naive: datetime, tz) -> bool:
    """True if this wall-clock time occurs twice under a DST fall-back."""
    a = naive.replace(tzinfo=tz, fold=0).utcoffset()
    b = naive.replace(tzinfo=tz, fold=1).utcoffset()
    return a != b


class Finding(BaseModel):
    code: str = Field(description="Stable machine-readable identifier")
    severity: str = Field(description="error, warn or info")
    detail: str = Field(description="What was found, with dates")
    fix: str = Field(description="Concrete suggestion")


class Fire(BaseModel):
    local: str = Field(description="Wall-clock time in the server timezone -- what cron matches on")
    utc: str = Field(description="The same instant in UTC")
    offset: str = Field(description="Server offset from UTC at that instant")
    note: Optional[str] = Field(None, description="Set when this fire is unusual")


class AppSetup(BaseAppSetup):
    scan_days: int = Field(
        default=365,
        description="How far ahead to scan for DST problems. Below ~365 you will "
                    "miss transitions for most of the year.",
    )
    max_fires: int = Field(default=10, description="How many upcoming fires to return")


class RunInput(BaseModel):
    expression: str = Field(
        description="5-field cron expression, or an alias like @daily. "
                    "Examples: '30 2 * * *', '*/15 * * * *', '0 0 13 * 5'"
    )
    timezone: str = Field(
        default="UTC",
        description="IANA timezone the scheduler runs in -- the box's zone, not yours. "
                    "Check with `timedatectl`. Examples: Asia/Kolkata, Europe/London.",
    )
    from_iso: Optional[str] = Field(
        None,
        description="UTC instant to start from, ISO-8601. Defaults to now. Set it for "
                    "reproducible output.",
    )


class RunOutput(BaseModel):
    expression: str = Field(description="The expression as parsed")
    timezone: str = Field(description="Scheduler timezone")
    verdict: str = Field(description="clean, warn or broken")
    utc_offset_now: str = Field(description="Current offset of the scheduler zone from UTC")
    fires_per_day: int = Field(description="Fires on an ordinary matching day")
    next_fires: List[Fire] = Field(description="Upcoming fires, local and UTC side by side")
    findings: List[Finding] = Field(description="Problems found, worst first")
    dst_gap_dates: List[str] = Field(description="Dates where a scheduled time does not exist")
    dst_overlap_dates: List[str] = Field(description="Dates where a scheduled time occurs twice")
    summary: str = Field(description="One-line human-readable result")


class App(BaseApp):

    async def setup(self, config: AppSetup):
        self.scan_days = max(1, config.scan_days)
        self.max_fires = max(1, config.max_fires)
        logger.info("cron-timezone-check ready (scan_days=%d max_fires=%d)",
                    self.scan_days, self.max_fires)

    async def run(self, input_data: RunInput) -> RunOutput:
        findings: List[Finding] = []

        try:
            tz = ZoneInfo(input_data.timezone)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            raise RuntimeError(
                f"Unknown timezone {input_data.timezone!r}. Use an IANA name such as "
                f"'Asia/Kolkata' or 'Europe/London' -- abbreviations like 'IST' are "
                f"ambiguous (India and Ireland both claim it)."
            )

        try:
            spec = CronSpec(input_data.expression)
        except CronParseError as exc:
            raise RuntimeError(f"Could not parse {input_data.expression!r}: {exc}")

        if input_data.from_iso:
            start = datetime.fromisoformat(input_data.from_iso.replace("Z", "+00:00"))
            if start.tzinfo is None:
                start = start.replace(tzinfo=UTC)
        else:
            start = datetime.now(UTC)

        logger.info("expression=%r tz=%s from=%s",
                    spec.expr, input_data.timezone, start.isoformat())

        off_now = start.astimezone(tz).utcoffset() or timedelta(0)
        off_txt = self._fmt_offset(off_now)

        # --- the trap that motivated this app ----------------------------
        if off_now != timedelta(0):
            hours = off_now.total_seconds() / 3600
            findings.append(Finding(
                code="utc_local_offset", severity="warn",
                detail=(f"The scheduler runs on {input_data.timezone} ({off_txt}), not UTC. "
                        f"`systemctl list-timers` and `journalctl --since` both render and "
                        f"parse in this zone, so reading their output as UTC puts every "
                        f"timestamp {abs(hours):g}h out."),
                fix="Force the zone when reading: `TZ=UTC systemctl list-timers`, "
                    "`journalctl --since '...' --utc`. For SQL, group by "
                    "`AT TIME ZONE '" + input_data.timezone + "'` rather than assuming.",
            ))

        # --- day/day-of-week OR semantics --------------------------------
        if spec.dom_restricted and spec.dow_restricted:
            findings.append(Finding(
                code="dom_dow_or_semantics", severity="warn",
                detail=("Both day-of-month and day-of-week are restricted. Cron ORs them: "
                        "this fires on matching days of the month AND on matching weekdays, "
                        "not only when both agree."),
                fix="If you meant 'only when both match', cron cannot express it -- gate "
                    "inside the job, or use a systemd OnCalendar which ANDs instead.",
            ))

        per_day = len(spec.times())
        if per_day >= 60:
            findings.append(Finding(
                code="high_frequency", severity="info",
                detail=f"{per_day} fires per matching day.",
                fix="After downtime, a catch-up-enabled scheduler may fire these in a "
                    "burst. For systemd prefer Persistent=false on sub-hourly timers.",
            ))

        # --- scan ----------------------------------------------------------
        gaps: List[str] = []
        overlaps: List[str] = []
        fires: List[Fire] = []
        local_cursor = start.astimezone(tz)
        day = local_cursor.date()
        matched_any = False

        for _ in range(self.scan_days + 1):
            if spec.matches_date(day):
                matched_any = True
                for (h, m) in spec.times():
                    naive = datetime(day.year, day.month, day.day, h, m)
                    if _nonexistent(naive, tz):
                        stamp = f"{day.isoformat()} {h:02d}:{m:02d}"
                        if stamp not in gaps:
                            gaps.append(stamp)
                        continue
                    amb = _ambiguous(naive, tz)
                    if amb:
                        stamp = f"{day.isoformat()} {h:02d}:{m:02d}"
                        if stamp not in overlaps:
                            overlaps.append(stamp)
                    aware = naive.replace(tzinfo=tz)
                    if aware >= local_cursor and len(fires) < self.max_fires:
                        fires.append(Fire(
                            local=aware.strftime("%Y-%m-%d %H:%M:%S %Z"),
                            utc=aware.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
                            offset=self._fmt_offset(aware.utcoffset() or timedelta(0)),
                            note="occurs twice today (DST fall-back)" if amb else None,
                        ))
            day = day + timedelta(days=1)

        if not matched_any:
            findings.append(Finding(
                code="never_fires", severity="error",
                detail=(f"No date in the next {self.scan_days} days matches "
                        f"{spec.expr!r}. Common cause: a day/month pair that does not "
                        f"exist, such as 31 in a 30-day month or Feb 30."),
                fix="Check the day-of-month and month fields against a calendar.",
            ))

        if gaps:
            findings.append(Finding(
                code="dst_gap_skip", severity="error",
                detail=(f"{len(gaps)} scheduled time(s) do not exist because the clock "
                        f"jumps forward: {', '.join(gaps[:4])}"
                        f"{' ...' if len(gaps) > 4 else ''}. Vixie cron skips these; "
                        f"systemd runs at the boundary instead. Neither says so."),
                fix="Move the schedule outside the transition window -- 01:00 or 04:00 "
                    "is safe in every zone -- or run the scheduler on UTC.",
            ))
        if overlaps:
            findings.append(Finding(
                code="dst_overlap_double", severity="warn",
                detail=(f"{len(overlaps)} scheduled time(s) occur twice because the clock "
                        f"falls back: {', '.join(overlaps[:4])}"
                        f"{' ...' if len(overlaps) > 4 else ''}. The job may run twice."),
                fix="Make the job idempotent, or move it outside the transition window.",
            ))

        rank = {"error": 0, "warn": 1, "info": 2}
        findings.sort(key=lambda f: rank.get(f.severity, 3))
        sev = {f.severity for f in findings}
        verdict = "broken" if "error" in sev else ("warn" if "warn" in sev else "clean")

        first = fires[0].utc if fires else "none in window"
        # Count problems separately from notes. "clean -- 1 finding(s)" reads as a
        # contradiction; an informational note is not a problem and should not
        # look like one in the one line most people will actually read.
        problems = sum(1 for f in findings if f.severity in ("error", "warn"))
        notes = len(findings) - problems
        tail = f"{problems} finding(s)" if problems else "no problems"
        if notes:
            tail += f", {notes} note(s)"
        summary = (f"{verdict} — {spec.expr!r} on {input_data.timezone} ({off_txt}); "
                   f"next fire {first}; {tail}")
        logger.info("verdict=%s gaps=%d overlaps=%d findings=%d",
                    verdict, len(gaps), len(overlaps), len(findings))

        return RunOutput(
            expression=spec.expr, timezone=input_data.timezone, verdict=verdict,
            utc_offset_now=off_txt, fires_per_day=per_day, next_fires=fires,
            findings=findings, dst_gap_dates=gaps, dst_overlap_dates=overlaps,
            summary=summary,
        )

    @staticmethod
    def _fmt_offset(off: timedelta) -> str:
        total = int(off.total_seconds())
        sign = "+" if total >= 0 else "-"
        total = abs(total)
        return f"UTC{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}"
