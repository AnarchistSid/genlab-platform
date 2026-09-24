"""Everything of one SHOW that we may use, gathered once and cached.

ANIME-17 §1. v5's reel A was 67% generated stills built from a single 101-second
PV: four correct filters left three usable windows, and the grammar did what it
was told with what it had. The filters were the second problem. The first is
that one PV is not a bundle.

The rule that makes this safe is the same one the fetcher has always had:
material is CARRIED, with provenance, from sources we can name. Here that means
an explicit channel allowlist per show — the trailer's own uploader plus the
standing list of anime publishers — and nothing else. A PV from a random
uploader is someone's re-upload, and re-uploads are how a rights problem enters
a pipeline that believes it is using official material.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

#: Channels that publish official anime promotional material. The seed
#: uploader (whoever posted the AniList trailer) is added per show.
STANDING_CHANNELS = {
    "aniplex",
    "aniplexus",
    "aniplexofamerica",
    "kadokawaanime",
    "kadokawa",
    "tohoanimation",
    "crunchyroll",
    "crunchyrolldubs",
    "muse asia",
    "museasia",
    "ani-one",
    "anione",
    "bandainamcoarts",
    "pony canyon",
    "ponycanyon",
    "shueisha",
    "shonenjump",
    "tvtokyo",
    "mappa",
    "ufotable",
    "bones",
    "wit studio",
    "cloverworks",
    "a-1 pictures",
    "davidproduction",
    "studio hibari",
    "sentai filmworks",
    "funimation",
    "netflix anime",
    "hidive",
    "kodansha",
    "medialink",
    "remow",
    # Japanese broadcaster and publisher channels. Without these the gate
    # rejected 【フジテレビ】アニメ公式チャンネル — which is where the AniList
    # trailer for Firefly Wedding is actually hosted. An allowlist written
    # only in English cannot allow a Japanese official channel.
    "フジテレビ",
    "アニメ公式チャンネル",
    "アニプレックス",
    "東宝",
    "バンダイナムコ",
    "ポニーキャニオン",
    "集英社",
    "講談社",
    "テレビ東京",
    "mbs",
    "tbs",
    "avex pictures",
    "エイベックス",
    "warner bros japan",
    "ワーナー",
    "松竹",
    "shochiku",
}

#: A search for "<title> 次回予告" returns previews for OTHER shows. The
#: video's title must actually name this show.
MIN_TITLE_OVERLAP = 0.60

#: The query shapes an official PV actually appears under.
QUERY_SUFFIXES = (
    "PV",
    "本PV",
    "PV第2弾",
    "character PV",
    "キャラクターPV",
    "OP",
    "ED",
    "予告",
    "次回予告",
    "trailer",
    "teaser",
)

#: §1 gate.
MIN_VIDEO_SOURCES = 3
MIN_CANDIDATE_WINDOWS = 40


@dataclass
class Asset:
    kind: str  # pv | preview | cover | banner | character | still
    url: str
    uploader: str = ""
    uploader_id: str = ""
    title: str = ""
    path: str = ""
    sha256: str = ""
    duration_s: float = 0.0
    provenance: str = ""
    text_bands: dict = field(default_factory=dict)


@dataclass
class Bundle:
    show: str
    assets: list[Asset] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    thin_reason: str = ""

    @property
    def videos(self) -> list[Asset]:
        return [a for a in self.assets if a.kind in ("pv", "preview")]

    @property
    def images(self) -> list[Asset]:
        return [a for a in self.assets if a.kind in ("cover", "banner", "character", "still")]

    def is_thin(self, candidate_windows: int) -> bool:
        return not (
            len(self.videos) >= MIN_VIDEO_SOURCES or candidate_windows >= MIN_CANDIDATE_WINDOWS
        )


def _norm(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum() or ch == " ").strip()


def names_the_show(video_title: str, titles: list[str] | str) -> bool:
    """Whether the video title names this show, in ANY of its titles.

    Two failures this has to survive at once:

    * A query of "<title> 次回予告" matched a KADOKAWA preview for a completely
      different series. The allowlist says the uploader is official; it says
      nothing about WHICH show the video is for.
    * Checking only the ENGLISH title rejected the show's own Japanese
      broadcaster upload, whose title is entirely in Japanese. AniList gives
      english, romaji AND native; all three are this show's name.
    """
    if isinstance(titles, str):
        titles = [titles]
    vt = _norm(video_title)
    if not vt:
        return False
    for show in titles:
        st = _norm(show)
        if not st:
            continue
        if st in vt:
            return True
        tokens = [t for t in st.split() if len(t) > 2]
        if tokens and sum(1 for t in tokens if t in vt) / len(tokens) >= MIN_TITLE_OVERLAP:
            return True
    return False


def allowed(uploader: str, seed_uploaders: set[str]) -> bool:
    """Official channels only. A re-upload is not official material."""
    u = _norm(uploader)
    if not u:
        return False
    # SUBSTRING, not equality. YouTube renders a collaboration as
    # "【フジテレビ】アニメ公式チャンネル and 2 more", so an exact match against
    # the seed rejected the show's own trailer channel.
    for seed in (_norm(x) for x in seed_uploaders if x):
        if seed and (seed in u or u in seed):
            return True
    return any(c in u or u in c for c in (_norm(x) for x in STANDING_CHANNELS))


def seed_from_trailer(trailer_id: str, *, ytdlp: str = "yt-dlp") -> str:
    """The uploader of the show's own AniList trailer, as an allowlist seed.

    The catalogue points at this video for this show, so whoever hosts it is
    official FOR THIS SHOW even when the channel is not on the standing list.
    """
    if not trailer_id:
        return ""
    r = subprocess.run(
        [
            ytdlp,
            "--no-warnings",
            "--skip-download",
            "--print",
            "%(channel)s",
            f"https://www.youtube.com/watch?v={trailer_id}",
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    return (r.stdout or "").strip().splitlines()[-1] if r.stdout.strip() else ""


def search_official(
    titles: list[str] | str,
    seed_uploaders: set[str],
    *,
    ytdlp: str = "yt-dlp",
    per_query: int = 6,
    max_results: int = 12,
) -> list[Asset]:
    """Candidate official videos for this show, by query shape then allowlist.

    ``titles`` is every name AniList knows for the show — english, romaji and
    native. Searching only the English one misses the Japanese uploads, which
    are the ones with the clean non-telop footage.
    """
    if isinstance(titles, str):
        titles = [titles]
    titles = [t for t in titles if t]
    title = titles[0]
    seen: dict[str, Asset] = {}
    rejected: list[dict] = []
    queries = [(t, sfx) for t in titles[:2] for sfx in QUERY_SUFFIXES]
    for _t, suffix in queries:
        if len(seen) >= max_results:
            break
        q = f"{_t} {suffix}"
        r = subprocess.run(
            [ytdlp, "--no-warnings", "--flat-playlist", "-J", f"ytsearch{per_query}:{q}"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        try:
            entries = json.loads(r.stdout or "{}").get("entries") or []
        except ValueError:
            continue
        for e in entries:
            vid = e.get("id")
            if not vid or vid in seen:
                continue
            up = e.get("channel") or e.get("uploader") or ""
            if not allowed(up, seed_uploaders):
                rejected.append({"id": vid, "uploader": up, "reason": "not an official channel"})
                continue
            if not names_the_show(e.get("title") or "", titles):
                rejected.append(
                    {
                        "id": vid,
                        "uploader": up,
                        "title": (e.get("title") or "")[:70],
                        "reason": "official channel, but not this show",
                    }
                )
                continue
            kind = "preview" if suffix in ("予告", "次回予告") else "pv"
            seen[vid] = Asset(
                kind=kind,
                url=f"https://www.youtube.com/watch?v={vid}",
                uploader=up,
                uploader_id=e.get("channel_id") or "",
                title=(e.get("title") or "")[:120],
                duration_s=float(e.get("duration") or 0.0),
                provenance=f"youtube search {q!r}, uploader on the show allowlist",
            )
    logger.info(
        "[bundle] %s: %d official candidates, %d rejected as unofficial",
        title,
        len(seen),
        len(rejected),
    )
    for r in rejected[:6]:
        logger.info("[bundle]   rejected %s (%s)", r["id"], r["uploader"][:40])
    return list(seen.values())


def sha_of(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def build(show: str, story: dict, work: Path, *, downloader, max_videos: int = 5) -> Bundle:
    """Assemble and cache the bundle. ``downloader(url, dest) -> Path``."""
    work.mkdir(parents=True, exist_ok=True)
    cache = work / "bundle.json"
    b = Bundle(show=show)

    seed = {story.get("trailer_uploader", ""), *(story.get("studios") or [])}
    seed.add(seed_from_trailer(story.get("trailer_id", "")))
    seed.discard("")
    logger.info("[bundle] %s: allowlist seeds %s", show, sorted(seed))
    cands = search_official(story.get("titles") or [show], seed)
    # The AniList trailer itself is always in, allowlist or not: it is the
    # link the catalogue gives us for this show.
    tid = story.get("trailer_id")
    if tid and not any(tid in a.url for a in cands):
        cands.insert(
            0,
            Asset(
                kind="pv",
                url=f"https://www.youtube.com/watch?v={tid}",
                uploader=story.get("trailer_uploader", "(AniList trailer)"),
                provenance="AniList Media.trailer",
            ),
        )
    for a in cands[:max_videos]:
        dest = work / f"{a.kind}_{a.url.rsplit('=', 1)[-1]}.mp4"
        try:
            p = downloader(a.url, dest)
        except Exception as exc:  # noqa: BLE001
            b.rejected.append({"url": a.url, "reason": f"download failed: {exc}"[:120]})
            continue
        a.path, a.sha256 = str(p), sha_of(p)
        b.assets.append(a)

    for key, kind in (("cover_image_url", "cover"), ("banner_image_url", "banner")):
        if story.get(key):
            b.assets.append(
                Asset(kind=kind, url=story[key], provenance=story.get("art_provenance", "AniList"))
            )
    for c in story.get("characters") or []:
        b.assets.append(
            Asset(
                kind="character",
                url=c["image_url"],
                title=c.get("name", ""),
                provenance=story.get("art_provenance", "AniList"),
            )
        )

    cache.write_text(
        json.dumps(
            {"show": show, "assets": [asdict(a) for a in b.assets], "rejected": b.rejected},
            indent=2,
        )
    )
    logger.info(
        "[bundle] %s: %d videos, %d images, %d rejected",
        show,
        len(b.videos),
        len(b.images),
        len(b.rejected),
    )
    return b
