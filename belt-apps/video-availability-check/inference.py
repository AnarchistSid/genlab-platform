"""Find out WHY a video URL will not download, before you build a pipeline on it.

Every failure mode looks the same in a log line — "yt-dlp failed" — and they
need completely different responses:

  * **geo-blocked** — the video exists and works elsewhere. Move the fetch,
    use a proxy in the right region, or pick different sources.
  * **bot-challenge** — the platform is refusing your IP, typically a
    datacenter one. Cookies or a residential egress fix this; changing region
    does not.
  * **removed / private / members-only** — nothing will fix it. Drop the URL.
  * **age-gated** — needs authentication, not a different network.

Confusing the first two costs the most time, because both surface as a
download failure from a cloud VM and the obvious fix for one is useless for
the other. This came out of diagnosing a content pipeline that went dark for
nine days: the reels were failing with "The uploader has not made this video
available in your country", which had been read as bot detection for a week.

Metadata only — `--skip-download`. Nothing is fetched, so it is fast and
cheap enough to screen a whole candidate list before committing to it.
"""
import json
import logging
import re
import shutil
import subprocess
import sys
from typing import List, Optional

from inferencesh import BaseApp, BaseAppSetup
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Ordered: first match wins, most specific first.
_PATTERNS = [
    ("geo_blocked", r"not made this video available in your country|"
                    r"not available in your country|blocked it in your country|"
                    r"video is not available in your|geo restrict"),
    ("bot_challenge", r"sign in to confirm|confirm you.?re not a bot|"
                      r"unusual traffic|captcha|too many requests|http error 429"),
    # "This video is unavailable" (note: not "Video unavailable") is what
    # YouTube returns for a malformed or deleted ID. Missing it classified a
    # dead URL as "unknown" and sent the reader looking for a cause.
    ("removed", r"video unavailable|this video is unavailable|has been removed|"
                r"has been terminated|no longer available|incomplete youtube id|"
                r"account associated with this video has been"),
    ("private", r"private video|this video is private|members[- ]only|"
                r"join this channel to get access"),
    ("age_gated", r"age.?restricted|sign in to confirm your age|inappropriate for some users"),
    ("live_not_started", r"premieres in|live event will begin|this live event has not"),
    ("network", r"unable to download|connection reset|timed out|temporary failure|"
                r"name or service not known"),
]

_ADVICE = {
    "geo_blocked": "The video is fine — your egress region is not. Fetch from another "
                   "region, route through a proxy where it is available, or prefer "
                   "sources without regional licensing.",
    "bot_challenge": "The platform is refusing this IP, usually a datacenter one. "
                     "Supply cookies from a real session or move egress to a "
                     "residential/consumer network. Changing REGION will not help.",
    "removed": "Permanently gone. Drop the URL and do not retry.",
    "private": "Requires access you do not have. Drop unless you can authenticate.",
    "age_gated": "Needs an authenticated session, not a different network.",
    "live_not_started": "Not published yet — retry after the scheduled start.",
    "network": "Transient or DNS. Retry before concluding anything about the video.",
    "unknown": "Unrecognised failure — read raw_error and consider filing the pattern.",
}


class UrlResult(BaseModel):
    url: str = Field(description="The URL checked.")
    available: bool = Field(description="True when metadata was retrieved successfully.")
    reason: str = Field(description="ok | geo_blocked | bot_challenge | removed | private | age_gated | live_not_started | network | unknown")
    advice: str = Field(description="What actually fixes this class of failure.")
    title: Optional[str] = Field(None, description="Title, when available.")
    duration_seconds: Optional[float] = Field(None, description="Duration, when available.")
    raw_error: str = Field("", description="First line of the tool's own error, trimmed.")


class AppSetup(BaseAppSetup):
    """Stateless."""


class RunInput(BaseModel):
    urls: List[str] = Field(
        default_factory=list,
        description="Video URLs to check. Anything yt-dlp supports.",
    )
    timeout_seconds: int = Field(
        45, description="Per-URL timeout. Raise for slow origins.",
    )
    max_urls: int = Field(
        25, description="Safety cap so a huge list cannot run for hours.",
    )


