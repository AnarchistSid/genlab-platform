"""Pin: a class with more than one dict-emitting serialiser emits the same keys from each.

Twice in a week a field was set by a producer, carried by one serialiser, dropped
by its sibling, and lost silently downstream:

* `like_count` — `TrendingVideo.to_dict()` emitted it, `to_story()` did not.
  `CompositeScorer` divides by it, so its absence floored `engagement_factor` at
  0.5 and the sports composite degenerated to a constant (0.473-0.484 across 71
  blueprints, July-September).
* `narration_script` — the writer emitted it, a cherry-pick propagator copied
  only hook + caption, and `GenerateAudio` reported `script_generation_failed`
  while TTS read the caption aloud.

Neither had an invariant between the two emitters. This supplies one, by AST —
no import of the modules, so nothing is executed and nothing needs a DB.

When this fails, the fix is normally to add the missing key, not to widen the
exclusion list. Widen it only when the two serialisers genuinely target different
consumers, and say which in the comment beside the entry.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SEARCH_DIRS = [
    _ROOT / "genlab-core" / "src",
    _ROOT / "BlackboxBrief",
    _ROOT / "ClutchWire",
    _ROOT / "CriticalRush",
    _ROOT / "SpliceReel",
    _ROOT / "FrameDrift",
]

# Deliberate divergences: (module_suffix, ClassName) -> {keys allowed to differ}
# Each entry needs a reason. An empty set means "must match exactly".
_ALLOWED_DIVERGENCE: dict[tuple[str, str], set[str]] = {
    # to_story() is the pipeline story contract and carries routing/provenance
    # fields the flat to_dict() cache shape has no use for. The ENGAGEMENT and
    # CONTENT fields must still match — that is what this pin exists for.
    ("media/trending_video_fetcher.py", "TrendingVideo"): {
        "story_id", "source", "source_url", "canonical_url", "published_date",
        "fetched_at", "summary", "video_source", "niche_id", "search_query",
        "download_url", "license", "description_snippet", "source_mention_count",
        "_trending_video", "published_at", "duration_seconds", "tags",
        "thumbnail_url", "channel_id", "channel_name", "video_id", "title",
        "view_velocity", "is_official_channel",
    },
}

# Keys that carry a measurement or a content claim. If a class emits one of
# these from ANY serialiser, every sibling serialiser must emit it too — no
# exclusion entry can waive these.
_NEVER_DIVERGE = {"like_count", "view_count", "is_highlight", "narration_script"}


def _dict_keys_returned(fn: ast.FunctionDef) -> set[str] | None:
    """Literal string keys of a dict this function returns, or None if it
    doesn't return a plain dict literal we can read statically."""
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            keys = set()
            for k in node.value.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
                else:
                    return None  # **spread or computed key — not readable
            return keys
    return None


def _collect() -> list[tuple[str, str, dict[str, set[str]]]]:
    out = []
    for d in _SEARCH_DIRS:
        if not d.exists():
            continue
        for path in d.rglob("*.py"):
            if "/tests/" in str(path) or path.name.startswith("test_"):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
                emitters: dict[str, set[str]] = {}
                for fn in [n for n in cls.body if isinstance(n, ast.FunctionDef)]:
                    if not fn.name.startswith("to_"):
                        continue
                    keys = _dict_keys_returned(fn)
                    if keys:
                        emitters[fn.name] = keys
                if len(emitters) >= 2:
                    out.append((str(path.relative_to(_ROOT)), cls.name, emitters))
    return out


PAIRS = _collect()


def test_the_scan_finds_the_known_case() -> None:
    """Guard the guard: if the AST walk silently stops matching, this fails
    rather than the suite passing on an empty set."""
    names = {(p, c) for p, c, _ in PAIRS}
    assert any(c == "TrendingVideo" for _, c in names), (
        f"TrendingVideo has to_dict() and to_story() and must be detected. "
        f"Found: {sorted(names)}"
    )


@pytest.mark.parametrize("path,cls,emitters", PAIRS, ids=lambda v: str(v)[:40])
def test_serialisers_of_one_class_emit_the_same_keys(
    path: str, cls: str, emitters: dict[str, set[str]]
) -> None:
    allowed: set[str] = set()
    for (suffix, name), keys in _ALLOWED_DIVERGENCE.items():
        if cls == name and path.endswith(suffix):
            allowed = keys
            break

    union: set[str] = set()
    for k in emitters.values():
        union |= k

    for fn_name, keys in emitters.items():
        missing = (union - keys) - allowed
        assert not missing, (
            f"{path}::{cls}.{fn_name}() is missing {sorted(missing)}, which a "
            f"sibling serialiser emits. Two emitters on one class with no "
            f"invariant is how like_count and narration_script were lost. "
            f"Add the key, or add a reasoned entry to _ALLOWED_DIVERGENCE."
        )


@pytest.mark.parametrize("path,cls,emitters", PAIRS, ids=lambda v: str(v)[:40])
def test_measurement_and_content_keys_never_diverge(
    path: str, cls: str, emitters: dict[str, set[str]]
) -> None:
    """These cannot be waived by an exclusion entry."""
    union: set[str] = set()
    for k in emitters.values():
        union |= k
    for guarded in _NEVER_DIVERGE & union:
        for fn_name, keys in emitters.items():
            assert guarded in keys, (
                f"{path}::{cls}.{fn_name}() drops {guarded!r} while a sibling "
                f"emits it. This key carries a measurement or a content claim and "
                f"is not waivable — a consumer divides by it or selects on it."
            )
