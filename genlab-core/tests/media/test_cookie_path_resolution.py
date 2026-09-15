"""Pin: the downloader uses the cookie file that actually has cookies in it.

2026-09-15. Prod maintained two cookie files and two env vars:

    YT_DLP_COOKIES      = /opt/genlab/.youtube_cookies.txt      0 bytes, Jul 1
    YT_DLP_COOKIES_FILE = /opt/genlab/.runtime/yt_cookies.txt   7063 bytes,
                                     22 youtube.com entries, refreshed same day

`download_top_videos` read NEITHER env var. It hardcoded
`{project_root}/.youtube_cookies.txt`, which resolved to the empty one, so
`--cookies` was never passed to yt-dlp while gaming's clip_sourcer kept the
other file fresh.

Measured on the 07:29Z sports fire: 0/5 downloaded, every URL answering "Sign
in to confirm you're not a bot", VideoGate dropping all 5 stories, 0 blueprints.
Proven directly against the same URL:

    without cookies                    -> ERROR: Sign in to confirm ... not a bot
    with .runtime/yt_cookies.txt       -> Downloading 1 format(s): 399+251

The subtle part is that EXISTENCE is not the test -- the broken file exists.
Emptiness is. A resolver that stopped at the first *present* path would have
selected the empty file and changed nothing.
"""

from __future__ import annotations

import inspect

from genlab_core.media import download_top_videos as dtv

_REAL = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tPREF\tabc\n"
_ONLY_COMMENTS = "# Netscape HTTP Cookie File\n# nothing below\n"


def _resolve(candidates: list[str]) -> tuple[str, bool]:
    """The resolver as implemented in _build_ytdlp_cmd."""
    import os

    for cand in candidates:
        if not cand or not os.path.exists(cand):
            continue
        try:
            with open(cand) as fh:
                content = fh.read()
        except OSError:
            continue
        if any(line.strip() and not line.startswith("#") for line in content.splitlines()):
            return cand, True
    return "", False


def test_empty_file_is_skipped_for_a_populated_one(tmp_path) -> None:
    """The exact prod shape: first candidate exists but is empty."""
    empty = tmp_path / ".youtube_cookies.txt"
    empty.write_text("")
    real = tmp_path / "yt_cookies.txt"
    real.write_text(_REAL)
    path, ok = _resolve([str(real), str(empty)])
    assert ok and path == str(real)


def test_comment_only_file_counts_as_empty(tmp_path) -> None:
    """A Netscape header with no cookie lines authenticates nothing."""
    header_only = tmp_path / "a.txt"
    header_only.write_text(_ONLY_COMMENTS)
    real = tmp_path / "b.txt"
    real.write_text(_REAL)
    assert _resolve([str(header_only), str(real)]) == (str(real), True)


def test_missing_paths_are_skipped_not_fatal(tmp_path) -> None:
    real = tmp_path / "b.txt"
    real.write_text(_REAL)
    assert _resolve(["", "/nonexistent/x.txt", str(real)]) == (str(real), True)


def test_no_usable_cookies_returns_false(tmp_path) -> None:
    empty = tmp_path / "a.txt"
    empty.write_text("")
    assert _resolve([str(empty)]) == ("", False)


def test_source_reads_both_env_vars_and_warns_when_none_usable() -> None:
    """Guard the guard: this file reimplements the resolver, so assert the
    real one still consults the environment and still logs the miss.

    Without the warning, 'no cookies configured' and 'YouTube is blocking us'
    produce the identical downstream symptom (rule #17/#19).
    """
    src = inspect.getsource(dtv)
    for fragment in ("YT_DLP_COOKIES_FILE", "YT_DLP_COOKIES", "not a bot"):
        assert fragment in src, (
            f"{fragment!r} missing from download_top_videos — the resolver this "
            f"pin protects is no longer the one in production."
        )


class TestWarpProxyYieldsToCookies:
    """WARP and cookies solve the same problem; together they lose.

    WARP exists to defeat "Sign in to confirm you're not a bot" from a
    datacenter IP. Cookies defeat the same wall without relocating us, so once
    cookies are present WARP contributes only a foreign exit node. Measured
    2026-09-15 on the four URLs the sports fire failed to fetch:

        video          direct                    via WARP
        t7m12y_xvr0    Downloading 1 format(s)   ERROR: "The uploader has not
        owZ01TVfc-0    Downloading 1 format(s)    made this video available in
        QmzCd5GGBiw    Downloading 1 format(s)    your country"
        NK7AfP_wi8M    Downloading 1 format(s)

    4/4 direct, 0/4 proxied. Two consecutive sports fires produced zero
    blueprints on this.
    """

    @staticmethod
    def _decide(warp: str, has_cookies: bool, force: str = "",
                url: str = "https://www.youtube.com/watch?v=x") -> str:
        """The decision as implemented in _build_ytdlp_cmd."""
        host = url.split("/")[2].lower() if "://" in url else ""
        is_youtube = any(host == d or host.endswith("." + d)
                         for d in ("youtube.com", "youtu.be", "googlevideo.com"))
        if warp and has_cookies and is_youtube and force != "1":
            return ""
        return warp

    def test_warp_skipped_when_cookies_are_available(self) -> None:
        assert self._decide("socks5://127.0.0.1:40000", True, "") == ""

    def test_warp_kept_when_no_cookies(self) -> None:
        """Without cookies WARP is still the only bot-wall defence there is."""
        warp = "socks5://127.0.0.1:40000"
        assert self._decide(warp, False, "") == warp

    def test_env_set_proxy_does_NOT_defeat_the_skip(self) -> None:
        """The bug in the first cut of this fix.

        Exempting YT_DLP_PROXY as "an operator decision" silently defeated the
        whole change: prod .env already sets it to the SAME WARP address the
        implicit branch computes. Only an explicit force flag may override.
        """
        warp = "socks5://127.0.0.1:40000"
        assert self._decide(warp, True) == "", (
            "an env-configured WARP address must not count as a deliberate override"
        )

    def test_force_flag_is_the_only_override(self) -> None:
        warp = "socks5://1.2.3.4:9050"
        assert self._decide(warp, True, force="1") == warp


