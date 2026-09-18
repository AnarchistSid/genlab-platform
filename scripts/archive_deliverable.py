#!/usr/bin/env python3
"""Make an approved deliverable reproducible, and prove it.

WHY THIS EXISTS
---------------
The WWE v6 deliverable cannot be re-rendered. Its source clip and its 96 SAM2
mattes lived in a scratch dir that was cleaned; no source video id was recorded;
and its crop script imports `v4_build` / `v4_look`, which were never archived.
When RENDER-01 Port 4 needed to prove the ported effects matched the approved
render, the gate the packet specified was simply unrunnable.

An archive that is "the outputs plus some scripts" is not an archive. The thing
that bit us was a TRANSITIVE import three levels down -- the sort of gap you do
not notice until the day you need it, which is always after the scratch is gone.

WHAT COMPLETE MEANS
-------------------
For a deliverable D with build scripts S:

  1. every module S imports, resolved TRANSITIVELY, is archived under D/scripts/
  2. every input file the scripts read is archived, or recorded in the manifest
     with an id and a retention path (for things too large to copy -- a 413 MB
     source clip is an id, not a payload)
  3. the kit YAML in force at approval time is snapshotted
  4. manifest.json carries a sha256 for every archived file

`manifest_complete()` returns the gaps. It is a pin, not a convention: a
deliverable that cannot prove its own reproducibility fails the test suite.

USAGE
    python scripts/archive_deliverable.py check <deliverable_dir>
    python scripts/archive_deliverable.py backfill <deliverable_dir> <scratch_dir>
"""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Modules that are dependencies of the ENVIRONMENT, not of the deliverable.
# stdlib comes from the interpreter rather than a hand-kept list: the first
# version of this shipped a literal set that omitted `urllib`, and a list that
# must be maintained to stay correct is the same class of thing as a scratch dir
# that must be remembered to be kept.
_THIRD_PARTY = {
    "numpy",
    "cv2",
    "PIL",
    "scipy",
    "torch",
    "torchvision",
    "yaml",
    "requests",
    "rembg",
    "sam2",
    "matplotlib",
    "pytest",
    "hydra",
    "omegaconf",
    "tqdm",
    "genlab_core",
}
_ENVIRONMENT = set(sys.stdlib_module_names) | _THIRD_PARTY


@dataclass
class Gap:
    kind: str  # "missing_import" | "missing_input" | "missing_kit" | "bad_hash"
    detail: str


