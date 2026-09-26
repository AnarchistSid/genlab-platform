#!/usr/bin/env python3
"""Is this video's channel owned by a rights holder or licensed distributor?

Matches on HANDLE or channel id, never on display name. A display name is a
label the uploader controls; a handle is an identifier YouTube controls.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parents[1] / "niches/anime/edit_lane/rights"


def load() -> dict:
    return yaml.safe_load((BASE / "allowlist.yaml").read_text())


def resolve(url_or_id: str) -> dict:
    """Ask yt-dlp for the channel's stable identifiers."""
    r = subprocess.run(
        ["yt-dlp", "--no-warnings", "--skip-download", "--print",
         "%(channel)s\t%(uploader_id)s\t%(channel_id)s\t%(title)s\t%(duration)s",
         url_or_id], capture_output=True, text=True, timeout=120)
    line = (r.stdout or "").strip().splitlines()
    if not line:
        return {"error": (r.stderr or "no output")[-160:]}
    ch, uid, cid, title, dur = (line[0].split("\t") + [""] * 5)[:5]
    return {"channel": ch, "handle": uid, "channel_id": cid,
            "title": title, "duration": dur}


def check(url_or_id: str) -> dict:
    al = load()
    info = resolve(url_or_id)
    if "error" in info:
        return {"allowed": False, "reason": f"could not resolve: {info['error']}"}
    handle = (info.get("handle") or "").strip()
    cid = (info.get("channel_id") or "").strip()
    for e in al["entities"]:
        hs = {h.lower() for h in e.get("handles", [])}
        if handle.lower() not in hs and cid not in e.get("channel_ids", []):
            continue
        st = e.get("status", "active")
        if st != "active":
            # A listed entity is not an allowed one. Listing records a
            # DECISION, including a rejection -- matching on the handle and
            # ignoring the status would have let Sony Pictures India through
            # the moment it was written down.
            return {"allowed": False, "entity": e["id"], "status": st,
                    "handle": handle, "channel": info.get("channel"),
                    "title": info.get("title"),
                    "reason": f"entity {e['id']!r} is listed with status {st}"}
        return {"allowed": True, "entity": e["id"], "role": e["role"],
                "status": st, "handle": handle,
                "licensed_titles": e.get("licensed_titles"),
                "channel": info.get("channel"), "title": info.get("title"),
                "duration": info.get("duration")}
    return {"allowed": False, "handle": handle, "channel": info.get("channel"),
            "title": info.get("title"),
            "reason": f"handle {handle!r} is not owned by any listed rights "
                      f"holder or licensed distributor"}


def main() -> int:
    for a in sys.argv[1:]:
        d = check(a)
        mark = "ALLOWED" if d["allowed"] else "REJECTED"
        extra = (f"{d.get('entity')} ({d.get('role')}"
                 + (f", {d['status']}" if d.get("status") != "active" else "") + ")"
                 if d["allowed"] else d["reason"])
        print(f"  {mark:<9} {d.get('handle','?'):<26} {extra}")
        if d.get("title"):
            print(f"            {d['title'][:70]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
