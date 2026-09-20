"""State lives in one place, and that place is a variable.

Six files built `Path.home() / ".genlab"` themselves, so "where does state live"
had six answers and no way to change them together — which is what made moving
the checkout impossible without touching a YouTube quota counter and a token
file that are written every day.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

from genlab_core.paths import ENV_STATE_DIR, state_dir

ROOT = Path(__file__).resolve().parents[2]


def test_the_default_is_unchanged(monkeypatch):
    """Prod's `genlab` user has HOME=/opt/genlab, so ~/.genlab is ALREADY inside
    the checkout and holds live state. Repointing the default would migrate it;
    this module exists to make the path overridable, not to move it."""
    monkeypatch.delenv(ENV_STATE_DIR, raising=False)
    assert state_dir() == Path.home() / ".genlab"


def test_the_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_STATE_DIR, str(tmp_path / "s"))
    assert state_dir() == tmp_path / "s"
    assert state_dir("youtube_quota.json") == tmp_path / "s" / "youtube_quota.json"


def test_create_makes_the_directory_for_a_file(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_STATE_DIR, str(tmp_path / "s"))
    p = state_dir("health", "report.json", create=True)
    assert p.parent.is_dir() and not p.exists()


def test_create_makes_a_bare_directory(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_STATE_DIR, str(tmp_path / "s"))
    assert state_dir("content_memory", create=True).is_dir()


def test_nothing_builds_the_path_by_hand_any_more():
    """The pin that matters. A new `Path.home() / ".genlab"` anywhere is a
    seventh answer to a question that now has one."""
    out = subprocess.run(
        ["git", "grep", "-n", "-e", '.genlab"', "--", "scripts", "genlab-core/src"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    ).stdout
    offenders = [
        ln
        for ln in out.splitlines()
        if ("Path.home()" in ln or "expanduser" in ln) and "genlab_core/paths.py" not in ln
    ]
    assert not offenders, "built by hand instead of state_dir():\n" + "\n".join(offenders)


def test_every_consumer_imports_the_resolver():
    """A file that calls state_dir() without importing it fails at runtime, in a
    script that runs on a timer — where nobody is watching."""
    bad = []
    for p in list((ROOT / "scripts").glob("*.py")) + list(
        (ROOT / "genlab-core/src/genlab_core").rglob("*.py")
    ):
        src = p.read_text(encoding="utf-8", errors="ignore")
        if "state_dir(" not in src or p.name == "paths.py":
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            # A file that does not parse is a different test's problem; this one
            # must not fail for it, or the real finding is buried.
            continue
        imported = any(
            isinstance(n, ast.ImportFrom)
            and n.module == "genlab_core.paths"
            and any(a.name == "state_dir" for a in n.names)
            for n in ast.walk(tree)
        )
        if not imported:
            bad.append(str(p.relative_to(ROOT)))
    assert not bad, bad
