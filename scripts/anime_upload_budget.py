#!/usr/bin/env python3
"""Daily upload budget for the channel. Check BEFORE a probe batch.

YouTube caps uploads per channel per day. On 2026-09-26 a six-title clearance
batch (12 uploads) hit `uploadLimitExceeded` after two titles, having already
spent 13 uploads that day on reel preflights and the publish. Four titles never
ran and one got only its audio variant -- which is not a clearance: CLM-011 is
the case where the audio variant read fine and the SILENT control found the
block.

The publish is reserved first. A reel costs 1 upload; a preflight or probe
costs 2, because a reading without its silent control is not a reading.

Exit 0 always (rule #26): "not enough budget" is a data-side answer, not an
incident. Read the printed verdict, or `--json`.
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from anime_preflight import UA, _env, _token          # noqa: E402

# Observed, not documented by YouTube: 13 uploads succeeded on 2026-09-26 and
# the 14th was refused. Held one below the lowest observed refusal.
OBSERVED_LIMIT = 13
RESERVE_PUBLISH = 1
COST = {"reel": 1, "preflight": 2, "probe": 2}


def _api(path: str, tok: str, **q) -> dict:
    u = "https://www.googleapis.com/youtube/v3/%s?%s" % (path, urllib.parse.urlencode(q))
    r = urllib.request.Request(u, headers={"Authorization": "Bearer %s" % tok,
                                           "User-Agent": UA})
    with urllib.request.urlopen(r, timeout=30) as x:
        return json.load(x)


def used_today(tok: str) -> int:
    ch = _api("channels", tok, part="contentDetails", mine="true")["items"][0]
    up = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    items = _api("playlistItems", tok, part="contentDetails", playlistId=up,
                 maxResults=50).get("items", [])
    ids = [i["contentDetails"]["videoId"] for i in items]
    today, n = datetime.now(UTC).date(), 0
    for k in range(0, len(ids), 50):
        for v in _api("videos", tok, part="snippet", id=",".join(ids[k:k+50])).get("items", []):
            at = datetime.fromisoformat(v["snippet"]["publishedAt"].replace("Z", "+00:00"))
            if at.astimezone(UTC).date() == today:
                n += 1
    return n


def budget(n_probes: int = 0, publish_pending: bool = True) -> dict:
    tok = _token(_env())
    used = used_today(tok)
    # Deletions do not refund the quota, so the playlist count is a FLOOR on
    # what has been spent. Treat it as such rather than as the exact figure.
    reserve = RESERVE_PUBLISH if publish_pending else 0
    free = max(OBSERVED_LIMIT - used - reserve, 0)
    want = n_probes * COST["probe"]
    fits = want <= free
    return {"used_today_floor": used, "observed_limit": OBSERVED_LIMIT,
            "reserved_for_publish": reserve, "available": free,
            "probes_requested": n_probes, "uploads_needed": want,
            "probes_that_fit": free // COST["probe"], "fits": fits,
            "verdict": ("proceed" if fits else
                        "SPREAD ACROSS DAYS — %d probe(s) fit today, %d requested"
                        % (free // COST["probe"], n_probes))}


def main() -> int:
    n = 0
    for a in sys.argv[1:]:
        if a.isdigit():
            n = int(a)
    pub = "--no-publish-reserve" not in sys.argv
    b = budget(n, pub)
    if "--json" in sys.argv:
        print(json.dumps(b, indent=2)); return 0
    print("[upload-budget] used today (floor) %d of ~%d; %d reserved for the publish"
          % (b["used_today_floor"], b["observed_limit"], b["reserved_for_publish"]))
    print("[upload-budget] available %d upload(s) = %d probe(s) at 2 each"
          % (b["available"], b["probes_that_fit"]))
    if n:
        print("[upload-budget] %s" % b["verdict"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
