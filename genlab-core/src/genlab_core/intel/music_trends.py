"""ANIME-PEAK-10 §2 — trending sound as a DATA SOURCE, not a model.

Inference generates; it does not discover. What is trending comes from public
charts, and this module fetches them so §3's prompts and §4's style reference
can be conditioned on the week's actual sound rather than on a guess.

WHAT IS AND IS NOT USED
    Chart metadata -- names, artists, tempo, usage counts -- conditions the
    prompts. A 30 s preview, where an API offers one, is read for ANALYSIS
    and may be passed as a style REFERENCE to a generator that accepts one.
    A preview is never composited into a reel.

REACHABILITY, MEASURED 2026-09-25
    Neither named source works from a script today:

    * TikTok Creative Center -- the JSON endpoints under
      ``/creative_radar_api/v1/`` return HTTP 200 with
      ``{"code":40101,"msg":"no permission"}``. They require signature
      headers the Creative Center web app mints per session. The public page
      itself is client-rendered: 21 KB of shell with no chart data in it, so
      parsing the HTML returns nothing either. It needs either browser
      automation against the operator's session, or a TikTok for Business
      API credential.
    * Spotify -- needs a registered app. ``SPOTIFY_CLIENT_ID`` and
      ``SPOTIFY_CLIENT_SECRET`` are both unset. Note also that Spotify
      restricted the ``audio-features`` endpoint (the source of BPM) for
      apps registered after 2024-11; whether tempo is available has to be
      checked once a credential exists rather than assumed.

Both paths are implemented. ``status()`` reports what is actually usable so a
caller never mistakes an empty result for "nothing is trending".
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_CFG_NAME = "music_trend_sources.yaml"


class SourceUnavailable(RuntimeError):
    """Raised with the reason, so a caller cannot read it as 'no trends'."""


@dataclass(frozen=True)
class TrendingSound:
    name: str
    artist: str
    source: str
    bpm: float | None = None
    sub_genre: str | None = None
    duration_s: float | None = None
    preview_url: str | None = None
    usage: int | None = None


def config() -> dict:
    import yaml

    here = Path(__file__).resolve()
    for base in (here.parents[2], here.parents[3], here.parents[4]):
        p = base / "config" / _CFG_NAME
        if p.exists():
            return yaml.safe_load(p.read_text())
    raise FileNotFoundError(_CFG_NAME)


def _get(url: str, headers: dict, timeout: int = 30) -> bytes:
    # Rule #25: never call urllib without an explicit User-Agent.
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _ua(cfg: dict) -> dict:
    return {"User-Agent": cfg["user_agent"]}


def fetch_tiktok(cfg: dict | None = None, region: str = "US",
                 limit: int = 20) -> list[TrendingSound]:
    cfg = cfg or config()
    s = cfg["sources"]["tiktok_creative_center"]
    url = (f"{s['url']}?page=1&limit={limit}&period={s['period_days']}"
           f"&rank_type=popular&country_code={region}")
    try:
        body = json.loads(_get(url, _ua(cfg)))
    except urllib.error.HTTPError as e:
        raise SourceUnavailable(f"tiktok HTTP {e.code}") from e
    if body.get("code") not in (0, None):
        raise SourceUnavailable(
            f"tiktok returned code {body.get('code')}: {body.get('msg')!r}. "
            "The Creative Center endpoints need session signature headers; "
            "use browser automation or a TikTok for Business credential.")
    out = []
    for row in (body.get("data", {}) or {}).get("sound_list", []):
        out.append(TrendingSound(
            name=row.get("title", ""), artist=row.get("author", ""),
            source="tiktok_creative_center", usage=row.get("user_count"),
            duration_s=row.get("duration"), preview_url=row.get("song_url")))
    return out


def _spotify_token(cfg: dict) -> str:
    s = cfg["sources"]["spotify"]
    cid = os.getenv(s["credential"]["client_id_env"])
    sec = os.getenv(s["credential"]["client_secret_env"])
    if not cid or not sec:
        raise SourceUnavailable(
            f"spotify needs {s['credential']['client_id_env']} and "
            f"{s['credential']['client_secret_env']}; register an app at "
            "developer.spotify.com. Not created here -- account creation is "
            "the operator's to do.")
    auth = base64.b64encode(f"{cid}:{sec}".encode()).decode()
    req = urllib.request.Request(
        s["token_url"], data=b"grant_type=client_credentials",
        headers={**_ua(cfg), "Authorization": f"Basic {auth}",
                 "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["access_token"]


def fetch_spotify(cfg: dict | None = None, limit: int = 25) -> list[TrendingSound]:
    cfg = cfg or config()
    s = cfg["sources"]["spotify"]
    tok = _spotify_token(cfg)
    hdr = {**_ua(cfg), "Authorization": f"Bearer {tok}"}
    out: list[TrendingSound] = []
    for query in s["playlist_queries"]:
        q = urllib.parse.quote(query)
        res = json.loads(_get(
            f"https://api.spotify.com/v1/search?q={q}&type=playlist&limit=1", hdr))
        items = (res.get("playlists") or {}).get("items") or []
        if not items:
            logger.warning("[trends] no playlist matched %r", query)
            continue
        pid = items[0]["id"]
        tracks = json.loads(_get(
            f"https://api.spotify.com/v1/playlists/{pid}/tracks?limit={limit}", hdr))
        ids = [t["track"]["id"] for t in tracks.get("items", [])
               if t.get("track") and t["track"].get("id")]
        feats = {}
        if ids:
            try:
                fr = json.loads(_get(
                    "https://api.spotify.com/v1/audio-features?ids=" + ",".join(ids), hdr))
                feats = {f["id"]: f for f in (fr.get("audio_features") or []) if f}
            except urllib.error.HTTPError as e:
                # Restricted for apps registered after 2024-11. Not fatal:
                # the chart is still useful without tempo, and tempo can be
                # measured from the preview instead.
                logger.warning("[trends] audio-features unavailable (HTTP %s); "
                               "tempo will have to come from the preview", e.code)
        for t in tracks.get("items", []):
            tr = t.get("track") or {}
            if not tr.get("id"):
                continue
            f = feats.get(tr["id"], {})
            out.append(TrendingSound(
                name=tr.get("name", ""),
                artist=", ".join(a["name"] for a in tr.get("artists", [])),
                source=f"spotify:{query}", bpm=f.get("tempo"), sub_genre=query,
                duration_s=(tr.get("duration_ms") or 0) / 1000.0,
                preview_url=tr.get("preview_url")))
    return out


def status(cfg: dict | None = None) -> dict[str, str]:
    """What is actually usable right now, and why not when it is not."""
    cfg = cfg or config()
    out = {}
    for name, fn in (("tiktok_creative_center", fetch_tiktok), ("spotify", fetch_spotify)):
        if not cfg["sources"][name].get("enabled", True):
            out[name] = "disabled in config"
            continue
        try:
            rows = fn(cfg)
            out[name] = f"ok, {len(rows)} sounds"
        except SourceUnavailable as e:
            out[name] = f"unavailable: {e}"
        except Exception as e:  # noqa: BLE001 - a status call must not raise
            out[name] = f"error: {type(e).__name__}: {str(e)[:120]}"
    return out


def write_trending_sounds(dest: Path, cfg: dict | None = None) -> dict:
    """Write trending_sounds.json, recording which sources answered."""
    cfg = cfg or config()
    sounds: list[TrendingSound] = []
    src_status = {}
    for name, fn in (("tiktok_creative_center", fetch_tiktok), ("spotify", fetch_spotify)):
        try:
            rows = fn(cfg)
            sounds.extend(rows)
            src_status[name] = f"ok, {len(rows)}"
        except SourceUnavailable as e:
            src_status[name] = f"unavailable: {e}"
    payload = {"sources": src_status, "sounds": [asdict(s) for s in sounds]}
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=1))
    return payload
