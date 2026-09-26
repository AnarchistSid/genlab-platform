#!/usr/bin/env python3
"""Content ID preflight: upload variants PRIVATELY, measure, decide.

WHAT THIS CAN AND CANNOT SEE -- stated here because the decision depends on it:

  CAN   a BLOCKING claim, and the exact territory list
        (contentDetails.regionRestriction.blocked)
  CAN   a rejection (status.rejectionReason: claim | copyright | legal | trademark)
  CANNOT  a monetise-only or track-only claim. The Videos resource exposes no
          claim object at all for a non-partner channel.
  CANNOT  the claimant's identity, under any policy.

So "not blocked" means NOT BLOCKED -- it does not mean unclaimed. A monetising
claim is invisible here and has to be read in YouTube Studio. The tool says so
in its own output rather than letting a clean-looking result be mistaken for
"no claim".

Content ID also LAGS: measured on this channel, two uploads read 0 blocked
territories at t+40s and flipped to 249 at t+80s. Never conclude from one early
read; this polls until stable.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

UA = "GenLab/1.0 (+https://github.com/anarchistsid/genlab)"
API = "https://www.googleapis.com/youtube/v3/videos"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"


def _env() -> dict:
    env = {}
    for ln in Path(".env").read_text().splitlines():
        if "=" in ln and not ln.strip().startswith("#"):
            k, _, v = ln.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _token(env: dict, niche_prefix: str = "FRAMEDRIFT") -> str:
    d = urllib.parse.urlencode({
        "client_id": env["YOUTUBE_CLIENT_ID"],
        "client_secret": env["YOUTUBE_CLIENT_SECRET"],
        "refresh_token": env[f"{niche_prefix}_YOUTUBE_REFRESH_TOKEN"],
        "grant_type": "refresh_token"}).encode()
    r = urllib.request.Request("https://oauth2.googleapis.com/token", data=d,
                               headers={"User-Agent": UA})
    with urllib.request.urlopen(r, timeout=30) as x:
        return json.load(x)["access_token"]


def upload_private(path: Path, title: str, token: str) -> str:
    size = path.stat().st_size
    meta = {"snippet": {"title": title[:95],
                        "description": "Private Content ID preflight. Not for publication.",
                        "categoryId": "1"},
            "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False}}
    req = urllib.request.Request(
        f"{UPLOAD}?uploadType=resumable&part=snippet,status",
        data=json.dumps(meta).encode(),
        headers={"Authorization": f"Bearer {token}", "User-Agent": UA,
                 "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Length": str(size),
                 "X-Upload-Content-Type": "video/mp4"})
    with urllib.request.urlopen(req, timeout=60) as r:
        loc = r.headers["Location"]
    put = urllib.request.Request(loc, data=path.read_bytes(), method="PUT",
                                 headers={"Content-Type": "video/mp4",
                                          "Content-Length": str(size),
                                          "User-Agent": UA})
    with urllib.request.urlopen(put, timeout=1800) as r:
        return json.load(r)["id"]


PENDING = "pending"


def observe(vid: str, token: str, settle_s: int = 240, step: int = 45,
            max_s: int = 2400) -> dict:
    """Poll until the upload is PROCESSED and the reading has not changed for
    three consecutive polls.

    HARD RULE: a reading only counts once YouTube reports uploadStatus
    "processed". Anything earlier is `pending`, never `clear`. Measured
    2026-09-26: a Demon Slayer TV probe read 0 blocked territories for 405 s
    while still "uploaded" and flipped to 1 the moment it processed; an MHA
    probe sat unprocessed for 585 s. Reporting either of those early would have
    been a false clear on a rights question.
    """
    last, stable, hist = None, 0, []
    t = 0
    while t <= max_s:
        req = urllib.request.Request(
            f"{API}?part=status,contentDetails&id={vid}",
            headers={"Authorization": f"Bearer {token}", "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as x:
            items = json.load(x).get("items") or []
        if not items:
            return {"video_id": vid, "gone": True}
        it = items[0]
        st, cd = it["status"], it.get("contentDetails", {})
        blocked = (cd.get("regionRestriction") or {}).get("blocked") or []
        cur = (st.get("uploadStatus"), st.get("rejectionReason"), len(blocked))
        hist.append({"t": t, "upload": cur[0], "reject": cur[1], "blocked": cur[2]})
        if cur[0] == "processed":
            stable = stable + 1 if cur == last else 1
            if stable >= 3 and t >= settle_s:
                break
        else:
            stable = 0
        last = cur
        time.sleep(step)
        t += step
    return {"video_id": vid, "upload_status": st.get("uploadStatus"),
            "rejection_reason": st.get("rejectionReason"),
            "blocked_count": len(blocked), "blocked_territories": blocked,
            "privacy": st.get("privacyStatus"), "history": hist,
            "claimant": None,
            "claimant_note": "not exposed by the Data API under any policy; "
                             "read it in YouTube Studio",
            "processed": st.get("uploadStatus") == "processed",
            "stable_polls": stable,
            "reading_valid": st.get("uploadStatus") == "processed" and stable >= 3,
            "policy_observed": (
                PENDING if not (st.get("uploadStatus") == "processed" and stable >= 3)
                else "block" if blocked or st.get("rejectionReason") in
                {"claim", "copyright", "legal", "trademark"}
                else "no_block_detected"),
            "monetise_status": "UNKNOWN — the Data API cannot see a "
                               "monetise-only claim"}


def decide(source_audio: dict, captions_only: dict, policy: str) -> dict:
    """The per-reel decision. `policy` comes from the pack."""
    if PENDING in (source_audio["policy_observed"], captions_only["policy_observed"]):
        return {"publish": "HOLD", "pack_policy": policy,
                "reason": "a variant is still PENDING — the upload has not "
                          "processed, or its reading has not settled. Pending "
                          "is never clear; re-run rather than publish."}
    sa_block = source_audio["policy_observed"] == "block"
    co_block = captions_only["policy_observed"] == "block"
    if policy == "none":
        chosen, why = "captions_only", "pack policy is `none`: never ship source audio"
    elif sa_block:
        chosen, why = "captions_only", (
            f"source-audio variant is BLOCKED in "
            f"{source_audio['blocked_count']} territories")
    elif policy == "allow_monetised":
        chosen, why = "source_audio", (
            "no block detected and the pack allows monetised claims — note the "
            "API cannot confirm whether a monetising claim exists")
    else:                                   # auto
        chosen, why = "source_audio", (
            "no block detected; pack policy `auto`. A monetise-only claim would "
            "be invisible here — confirm in Studio before relying on revenue")
    if chosen == "captions_only" and co_block:
        chosen, why = "BLOCKED_BOTH", (
            "both variants are blocked — the block is not the source audio; "
            f"captions-only is blocked in {captions_only['blocked_count']} "
            "territories too")
    return {"publish": chosen, "reason": why, "pack_policy": policy}


def main() -> int:
    if len(sys.argv) < 4:
        print("usage: anime_preflight.py <source_audio.mp4> <captions_only.mp4> "
              "<policy: auto|none|allow_monetised> [--label TEXT]")
        return 0
    sa, co, policy = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    label = sys.argv[sys.argv.index("--label") + 1] if "--label" in sys.argv else "preflight"
    env = _env()
    tok = _token(env)
    out = {}
    for key, p in (("source_audio", sa), ("captions_only", co)):
        vid = upload_private(p, f"[PRIVATE PREFLIGHT] {label} — {key}", tok)
        print(f"  uploaded {key}: {vid}")
        out[key] = observe(vid, tok)
        print(f"    -> {out[key]['policy_observed']}, "
              f"{out[key]['blocked_count']} territories blocked")
    out["decision"] = decide(out["source_audio"], out["captions_only"], policy)
    print(f"\n  DECISION: publish {out['decision']['publish']}")
    print(f"  because: {out['decision']['reason']}")
    Path(f"preflight_{label}.json").write_text(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
