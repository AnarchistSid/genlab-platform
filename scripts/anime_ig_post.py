#!/usr/bin/env python3
"""Post the approved MHA reel to FrameDrift on Instagram — once, and only once.

THE GUARD. FrameDrift's IG has posted exactly one reel a day at ~07:00Z on every
day the pipeline ran, and the operator's instruction is that this reel "counts as
the day's one IG post". Those two facts can both be true only if the pipeline's
own post does not happen. This cannot know whether it will, so it ASKS the
account: if anything has already posted today, it declines and says so rather
than making the day's second post. Declining is exit 0 -- it is a data-side
outcome, not an incident (rule #26).

No audio_configuration is sent: the reel carries its own Japanese source audio
and attaching an Instagram track is explicitly out of scope.
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
ROOT = Path(__file__).resolve().parent.parent
STATE = Path(os.environ.get("GENLAB_IG_POST_STATE",
                            ROOT / ".runtime/anime_ig_post.json"))
REEL = Path.home() / ".claude/jobs/11e32697/tmp/mha_v3/mha_deku_vs_overhaul_v4.mp4"
SHA = "7aa4af1eb7fe617874b41071dcb9b03273a01df9239162f6eadc731ad556e324"
CAPTION = (
    "Overhaul told him his justice was small. Deku answered with a question.\n\n"
    "\U0001F3AC Footage: https://www.youtube.com/watch?v=hm-zgEdCRes — Crunchyroll\n"
    "© K. Horikoshi / Shueisha, My Hero Academia Project\n\n"
    "Japanese audio. English subtitles read from the source.\n"
    "My Hero Academia episode 76, \"Infinite 100%\".\n\n"
    "Whose line hit harder, Overhaul's or Deku's?\n\n"
    "#MyHeroAcademia #Deku #Overhaul #anime #animeedit")


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
    # graph.facebook.com ALWAYS, never graph.instagram.com (security rule).
    q = urllib.parse.urlencode({**params, "access_token": token})
    r = urllib.request.Request("https://graph.facebook.com/v21.0/%s?%s" % (path, q),
                               headers={"User-Agent": UA})
    with urllib.request.urlopen(r, timeout=45) as x:
        return json.load(x)


def posted_today(uid: str, token: str) -> list[dict]:
    d = graph("%s/media" % uid, {"fields": "id,timestamp,permalink", "limit": "8"}, token)
    today = datetime.now(UTC).date()
    out = []
    for m in d.get("data", []):
        t = datetime.fromisoformat(m["timestamp"].replace("+0000", "+00:00"))
        if t.astimezone(UTC).date() == today:
            out.append({"id": m["id"], "at": m["timestamp"], "permalink": m.get("permalink")})
    return out


def main() -> int:
    e = env()
    uid = e["FRAMEDRIFT_IG_USER_ID"]
    tok = e.get("FRAMEDRIFT_META_ACCESS_TOKEN") or e["META_ACCESS_TOKEN"]
    st = json.loads(STATE.read_text()) if STATE.exists() else {}

    if st.get("media_id"):
        print("[ig-post] already posted: %s — nothing to do" % st["media_id"])
        return 0

    import hashlib
    h = hashlib.sha256(REEL.read_bytes()).hexdigest()
    if h != SHA:
        print("[ig-post] REFUSING: reel sha256 %s is not the approved v4" % h[:16])
        return 0

    already = posted_today(uid, tok)
    if already:
        print("[ig-post] DECLINED — FrameDrift has already posted to Instagram today:")
        for m in already:
            print("    %s  %s" % (m["at"], m.get("permalink") or m["id"]))
        print("  The instruction is that this reel is the day's ONE IG post, and the daily")
        print("  pipeline post has taken the slot. Not making the day's second post.")
        print("  To proceed: hold the anime pipeline's IG publish for a day, then re-run.")
        st["declined"] = {"at": datetime.now(UTC).isoformat(), "existing": already}
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(st, indent=2))
        return 0

    sys.path.insert(0, str(ROOT / "genlab-core/src"))
    from genlab_core.platforms.instagram import InstagramClient
    from genlab_core.platforms.base import PublishPayload           # noqa: F401

    print("[ig-post] no post today; publishing the approved v4")
    client = InstagramClient(niche_id="anime")
    payload = PublishPayload(caption=CAPTION, media_paths=[str(REEL)])
    res = client.publish(payload)
    ok = getattr(res, "success", False)
    pid = getattr(res, "post_id", None) or getattr(res, "platform_post_id", None)
    print("[ig-post] success=%s post_id=%s" % (ok, pid))
    if ok and pid:
        st.update({"media_id": pid, "posted_at": datetime.now(UTC).isoformat(),
                   "sha256": h, "account": uid,
                   "note": "FrameDrift production account, per operator 2026-09-26",
                   "audio_configuration": "NONE — source audio only, by instruction"})
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(st, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
