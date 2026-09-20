"""Pin: every caller of ``check_pre_render_quality`` passes ``title=``.

Rules 4 (``hook_equals_title``) and 5 (``hook_title_truncation``) in
``pre_render_quality`` only activate when the caller supplies ``title``.
The parameter defaults to ``""`` so legacy callers keep working — which
means omitting it is not an error, produces no warning, and silently
downgrades the gate from five rules to three.

That is exactly what happened. ``base_visual_render`` passed ``title``;
the two niches with their own render paths did not:

    BlackboxBrief/bb_strategies/visual_render.py
    CriticalRush/niches/gaming/stages/render_gaming_video.py

Measured 2026-09-20 over 90 days of production blueprints — hooks equal
to the source title verbatim, and how many of them reached a render:

    niche         verbatim   rendered   rules 4/5
    ai_creators         68         21   dead
    gaming              54         16   dead
    movies              52          0   live
    anime               30          0   live
    sports              23          1   live   (the 1 predates the rule)

37 reels shipped with the source title as the hook, including
``League of Legends`` and ``Apple v. OpenAI #Vergecast`` — the same
``Grand Theft Auto V`` shape the module was written to stop. The other
three niches caught all 82 of theirs and held them at DRAFTED.

This pin is AST-based rather than a behavioural test of the two render
paths, because the recurrence shape is *a new niche adding its own
render path* — a behavioural test only covers call sites that already
exist. Walking the source catches the next one before it ships.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]

_FUNC = "check_pre_render_quality"

# Directories that are not production call sites.
_SKIP_PARTS = {".venv", "node_modules", "__pycache__", ".git", "build", "dist"}


def _python_files() -> list[Path]:
    out = []
    for p in _REPO_ROOT.rglob("*.py"):
        if _SKIP_PARTS & set(p.parts):
            continue
        out.append(p)
    return out


def _call_sites() -> list[tuple[Path, int, ast.Call]]:
    """Every call to ``check_pre_render_quality`` outside its own module/tests."""
    sites: list[tuple[Path, int, ast.Call]] = []
    for path in _python_files():
        if path.name.startswith("test_") or path.name == "pre_render_quality.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = (
                fn.id
                if isinstance(fn, ast.Name)
                else fn.attr
                if isinstance(fn, ast.Attribute)
                else None
            )
            if name == _FUNC:
                sites.append((path, node.lineno, node))
    return sites


def test_call_sites_are_discoverable():
    """Guard the guard: if this finds nothing, every other test here is vacuous.

    A rename or an import-style change would otherwise turn the pin below
    into a test that passes by finding zero call sites.
    """
    sites = _call_sites()
    assert len(sites) >= 3, (
        f"Expected at least 3 production call sites for {_FUNC}, found {len(sites)}. "
        "Either the function was renamed, or this pin has stopped matching — "
        "fix the pin, do not delete it."
    )


def test_every_call_site_passes_title():
    """No caller may omit ``title=``, or rules 4 and 5 go dark for that niche."""
    offenders = []
    for path, lineno, node in _call_sites():
        kwargs = {kw.arg for kw in node.keywords if kw.arg}
        # ``**kwargs`` forwarding (kw.arg is None) counts as passing it through.
        forwards = any(kw.arg is None for kw in node.keywords)
        if "title" not in kwargs and not forwards:
            rel = path.relative_to(_REPO_ROOT)
            offenders.append(f"{rel}:{lineno}")

    assert not offenders, (
        "These callers omit `title=`, which silently disables rules 4 "
        "(hook_equals_title) and 5 (hook_title_truncation) for that render "
        "path:\n  " + "\n  ".join(offenders) + "\n\n"
        "Pass `title=story.get('title', '')`. The parameter defaults to '' "
        "for backward compatibility, so omitting it raises no error and "
        "produces no warning — it just ships the source title as the hook."
    )
