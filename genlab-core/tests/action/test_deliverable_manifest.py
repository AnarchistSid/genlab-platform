"""Every approved deliverable must be able to prove it is reproducible.

This pin exists because RENDER-01 Port 4's specified gate -- re-render the
approved segment and compare -- was declared unrunnable when its build script's
transitive imports could not be found. A deliverable whose reproducibility is
only discovered at the moment you need it is a deliverable you cannot trust.

It also guards a subtler failure that actually happened: the gap was reported as
PERMANENT when in fact the material had simply not been looked for properly (a
`find -maxdepth 4` against a tree whose contents sit at depth 6). A manifest
turns "I could not find it" into a recorded, checkable claim.

Skips when `.deliverables/` is absent -- it is gitignored local material, so CI
has nothing to check. That is deliberate: the alternative is a test that fails
everywhere except one laptop.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "scripts"))

archive_deliverable = pytest.importorskip(
    "archive_deliverable", reason="scripts/archive_deliverable.py not importable"
)

_DELIVERABLES = _ROOT / ".deliverables"


def _dirs() -> list[Path]:
    if not _DELIVERABLES.is_dir():
        return []
    return sorted(d for d in _DELIVERABLES.iterdir() if d.is_dir())


@pytest.mark.skipif(not _DELIVERABLES.is_dir(), reason=".deliverables/ not present")
@pytest.mark.parametrize("deliverable", _dirs(), ids=lambda d: d.name)
def test_deliverable_is_reproducible(deliverable: Path):
    gaps = archive_deliverable.manifest_complete(deliverable)
    assert not gaps, "\n".join(f"[{g.kind}] {g.detail}" for g in gaps)


def test_a_missing_transitive_import_is_caught(tmp_path):
    """Mutation check: the shape that actually bit us.

    `v7_crop.py` imported `v4_build`, which imported more still. A gate that
    only checks the top-level script sees nothing wrong.
    """
    d = tmp_path / "fake_deliverable"
    (d / "scripts").mkdir(parents=True)
    (d / "scripts" / "top.py").write_text("import helper_present\n")
    (d / "scripts" / "helper_present.py").write_text("import helper_MISSING\n")
    (d / "kit.yaml").write_text("a: 1\n")
    archive_deliverable.write_manifest(
        d, archive_deliverable.Manifest(name="fake", approved_artifact="x.mp4")
    )
    gaps = archive_deliverable.manifest_complete(d)
    kinds = {g.kind for g in gaps}
    assert "missing_import" in kinds, f"transitive gap not caught: {gaps}"
    assert any("helper_MISSING" in g.detail for g in gaps)


def test_a_tampered_file_is_caught(tmp_path):
    """The manifest's hashes must actually be checked, not just written."""
    d = tmp_path / "fake2"
    (d / "scripts").mkdir(parents=True)
    (d / "scripts" / "top.py").write_text("x = 1\n")
    (d / "kit.yaml").write_text("a: 1\n")
    archive_deliverable.write_manifest(
        d, archive_deliverable.Manifest(name="fake2", approved_artifact="x.mp4")
    )
    assert not archive_deliverable.manifest_complete(d)
    (d / "kit.yaml").write_text("a: 2\n")  # tamper
    gaps = archive_deliverable.manifest_complete(d)
    assert any(g.kind == "bad_hash" for g in gaps), f"tamper not caught: {gaps}"


def test_stdlib_is_not_mistaken_for_a_missing_module():
    """The first version of the resolver shipped a hand-written stdlib set that
    omitted `urllib`, and reported it as an un-archived dependency four times."""
    found = archive_deliverable.local_imports(
        "import urllib.request\nimport json, os\nimport numpy as np\nimport v6_look as L6\n"
    )
    assert found == {"v6_look"}, found


def test_multiple_modules_on_one_import_line_are_all_seen():
    """`import v6_look as L6, v8_look as L8, v9_face as VF` is the exact line in
    both approved builds -- and the shape a line-oriented grep gets wrong."""
    found = archive_deliverable.local_imports("import v6_look as L6, v8_look as L8, v9_face as VF")
    assert found == {"v6_look", "v8_look", "v9_face"}, found
