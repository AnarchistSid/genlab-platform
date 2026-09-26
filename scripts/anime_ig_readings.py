#!/usr/bin/env python3
"""Readings on the FrameDrift Instagram post: +1 h, +24 h, +48 h.

WHAT THE GRAPH API CAN AND CANNOT SEE — stated here because the stop rule
depends on it, and a clean API read is not the same as a clean post:

  CAN   the media still exists, its permalink, type, timestamp
  CAN   reach and plays, via /{media-id}/insights
  CANNOT  whether Instagram has MUTED the audio. No field exposes it.
  CANNOT  regional restrictions on the media. No field exposes it.
  CANNOT  account-status warnings or strikes. Account Status lives in the app.

So this reports what it can measure and NAMES the rest as app checks rather
than letting silence read as "fine". Exit 0 always except on a measurable
incident (rule #26): media gone, or reach collapsed to zero after it had some.
"""
from __future__ import annotations

import json
import os
import statistics
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

UA = "GenLab/1.0 (+https://github.com/anarchistsid/genlab)"
ROOT = Path(__file__).resolve().parent.parent
POST_STATE = ROOT / ".runtime/anime_ig_post.json"
STATE = ROOT / ".runtime/anime_ig_readings.json"
APP_CHECKS = [
    "Audio: is the reel's audio MUTED? (Reel > ... > the app shows a copyright notice)",
    "Regional restrictions: Settings > Account Status > any 'limited in some regions'",
    "Account Status: Settings > Account Status — any warning, strike or feature limit",
    "Rights Manager: any match reported against this media",
]


def env() -> dict:
    e = dict(os.environ)
    f = ROOT / ".env"
    if f.exists():
        for ln in f.read_text().splitlines():
            if "=" in ln and not ln.strip().startswith("#"):
                k, _, v = ln.partition("=")
                e.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return e


def graph(path: str, params: dict, token: str) -> dict:
    q = urllib.parse.urlencode({**params, "access_token": token})
    r = urllib.request.Request("https://graph.facebook.com/v21.0/%s?%s" % (path, q),
                               headers={"User-Agent": UA})
    with urllib.request.urlopen(r, timeout=45) as x:
        return json.load(x)


def reach_of(mid: str, token: str) -> int | None:
    try:
        d = graph("%s/insights" % mid, {"metric": "reach"}, token)
        for m in d.get("data", []):
            vals = m.get("values") or []
            if vals:
                return int(vals[0].get("value", 0))
    except Exception:                                     # noqa: BLE001
        return None
    return None


def main() -> int:
    if not POST_STATE.exists():
        print("[ig-readings] nothing posted yet; nothing to read.")
        return 0
    ps = json.loads(POST_STATE.read_text())
    mid = ps.get("media_id")
    if not mid:
        print("[ig-readings] the post was declined or never made (%s); nothing to read."
              % ("declined " + ps["declined"]["at"] if ps.get("declined") else "no media_id"))
        return 0

    e = env()
    uid = e["FRAMEDRIFT_IG_USER_ID"]
    tok = e.get("FRAMEDRIFT_META_ACCESS_TOKEN") or e["META_ACCESS_TOKEN"]
    posted = datetime.fromisoformat(ps["posted_at"])
    hours = (datetime.now(UTC) - posted).total_seconds() / 3600
    st = json.loads(STATE.read_text()) if STATE.exists() else {"readings": []}

    gone, media = False, {}
    try:
        media = graph(mid, {"fields": "id,media_type,media_product_type,permalink,"
                                      "timestamp,is_comment_enabled,like_count,comments_count"}, tok)
    except Exception as exc:                              # noqa: BLE001
        gone = True
        media = {"error": "%s: %s" % (type(exc).__name__, str(exc)[:120])}

    reach = None if gone else reach_of(mid, tok)

    # channel median reach, from the posts before this one
    med, sample = None, []
    try:
        recent = graph("%s/media" % uid, {"fields": "id,timestamp", "limit": "15"}, tok)
        for m in recent.get("data", []):
            if m["id"] == mid:
                continue
            r = reach_of(m["id"], tok)
            if r:
                sample.append(r)
        if sample:
            med = statistics.median(sample)
    except Exception:                                     # noqa: BLE001
        pass

    row = {"at": datetime.now(UTC).isoformat(), "hours_since_post": round(hours, 2),
           "media_present": not gone, "media": media, "reach": reach,
           "channel_median_reach": med, "median_sample_n": len(sample),
           "reach_vs_median": (round(reach / med, 3) if (reach and med) else None),
           "api_cannot_see": ["audio_muted", "regional_restrictions", "account_status"],
           "app_checks": APP_CHECKS}
    st["readings"].append(row)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=2))

    print("[ig-readings] +%.1f h  media=%s  reach=%s  median=%s  ratio=%s"
          % (hours, "present" if not gone else "GONE", reach, med, row["reach_vs_median"]))
    print("  permalink: %s" % media.get("permalink", "-"))
    print("  the API cannot see: audio mute, regional restriction, account status.")
    print("  CHECK IN THE APP:")
    for c in APP_CHECKS:
        print("    - %s" % c)

    if gone:
        print("  INCIDENT: the media is no longer retrievable. Stop rule: pause all anime")
        print("  posts on Instagram and take the post down. Report.")
        return 1
    prev = [r for r in st["readings"][:-1] if r.get("reach")]
    if reach == 0 and prev:
        print("  INCIDENT: reach collapsed to zero after being non-zero — treat as a")
        print("  restriction until the app says otherwise. Stop rule applies.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