class RunOutput(BaseModel):
    checked: int = Field(description="URLs actually checked.")
    available: int = Field(description="How many are downloadable from here.")
    unavailable: int = Field(description="How many are not.")
    by_reason: dict = Field(description="Count per failure class.")
    dominant_problem: str = Field(description="The single class to act on first, or 'none'.")
    results: List[UrlResult] = Field(description="Per-URL detail.")
    report: str = Field(description="Human-readable summary with the advice that matters.")


def classify(stderr: str) -> str:
    low = (stderr or "").lower()
    for name, pattern in _PATTERNS:
        if re.search(pattern, low):
            return name
    return "unknown"


def check_one(url: str, timeout: int) -> UrlResult:
    try:
        r = subprocess.run(
            # `python -m yt_dlp` rather than the `yt-dlp` console script: the
            # pip entry point is not always on PATH in a container, and the
            # first cloud deploy of this app classified every URL as "unknown"
            # for exactly that reason. The module form works wherever the
            # package is importable.
            [sys.executable, "-m", "yt_dlp",
             "--skip-download", "--no-warnings", "--dump-json", url],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return UrlResult(url=url, available=False, reason="network",
                         advice=_ADVICE["network"], raw_error=f"timed out after {timeout}s")
    except FileNotFoundError:
        return UrlResult(url=url, available=False, reason="unknown",
                         advice="yt-dlp is not importable in this environment.",
                         raw_error="python -m yt_dlp not found")
    if r.returncode == 0 and (r.stdout or "").strip():
        try:
            meta = json.loads((r.stdout or "").splitlines()[0])
        except Exception:
            meta = {}
        return UrlResult(url=url, available=True, reason="ok", advice="",
                         title=meta.get("title"),
                         duration_seconds=meta.get("duration"))
    err = (r.stderr or "").strip()
    first = next((ln for ln in err.splitlines() if ln.strip()), "")[:300]
    reason = classify(err)
    return UrlResult(url=url, available=False, reason=reason,
                     advice=_ADVICE.get(reason, _ADVICE["unknown"]), raw_error=first)


class App(BaseApp):
    async def setup(self, config: AppSetup):
        try:
            import yt_dlp  # noqa: F401
            ok = True
        except ImportError:
            ok = False
        logger.info("video-availability-check ready (yt_dlp importable=%s)", ok)

    async def run(self, input_data: RunInput) -> RunOutput:
        urls = [u.strip() for u in input_data.urls if u and u.strip()][: max(1, input_data.max_urls)]
        if not urls:
            return RunOutput(checked=0, available=0, unavailable=0, by_reason={},
                             dominant_problem="none", results=[],
                             report="No URLs provided.")

        logger.info("checking %d url(s)", len(urls))
        results = [check_one(u, input_data.timeout_seconds) for u in urls]
        ok = sum(1 for r in results if r.available)
        by_reason: dict = {}
        for r in results:
            if not r.available:
                by_reason[r.reason] = by_reason.get(r.reason, 0) + 1

        dominant = max(by_reason, key=by_reason.get) if by_reason else "none"
        lines = [f"{ok}/{len(results)} available from this host."]
        if by_reason:
            lines.append("  failures: " + ", ".join(f"{k}={v}" for k, v in
                                                    sorted(by_reason.items(), key=lambda kv: -kv[1])))
            lines.append(f"  act on first: {dominant} — {_ADVICE.get(dominant, '')}")
            # The distinction that costs the most time when missed.
            if "geo_blocked" in by_reason and "bot_challenge" in by_reason:
                lines.append(
                    "  NOTE: both geo_blocked and bot_challenge present — these need "
                    "OPPOSITE fixes. Region change helps the first and not the second."
                )
        for r in results:
            mark = "ok " if r.available else "FAIL"
            extra = r.title[:44] if (r.available and r.title) else r.reason
            lines.append(f"    [{mark}] {r.url[:58]}  {extra}")

        logger.info("%d/%d available, dominant=%s", ok, len(results), dominant)
        return RunOutput(checked=len(results), available=ok,
                         unavailable=len(results) - ok, by_reason=by_reason,
                         dominant_problem=dominant, results=results,
                         report="\n".join(lines))
