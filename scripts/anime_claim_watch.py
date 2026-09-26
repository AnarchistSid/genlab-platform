#!/usr/bin/env python3
"""14-day claim watch for a pilot anime reel.

The stop rule it serves: one claim or strike on a studio's material pauses
that studio's fights and the reel comes down. So this has to notice a claim
without anyone remembering to look.

Exit codes follow rule #26: 0 unless a genuine incident needs paging. "Not yet
published", "no credentials", "nothing found" are all data-side outcomes that
belong in the log, not in systemd's failure counter.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

UA = "GenLab/1.0 (+https://github.com/anarchistsid/genlab)"
STATE = Path(os.environ.get("GENLAB_CLAIM_WATCH_STATE",
                            Path.home() / "GenLab/.runtime/anime_claim_watch.json"))


def load() -> dict:
    if not STATE.exists():
        return {}
    return json.loads(STATE.read_text())


def save(d: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2))
    tmp.replace(STATE)          # durable: the state file IS the record


def _access_token() -> tuple[str | None, str]:
    """Refresh the owning channel's OAuth token. Secrets are read from the
    environment and never printed -- a token in a log is a token in journald."""
    env = dict(os.environ)
    # Under launchd there IS no shell profile, so the .env the preflight reads
    # was invisible here and every scheduled run reported "missing credentials"
    # and checked nothing -- while still exiting 0 per rule #26. An armed watch
    # that never looks reads as covered, which is worse than no watch at all.
    dotenv = Path(__file__).resolve().parent.parent / ".env"
    if dotenv.exists():
        for ln in dotenv.read_text().splitlines():
            if "=" in ln and not ln.strip().startswith("#"):
                k, _, v = ln.partition("=")
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    cid = env.get("YOUTUBE_CLIENT_ID")
    csec = env.get("YOUTUBE_CLIENT_SECRET")
    rt = env.get("FRAMEDRIFT_YOUTUBE_REFRESH_TOKEN")
    if not (cid and csec and rt):
        return None, "missing YOUTUBE_CLIENT_ID / _SECRET / FRAMEDRIFT_YOUTUBE_REFRESH_TOKEN"
    body = urllib.parse.urlencode({
        "client_id": cid, "client_secret": csec,
        "refresh_token": rt, "grant_type": "refresh_token"}).encode()
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token", data=body,
        headers={"User-Agent": UA})          # rule #25: never default urllib UA
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)["access_token"], ""
    except Exception as exc:                 # noqa: BLE001
        return None, f"token refresh failed: {type(exc).__name__}"


# What an OAuth Data API call CAN see, confirmed against the Videos resource
# documentation: status.rejectionReason (enum includes "claim" and
# "copyright"), status.uploadStatus, status.privacyStatus, and
# contentDetails.regionRestriction.blocked.
#
# What it CANNOT see: a Content ID claim that only monetises or tracks and
# leaves the video public everywhere. The Videos resource exposes no claim
# field at all; that state is visible in YouTube Studio, or through the
# Partner API, which normal channels do not have. So this watch detects
# BLOCKING claims and takedowns, and is blind to monetisation-only ones --
# which is stated in its own output rather than left for someone to assume.
_BLOCKING_REASONS = {"claim", "copyright", "legal", "trademark"}


def check_youtube(video_id: str) -> dict:
    """Real claim check for the owning channel. Never reports clean unchecked."""
    if not video_id:
        return {"platform": "youtube", "checked": False,
                "why": "no video id recorded; the reel is not published yet"}
    tok, why = _access_token()
    if not tok:
        return {"platform": "youtube", "checked": False, "why": why}
    url = ("https://www.googleapis.com/youtube/v3/videos"
           f"?part=status,contentDetails&id={urllib.parse.quote(video_id)}")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {tok}", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except Exception as exc:                 # noqa: BLE001
        return {"platform": "youtube", "checked": False,
                "why": f"videos.list failed: {type(exc).__name__}"}

    items = data.get("items") or []
    if not items:
        # Gone from the API while we hold its id: removed or made private.
        return {"platform": "youtube", "checked": True, "claim": True,
                "strike": False, "detail": "video not returned by the API — "
                "removed, or no longer visible to the owning account"}
    st = items[0].get("status", {})
    cd = items[0].get("contentDetails", {})
    blocked = (cd.get("regionRestriction") or {}).get("blocked") or []
    reason = st.get("rejectionReason")
    claim = bool(
        (reason in _BLOCKING_REASONS)
        or st.get("uploadStatus") == "rejected"
        or blocked
    )
    return {
        "platform": "youtube", "checked": True, "claim": claim,
        "strike": reason in {"copyright", "legal"},
        "upload_status": st.get("uploadStatus"),
        "privacy_status": st.get("privacyStatus"),
        "rejection_reason": reason,
        "blocked_regions": blocked[:20],
        "blind_spot": "monetisation-only Content ID claims are not exposed by "
                      "the Data API; check Studio for those",
    }


# Every piece of licensed material the reel draws on. A claim arrives against a
# POST, not against a source, so the watch has to be able to say which material
# is implicated -- and the stop rule pauses a STUDIO's fights, which means the
# studio has to be recorded alongside the URL.
SOURCES = [
    {"role": "main fight clip", "studio": "ufotable",
     "work": "Tanjiro and Giyu vs Akaza | Demon Slayer: Kimetsu no Yaiba "
             "Infinity Castle I",
     "uploader": "Crunchyroll",
     "url": "https://www.youtube.com/watch?v=HFAzgHHITVM"},
    # v22 moved the cold open to the ENGLISH DUB trailer: the body is the dub,
    # and the sub trailer also carried burned subtitles.
    {"role": "cold-open flashback", "studio": "ufotable",
     "work": "Demon Slayer -Kimetsu no Yaiba- The Movie: Mugen Train "
             "English Dub Trailer",
     "uploader": "Aniplex USA",
     "url": "https://www.youtube.com/watch?v=L9MQLhV2u2E"},
]


# ---------------------------------------------------------------------------
# The Studio step.
#
# The Data API sees BLOCKS and their territories. It does not expose a claim
# object at all -- no claimant, no asset, no match type, and no monetise-only
# or track-only claim. Measured 2026-09-26: the API reported "249 territories
# blocked" on seven uploads and could not say that the claimant was Aniplex,
# that the asset was the Infinity Castle film, or that a SECOND claim
# (RouteNote/Delar, track-only) sat on one of the beds and was invisible.
#
# So the watch has two steps and they answer different questions:
#   API    -> is it blocked, and where           (automatable, runs every day)
#   Studio -> who claimed it, on what, how       (read-only, needs a browser)
#
# This function does not drive the browser itself. It emits the exact
# read-only checklist for a Claude-in-Chrome pass and records where the answer
# goes, so a Studio read is a step with an output slot rather than a memory.
STUDIO_URL = "https://studio.youtube.com/video/{video_id}/copyright"

STUDIO_FIELDS = ("claimant", "asset_title", "match_type", "policy", "territories")


def studio_checklist(video_ids: list[str]) -> dict:
    """Read-only Studio pass. Never dispute, never delete, never appeal."""
    return {
        "mode": "READ-ONLY",
        "tool": "Claude in Chrome",
        "forbidden": ["dispute", "appeal", "delete", "trim", "replace audio",
                      "change visibility", "acknowledge"],
        "for_each": [
            {"video_id": v, "url": STUDIO_URL.format(video_id=v),
             "capture": list(STUDIO_FIELDS)}
            for v in video_ids
        ],
        "write_to": "niches/anime/edit_lane/rights/claims.yaml",
        "note": "A claim the API cannot see is still a claim. Absence of a "
                "block is not absence of a claim.",
    }


def main() -> int:
    d = load()
    now = datetime.now(UTC)
    if not d or not d.get("published_at"):
        print(f"[claim-watch] not armed: no published_at in {STATE}. "
              f"Arm it by setting published_at + the platform ids after publish.")
        return 0
    started = datetime.fromisoformat(d["published_at"])
    day = (now - started).days
    if day > int(d.get("watch_days", 14)):
        print(f"[claim-watch] window closed on day {day}; nothing to do.")
        return 0

    results = [check_youtube(d.get("ids", {}).get("youtube", ""))]
    incidents = [r for r in results if r.get("claim") or r.get("strike")]
    d.setdefault("log", []).append(
        {"at": now.isoformat(), "day": day, "results": results})
    d["last_checked"] = now.isoformat()
    save(d)

    # SOURCES is the Demon Slayer default. Overwriting the state with it made
    # the watch report ufotable material while watching an MHA reel, which is
    # the wrong stop rule printed next to the right video id.
    srcs = d.get("sources") or SOURCES
    if srcs and isinstance(srcs[0], str):
        srcs = [{"role": "source", "studio": d.get("studio", "?"),
                 "work": d.get("work", "?"), "uploader": "?", "url": u} for u in srcs]
    d["sources"] = srcs
    save(d)

    unchecked = [r for r in results if not r.get("checked")]
    print("[claim-watch] material under watch:")
    for src in srcs:
        print(f"  - {src['role']}: {src['work']} ({src['studio']}) "
              f"via {src['uploader']}  {src['url']}")
    print(f"[claim-watch] day {day}/{d.get('watch_days', 14)}  "
          f"{len(results)} platform(s), {len(unchecked)} could not be checked")
    for r in unchecked:
        print(f"  WARN  {r['platform']}: {r['why']}")
    ids = [v for v in (d.get("ids") or {}).values() if v]
    if ids:
        print("[claim-watch] Studio step (read-only, after each preflight):")
        for row in studio_checklist(ids)["for_each"]:
            print(f"  - {row['url']}")
            print(f"      capture: {', '.join(row['capture'])}")
        print("  The API step above sees blocks only. Claimant, asset and "
              "match type come from Studio, and a track-only or monetise-only "
              "claim is invisible to the API entirely.")

    if incidents:
        print("  INCIDENT: claim or strike found — per the stop rule, pause this "
              "studio's fights and take the reel down.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
