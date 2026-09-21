"""A story summary must survive to the writer intact.

ANIME-03 §2. `stories.summary` is `text` and always was — the 255 was a
literal at the persist site, not a column limit, so no migration was needed
to lift it.

What it cost, measured 2026-09-21: AniList returns 200-2000 characters. The
Firefly Wedding description is cut at character 255, mid-word, on

    "...a marriage that will redeem her worth in her"

and the sentence immediately after is the one that carries the story: she is
targeted by the assassin Shinpei and proposes marriage to him. 34 anime,
23 movies and 7 sports stories sat at exactly 255 — every one of them cut.
Their hooks were written from a setup with the turn removed.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from genlab_core.pipeline.stages import fetch_anime_promos as promos
from genlab_core.pipeline.stages import push_to_backlog as push
from genlab_core.writing.constants import SUMMARY_MAX_CHARS

_REAL_TRUNCATED_AT = 255
_ANILIST_TYPICAL_MAX = 2000


def test_the_cap_clears_a_real_anilist_description():
    assert SUMMARY_MAX_CHARS >= _ANILIST_TYPICAL_MAX, (
        f"AniList returns up to ~{_ANILIST_TYPICAL_MAX} chars; a cap below "
        "that reintroduces the cut"
    )


def test_a_900_character_description_round_trips():
    """The pin §2 asks for, at the stage that actually built the summary."""
    desc = ("Satoko has everything except time. " * 30)[:900]
    assert len(desc) == 900
    out = promos._build_promo_summary({"description": desc, "title": "X", "source": "anilist"})
    assert len(out) == 900, f"summary truncated to {len(out)}"
    assert out == desc


def test_the_sentence_that_carries_the_story_survives():
    """Length alone is not the point; the turn is."""
    setup = "On the surface, Satoko has it all. " * 6  # ~204 chars of setup
    turn = "She finds herself the target of the assassin Shinpei, and proposes marriage."
    out = promos._build_promo_summary(
        {"description": setup + turn, "title": "Firefly Wedding", "source": "anilist"}
    )
    assert "proposes marriage" in out, (
        "the story's turn was cut; this is the exact failure — at 255 chars "
        "the writer saw the setup and never the turn"
    )


def test_no_literal_cap_on_a_summary_assignment():
    """AST, and scoped to `"summary": <expr>[:N]` only.

    A first version flagged every `[:200]`/`[:500]` in the module and caught
    six innocents — error-message truncations like
    `f"render:validation_failed:{reason}"[:500]` and the short `angle` label.
    It did surface one real thing, so the breadth was not wasted: `angle` is
    built from the summary but is a derived label on the blueprint, not the
    writer's context, and 200 chars is right for it.
    """
    tree = ast.parse(inspect.getsource(push))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, val in zip(node.keys, node.values, strict=False):
            if not (isinstance(key, ast.Constant) and key.value == "summary"):
                continue
            if isinstance(val, ast.Subscript) and isinstance(val.slice, ast.Slice):
                upper = val.slice.upper
                if isinstance(upper, ast.Constant) and isinstance(upper.value, int):
                    offenders.append(f"line {val.lineno}: summary=[:{upper.value}]")
    assert not offenders, (
        "a literal cap on a persisted summary: "
        + ", ".join(offenders)
        + " — use SUMMARY_MAX_CHARS so every site moves together"
    )


def test_every_summary_cap_reads_the_same_constant():
    root = Path(__file__).resolve().parents[2]
    sites = [
        "genlab-core/src/genlab_core/pipeline/stages/push_to_backlog.py",
        "genlab-core/src/genlab_core/pipeline/stages/fetch_anime_promos.py",
        "genlab-core/src/genlab_core/media/trending_video_fetcher.py",
    ]
    for rel in sites:
        src = (root / rel).read_text()
        assert "SUMMARY_MAX_CHARS" in src, f"{rel} does not use the shared cap"
