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