@dataclass
class Manifest:
    name: str
    approved_artifact: str = ""
    files: dict[str, str] = field(default_factory=dict)  # relpath -> sha256
    external_inputs: list[dict] = field(default_factory=list)
    unrecoverable: str = ""
    superseded_by: str = ""
    reproducible_via: str = ""
    notes: str = ""

    def to_json(self) -> str:
        return json.dumps(
            {
                "name": self.name,
                "approved_artifact": self.approved_artifact,
                "files": self.files,
                "external_inputs": self.external_inputs,
                "unrecoverable": self.unrecoverable,
                "superseded_by": self.superseded_by,
                "reproducible_via": self.reproducible_via,
                "notes": self.notes,
            },
            indent=1,
            sort_keys=True,
        )


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def local_imports(source: str) -> set[str]:
    """Module names a script imports that are not stdlib/vendor.

    Uses the AST rather than a regex: `import v6_look as L6, v8_look as L8` on
    one line is the exact shape that a line-oriented grep gets wrong, and it is
    the shape both approved builds use.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                found.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return {m for m in found if m not in _ENVIRONMENT}


def resolve_transitively(scripts_dir: Path, roots: list[Path]) -> tuple[set[str], set[str]]:
    """Walk the import graph from `roots`. Returns (resolved, missing)."""
    seen: set[str] = set()
    missing: set[str] = set()
    queue = list(roots)
    while queue:
        script = queue.pop()
        if not script.exists():
            continue
        for mod in local_imports(script.read_text(errors="replace")):
            if mod in seen:
                continue
            seen.add(mod)
            cand = scripts_dir / f"{mod}.py"
            if cand.exists():
                queue.append(cand)
            else:
                missing.add(mod)
    return seen, missing


def build_scripts(deliverable: Path) -> list[Path]:
    """Scripts that PRODUCE the deliverable, wherever they were put.

    Historic deliverables put them at the top level; later ones use scripts/.
    Both are accepted -- rejecting one would make the pin fail on layout rather
    than on reproducibility.
    """
    out = list((deliverable / "scripts").glob("*.py")) if (deliverable / "scripts").is_dir() else []
    out += list(deliverable.glob("*.py"))
    return out


def _mattes_are_reel_space(inputs: Path, reel=(1080, 1920)) -> bool:
    """True when the archived mattes are already the size the reel renders."""
    # Match on the PATH, not the directory NAME: the WWE archive nests them as
    # inputs/matte/knight/*.png, so the dir literally called "matte" holds no
    # PNGs at all and a name test finds nothing.
    for d in inputs.rglob("*"):
        if not d.is_dir() or "matte" not in str(d.relative_to(inputs)):
            continue
        pngs = sorted(d.glob("*.png"))
        if not pngs:
            continue
        try:
            from PIL import Image

            with Image.open(pngs[0]) as im:
                return im.size == reel
        except Exception:  # noqa: BLE001 — a check must not fail the check
            return False
    return False


def manifest_complete(deliverable: Path) -> list[Gap]:
    """The gaps that stop this deliverable being reproducible."""
    gaps: list[Gap] = []
    mpath = deliverable / "manifest.json"
    if not mpath.exists():
        return [Gap("missing_manifest", f"{deliverable.name} has no manifest.json")]

    man = json.loads(mpath.read_text())
    if man.get("unrecoverable"):
        return []  # recorded as lost, with a reason; nothing left to demand
    if man.get("superseded_by"):
        # An iteration on the way to an approved deliverable. Exempt, but only
        # because it SAYS so and names its successor -- not because a hardcoded
        # list of directory names says so, which is the kind of scoping that
        # quietly stops covering things.
        return []
    if man.get("reproducible_via"):
        # A CODE deliverable: the artifact is committed source, which git already
        # reproduces exactly. Demanding archived build scripts and a frame corpus
        # of it would be theatre. It must still name HOW, so the claim is checkable.
        return []

    scripts_dir = deliverable / "scripts"
    roots = build_scripts(deliverable)
    if not roots:
        gaps.append(Gap("missing_import", "no build scripts archived at all"))
    _, missing = resolve_transitively(scripts_dir, roots)
    for mod in sorted(missing):
        gaps.append(Gap("missing_import", f"{mod}.py imported but not archived"))

    if not any(k.endswith((".yaml", ".yml")) for k in man.get("files", {})):
        gaps.append(Gap("missing_kit", "no kit YAML snapshot in the manifest"))

    # A DELIVERABLE WITH MATTES MUST CARRY THE CROP GEOMETRY.
    #
    # `crop_rect_for()` needs mag / cx / cy plus the chrome-cropped source
    # height, or the archived mattes cannot be reproduced in reel space. UFC-05
    # was archived with only its FRAMING plan (u1/plan.json, mag 1.7778) while
    # the crop used a different file (w2/plan.json, mag 2.0361, src_h 943) — the
    # archive looked complete and the matte could not be rebuilt from it.
    #
    # Required by CAPABILITY, not by filename: the WWE line carries one plan
    # (out/src/seg/mag/cx/cy/torso_w_src) that serves both roles, and demanding
    # a `crop_plan.json` there was this check over-fitting the UFC shape to
    # every deliverable. src_h may be per-row or derivable from chrome.json's
    # video_rect height.
    # Actual matte PNGs, not a filename containing "matte". CONTENT-19 v6 is a
    # SPLICE — it reuses an approved segment's rendered frames and has no mattes
    # of its own — but it ships a proof image called `..._matte_overlays.png`,
    # which a substring test reads as "this deliverable has mattes".
    inputs_dir = deliverable / "inputs"
    has_mattes = (
        any(
            p.suffix == ".png" and "matte" in str(p.relative_to(inputs_dir))
            for p in inputs_dir.rglob("*.png")
        )
        if inputs_dir.is_dir()
        else False
    )
    if has_mattes:
        inputs = deliverable / "inputs"
        # Mattes ALREADY in reel space need no crop geometry — they were
        # produced on the crop rather than on the native frame. The two ACTION
        # arcs differ here and the difference is real: the WWE line ran SAM2 on
        # the 1080x1920 crop (v10/frames), UFC-05 on the native 1920x1080
        # (w2/frames) and warped. Demanding a crop plan of the first was this
        # check generalising from the second.
        if _mattes_are_reel_space(inputs):
            return gaps
        plans = [p for p in (inputs / "crop_plan.json", inputs / "plan.json") if p.exists()]
        if not plans:
            gaps.append(Gap("missing_input", "mattes archived but no crop plan (mag/cx/cy)"))
        else:
            usable = False
            for p in plans:
                try:
                    rows = json.loads(p.read_text())
                except (OSError, ValueError):
                    continue
                rows = rows if isinstance(rows, list) else list(rows.values())
                if not rows or not isinstance(rows[0], dict):
                    continue
                if not {"mag", "cx", "cy"} <= set(rows[0]):
                    continue
                has_h = any("src_h" in r for r in rows) or (inputs / "chrome.json").exists()
                if has_h:
                    usable = True
                    break
            if not usable:
                gaps.append(
                    Gap(
                        "missing_input",
                        "no plan carries mag/cx/cy plus a source height (per-row src_h or "
                        "chrome.json) — crop_rect_for cannot rebuild the mattes in reel space",
                    )
                )

    for rel, digest in sorted(man.get("files", {}).items()):
        f = deliverable / rel
        if not f.exists():
            gaps.append(Gap("missing_input", f"{rel} in manifest but not on disk"))
        elif sha256(f) != digest:
            gaps.append(Gap("bad_hash", f"{rel} does not match its manifest hash"))
    return gaps


def write_manifest(deliverable: Path, man: Manifest) -> None:
    for f in sorted(deliverable.rglob("*")):
        if not f.is_file() or f.name == "manifest.json":
            continue
        man.files[str(f.relative_to(deliverable))] = sha256(f)
    (deliverable / "manifest.json").write_text(man.to_json() + "\n")


def _cmd_check(target: Path) -> int:
    gaps = manifest_complete(target)
    if not gaps:
        print(f"{target.name}: COMPLETE")
        return 0
    print(f"{target.name}: {len(gaps)} gap(s)")
    for g in gaps:
        print(f"  [{g.kind}] {g.detail}")
    return 1


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    cmd, target = argv[1], Path(argv[2])
    if cmd == "check":
        return _cmd_check(target)
    if cmd == "copy-imports":
        # Resolve the import graph against a scratch dir and copy what's missing.
        scratch = Path(argv[3])
        dest = target / "scripts"
        dest.mkdir(parents=True, exist_ok=True)
        for _ in range(6):  # transitive: a copied module may import another
            _, missing = resolve_transitively(dest, build_scripts(target))
            if not missing:
                break
            for mod in missing:
                for cand in (scratch / f"{mod}.py", *scratch.rglob(f"{mod}.py")):
                    if cand.exists():
                        shutil.copy2(cand, dest / f"{mod}.py")
                        print(f"  + {mod}.py  <- {cand}")
                        break
                else:
                    print(f"  ! {mod}.py NOT FOUND in {scratch}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