class TestErrorCapture:
    """Report the ERROR line, not whatever stderr starts with.

    yt-dlp leads stderr with a stale-version WARNING, so the truncated message
    was always that warning and never the failure. Every download failure in
    this pipeline was logged as a version complaint, which sent this
    investigation after a stale-version theory twice while the real causes --
    the bot wall, then a geo-blocking proxy -- stayed invisible.
    """

    @staticmethod
    def _extract(raw: str) -> str:
        errs = [ln.strip() for ln in raw.splitlines() if ln.strip().startswith("ERROR:")]
        return "; ".join(errs) if errs else raw

    def test_version_warning_does_not_mask_the_real_error(self) -> None:
        raw = (
            "WARNING: Your yt-dlp version (2026.06.06.234447) is older than 90 days!\n"
            "         It is strongly recommended to always use the latest version.\n"
            "         You installed yt-dlp with pip or using the wheel\n"
            "ERROR: [youtube] t7m12y_xvr0: The uploader has not made this video "
            "available in your country\n"
        )
        got = self._extract(raw)
        assert got.startswith("ERROR:")
        assert "available in your country" in got
        assert "older than 90 days" not in got

    def test_multiple_errors_are_all_kept(self) -> None:
        raw = "ERROR: first thing\nnoise\nERROR: second thing\n"
        assert self._extract(raw) == "ERROR: first thing; ERROR: second thing"

    def test_falls_back_to_raw_when_no_error_line(self) -> None:
        """Never return empty — a blank reason is worse than a noisy one."""
        assert self._extract("something odd happened") == "something odd happened"


class TestProxyDecisionIsPerHost:
    """The cookie jar is YouTube's; it buys nothing on another host.

    SOURCE-08 skipped WARP whenever a cookie file existed. Right for YouTube
    (WARP geo-blocks it, measured 0/4), wrong for Reddit, which blocks
    datacenter IPs outright and which WARP was the only thing defeating.

    Measured on the 11:12Z movies fire, all candidates v.redd.it:

        ERROR: [generic] Unable to download webpage: HTTP Error 403: Blocked
        0/5 downloaded, stories=0, blueprints=0

    A regression I introduced and did not see for two fires, because the runs
    in between happened to draw YouTube URLs. Host-blind rules hide behind
    whatever the input mix happens to be.
    """

    WARP = "socks5://127.0.0.1:40000"

    def test_youtube_with_cookies_skips_warp(self) -> None:
        d = TestWarpProxyYieldsToCookies._decide
        assert d(self.WARP, True, url="https://www.youtube.com/watch?v=abc") == ""
        assert d(self.WARP, True, url="https://youtu.be/abc") == ""

    def test_reddit_keeps_warp_even_with_cookies(self) -> None:
        """The regression. Reddit 403s from a datacenter IP without WARP."""
        d = TestWarpProxyYieldsToCookies._decide
        assert d(self.WARP, True, url="https://v.redd.it/5o4umbq4qlph1") == self.WARP

    def test_other_hosts_keep_warp(self) -> None:
        d = TestWarpProxyYieldsToCookies._decide
        for u in ("https://clips.twitch.tv/x", "https://www.tiktok.com/@a/video/1",
                  "https://cdn.example.com/a.mp4"):
            assert d(self.WARP, True, url=u) == self.WARP, u

    def test_lookalike_host_does_not_match(self) -> None:
        """notyoutube.com must not be treated as youtube.com."""
        d = TestWarpProxyYieldsToCookies._decide
        assert d(self.WARP, True, url="https://notyoutube.com/watch?v=x") == self.WARP

    def test_source_gates_on_host(self) -> None:
        """Guard the guard: the shipped decision must consult the URL."""
        import inspect

        from genlab_core.media import download_top_videos as dtv

        src = " ".join(inspect.getsource(dtv._download_video).split())
        assert "_is_youtube" in src and "youtu.be" in src, (
            "the WARP skip is host-blind again — it will 403 every Reddit URL"
        )
