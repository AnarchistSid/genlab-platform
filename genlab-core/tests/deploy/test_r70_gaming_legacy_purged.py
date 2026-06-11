"""R-70: pin that the two gaming legacy strategy files don't come back.

History
-------
* Commit 506d67e (R-20) claimed all 1,960 LOC of dead code was
  removed — but only 1,136 LOC of the cluster actually landed; the
  two 826-LOC gaming legacy strategies kept living in-tree.
* Wave-7 audit caught the discrepancy and surfaced it as R-70.
* This PR (R-70 part 1) deletes the two files for real and adds
  this pin so a re-introduction is caught at CI time, not by the
  next audit pass six months out.

What this test enforces
-----------------------
1. Neither legacy file exists at its canonical path.
2. No Python module in the workspace imports
   ``write_gaming_content_legacy`` or
   ``adapt_gaming_content_legacy``. An accidental ``cp`` from an
   old branch + a niche.yaml stage reference would silently bring
   the dead module back into the pipeline.

What this test deliberately does NOT enforce
--------------------------------------------
The audit's other half — extracting ``BaseVisualRenderStrategy`` +
``BaseScoringStrategy`` and collapsing the ~750L copy-paste across
SR/CW/FD — is a multi-PR refactor. It needs to read all three
implementations, factor the true common shape, migrate one
channel as a pilot, then sweep. Filed as an explicit R-70 part 2
deferred follow-up; this pin's name is scoped to ``part 1`` so
it doesn't pretend to be the whole story.
"""

from __future__ import annotations

import re
from pathlib import Path

_GENLAB_ROOT = Path(__file__).resolve().parents[3]

_LEGACY_PATHS = (
    _GENLAB_ROOT
    / "CriticalRush"
    / "niches"
    / "gaming"
    / "stages"
    / "write_gaming_content_legacy.py",
    _GENLAB_ROOT
    / "CriticalRush"
    / "niches"
    / "gaming"
    / "stages"
    / "adapt_gaming_content_legacy.py",
)

# Names that, if found in a Python import statement anywhere in the
# workspace, mean the dead module is back in the live pipeline.
_DEAD_MODULE_NAMES = (
    "write_gaming_content_legacy",
    "adapt_gaming_content_legacy",
)

_PYTHON_FILE_SUFFIX = ".py"
# Skip dirs that legitimately may contain stale references (caches,
# vendored deps, the audit doc itself).
_SKIP_DIR_PARTS = frozenset(
    {
        ".venv",
        "__pycache__",
        "node_modules",
        ".tmp",
        ".git",
        "docs",  # audit doc names these files as part of the closure narrative
    }
)


def test_r70_legacy_files_are_gone() -> None:
    """The two 826-LOC dead gaming stage files must not be in tree."""
    still_present = [p for p in _LEGACY_PATHS if p.exists()]
    assert not still_present, (
        "R-70 regression: gaming legacy strategy files are back in tree: "
        f"{[str(p.relative_to(_GENLAB_ROOT)) for p in still_present]}. "
        "These are dead code (no live references) — they should stay deleted. "
        "If you genuinely need to bring one back, also wire it into a "
        "niche.yaml stage and remove it from _DEAD_MODULE_NAMES in this test."
    )


def test_r70_no_imports_of_legacy_modules() -> None:
    """No Python file in the workspace may import the deleted modules.

    Walks the workspace, skipping caches/vendored dirs, and regex-greps
    each .py file's import statements. An accidental ``cp`` from a
    stale branch + a niche.yaml stage reference would silently re-
    activate the dead module — this pin catches that before runtime.
    """
    # Match ``from X.Y.write_gaming_content_legacy import …`` and
    # ``import X.Y.write_gaming_content_legacy [as …]`` AND the bare
    # module name in either form.
    pattern = re.compile(
        r"^(?:from|import)\s+[\w.]*\b(?:"
        + "|".join(re.escape(n) for n in _DEAD_MODULE_NAMES)
        + r")\b",
        re.MULTILINE,
    )

    offenders: list[str] = []
    for path in _GENLAB_ROOT.rglob("*" + _PYTHON_FILE_SUFFIX):
        # Skip dirs that legitimately reference these names (docs, caches).
        if any(part in _SKIP_DIR_PARTS for part in path.parts):
            continue
        # Skip THIS test file — it intentionally mentions the names.
        if path.resolve() == Path(__file__).resolve():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if pattern.search(text):
            offenders.append(str(path.relative_to(_GENLAB_ROOT)))

    assert not offenders, (
        "R-70 regression: the deleted gaming legacy modules are imported "
        f"by: {offenders}. Either restore the deleted file (and remove "
        "from _DEAD_MODULE_NAMES) or refactor the import out."
    )
