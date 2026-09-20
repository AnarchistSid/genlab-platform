"""Validate an Alembic migration graph before it silently drops one of your migrations.

Alembic tracks schema history as a linked list: every file declares its own
`revision` id and points at its parent with `down_revision`. The chain is the
whole mechanism. Nothing validates it.

The failure worth knowing about is **two files declaring the same revision id**.
Alembic builds its map keyed on that id, so the second file to load overwrites
the first. One of your migrations then does not exist as far as Alembic is
concerned — no error, no warning, no mention in `alembic history`. It simply
never runs.

What makes it expensive is what happens downstream. Code written against the
missing table or column hits a `ProgrammingError`, gets caught by whatever
defensive `try/except` sits nearest, and degrades quietly. In the codebase that
prompted this app, a duplicate revision id left a reward-attribution loop dead
for 35 days while every dashboard reported healthy. The fix was renaming one
file. Finding it took vastly longer than that.

## What it checks

- **duplicate_revision** — two files, one id. One migration is unreachable.
- **missing_down_revision** — a parent id that no file defines. Everything
  downstream of the gap can never apply.
- **cycle** — the chain loops. Alembic cannot order it.
- **multiple_heads** — more than one tip. `upgrade head` is ambiguous and
  aborts; you need a merge revision.
- **multiple_bases** — more than one root. Occasionally deliberate, usually a
  `down_revision` someone forgot to fill in.
- **self_reference** — a revision that is its own parent.
- **no_revision_id** — a file in the versions directory that declares nothing.
- **filename_id_mismatch** — the conventional filename prefix disagrees with
  the declared id. Harmless to Alembic, misleading to humans reading `ls`.

## Parsing

Files are parsed with `ast`, not regex — module-level assignments only, nothing
is imported or executed. That handles every form Alembic accepts without
special cases: bare strings, the typed `revision: str = "..."` style, `None`
for a base, and the tuple `down_revision = ("a", "b")` that merge revisions
use. A regex pass is the fallback for fragments that will not parse, and the
result says which method was used per file so you can distrust the weaker one.

When the graph is clean it returns the full base-to-head order, which is the
sequence Alembic will actually apply.
"""
import ast
import logging
import os
import re
from typing import Dict, List, Optional

from inferencesh import BaseApp, BaseAppSetup
from pydantic import BaseModel, Field

logger = logging.getLogger("alembic-graph-check")

# Alembic's convention is <revision_id>_<slug>.py. The id is not required to
# match, so a mismatch is a readability problem rather than a correctness one.
_FILENAME_ID_RE = re.compile(r"^([0-9a-fA-F]{8,32})_")

# Fallbacks for fragments that will not parse as a module.
_RE_REV = re.compile(r"^\s*revision(?:\s*:\s*[^=]+)?\s*=\s*(.+)$", re.MULTILINE)
_RE_DOWN = re.compile(r"^\s*down_revision(?:\s*:\s*[^=]+)?\s*=\s*(.+)$", re.MULTILINE)

_ASSIGN_NAMES = ("revision", "down_revision", "branch_labels", "depends_on")


class MigrationFile(BaseModel):
    name: str = Field(description="Filename, e.g. 43c4084cf927_add_variant_type.py")
    content: str = Field(description="Full text of the migration file")


class Finding(BaseModel):
    code: str = Field(description="Stable machine-readable identifier")
    severity: str = Field(description="critical, error or warn")
    detail: str = Field(description="What was found, naming the files involved")
    fix: str = Field(description="Concrete suggestion")
    files: List[str] = Field(description="Files implicated in this finding")


class RevisionInfo(BaseModel):
    revision: Optional[str] = Field(None, description="Declared revision id")
    down_revision: List[str] = Field(description="Parent ids; empty for a base, 2+ for a merge")
    file: str = Field(description="Source filename")
    parsed_by: str = Field(description="ast or regex — regex results are less trustworthy")
    branch_labels: List[str] = Field(description="Declared branch labels, if any")


class AppSetup(BaseAppSetup):
    allow_multiple_bases: bool = Field(
        default=False,
        description="Treat more than one root as legitimate rather than reporting it.",
    )
    check_filenames: bool = Field(
        default=True,
        description="Report when the filename prefix disagrees with the declared revision id.",
    )


class RunInput(BaseModel):
    files: List[MigrationFile] = Field(
        description="Every file in your alembic versions/ directory. Partial sets "
                    "produce false missing_down_revision findings -- pass them all."
    )


class RunOutput(BaseModel):
    verdict: str = Field(description="clean, warn or broken")
    total_files: int = Field(description="Files supplied")
    total_revisions: int = Field(description="Distinct revision ids found")
    heads: List[str] = Field(description="Revisions with no child")
    bases: List[str] = Field(description="Revisions with no parent")
    findings: List[Finding] = Field(description="Problems found, worst first")
    apply_order: List[str] = Field(
        description="Base-to-head order Alembic will apply, when the graph is orderable"
    )
    revisions: List[RevisionInfo] = Field(description="Parsed detail per file")
    summary: str = Field(description="One-line human-readable result")


def _literal(node) -> Optional[object]:
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def _norm_down(value) -> List[str]:
    """Normalise down_revision into a list of parent ids.

    Alembic accepts None (base), a string (normal), or a tuple/list (a merge
    revision joining branches). Collapsing all three here means the graph code
    below never has to care which form was used.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (tuple, list)):
        return [str(v) for v in value if isinstance(v, str) and v]
    return []


def _parse_ast(content: str):
    """Read module-level assignments. Nothing is imported or executed."""
    tree = ast.parse(content)
    found: Dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
            value = node.value
        else:
            continue
        if value is None:
            continue
        for name in targets:
            if name in _ASSIGN_NAMES and name not in found:
                found[name] = _literal(value)
    return found


def _parse_regex(content: str):
    """Fallback for fragments that are not a parseable module."""
    found: Dict[str, object] = {}
    m = _RE_REV.search(content)
    if m:
        found["revision"] = _literal(ast.parse(m.group(1).strip(), mode="eval").body) \
            if _safe_expr(m.group(1)) else None
    m = _RE_DOWN.search(content)
    if m:
        found["down_revision"] = _literal(ast.parse(m.group(1).strip(), mode="eval").body) \
            if _safe_expr(m.group(1)) else None
    return found


def _safe_expr(text: str) -> bool:
    try:
        ast.parse(text.strip(), mode="eval")
        return True
    except Exception:
        return False


class App(BaseApp):

    async def setup(self, config: AppSetup):
        self.allow_multiple_bases = config.allow_multiple_bases
        self.check_filenames = config.check_filenames
        logger.info(
            "alembic-graph-check ready (allow_multiple_bases=%s check_filenames=%s)",
            self.allow_multiple_bases, self.check_filenames,
        )

    async def run(self, input_data: RunInput) -> RunOutput:
        files = input_data.files
        logger.info("parsing %d migration file(s)", len(files))

        findings: List[Finding] = []
        infos: List[RevisionInfo] = []
        # revision id -> [files declaring it]. A list, not a single value,
        # precisely so the duplicate case is representable instead of lost.
        by_rev: Dict[str, List[str]] = {}
        downs: Dict[str, List[str]] = {}
        ast_count = 0

        for f in files:
            base = os.path.basename(f.name)
            try:
                found = _parse_ast(f.content)
                method = "ast"
                ast_count += 1
            except SyntaxError:
                found = _parse_regex(f.content)
                method = "regex"
                logger.warning("%s did not parse as a module; fell back to regex", base)

            rev = found.get("revision")
            rev = str(rev) if isinstance(rev, str) and rev else None
            parents = _norm_down(found.get("down_revision"))
            labels = _norm_down(found.get("branch_labels"))

            infos.append(RevisionInfo(
                revision=rev, down_revision=parents, file=base,
                parsed_by=method, branch_labels=labels,
            ))

            if rev is None:
                findings.append(Finding(
                    code="no_revision_id", severity="error",
                    detail=f"{base} declares no revision id.",
                    fix="Every file in versions/ must set `revision`. If this is a "
                        "helper module it does not belong in versions/.",
                    files=[base],
                ))
                continue

            by_rev.setdefault(rev, []).append(base)
            # First declaration wins for edges; the duplicate is reported
            # separately. This mirrors what Alembic does, so apply_order
            # reflects reality rather than an idealised graph.
            downs.setdefault(rev, parents)

            if rev in parents:
                findings.append(Finding(
                    code="self_reference", severity="critical",
                    detail=f"{base} lists itself ({rev}) as its own down_revision.",
                    fix="Point down_revision at the previous migration, or None if this is the base.",
                    files=[base],
                ))

            if self.check_filenames:
                m = _FILENAME_ID_RE.match(base)
                if m and m.group(1).lower() != rev.lower():
                    findings.append(Finding(
                        code="filename_id_mismatch", severity="warn",
                        detail=f"{base} is named for {m.group(1)!r} but declares {rev!r}.",
                        fix="Rename the file to match. Alembic does not care; anyone "
                            "reading `ls versions/` does.",
                        files=[base],
                    ))

        # --- duplicates: the headline check ------------------------------
        for rev, owners in sorted(by_rev.items()):
            if len(owners) > 1:
                findings.append(Finding(
                    code="duplicate_revision", severity="critical",
                    detail=f"Revision id {rev!r} is declared by {len(owners)} files: "
                           f"{', '.join(sorted(owners))}. Alembic keys its map on this id, "
                           f"so only one of these ever applies -- silently, with no error.",
                    fix="Give the losing file a fresh revision id and re-point the "
                        "down_revision of whatever followed it. Then verify with "
                        "`alembic history` that both now appear.",
                    files=sorted(owners),
                ))

        known = set(by_rev)

        # --- dangling parents --------------------------------------------
        for rev, parents in sorted(downs.items()):
            for p in parents:
                if p not in known:
                    owner = by_rev.get(rev, ["?"])[0]
                    findings.append(Finding(
                        code="missing_down_revision", severity="critical",
                        detail=f"{owner} ({rev}) points at parent {p!r}, which no supplied "
                               f"file defines. The chain is broken here.",
                        fix="Either the parent file is missing from this set, or the id is "
                            "a typo. Nothing downstream of this gap can apply.",
                        files=[owner],
                    ))

        # --- structure ----------------------------------------------------
        children: Dict[str, List[str]] = {r: [] for r in known}
        for rev, parents in downs.items():
            for p in parents:
                if p in children:
                    children[p].append(rev)

        heads = sorted(r for r in known if not children.get(r))
        bases = sorted(r for r in known if not downs.get(r))

        if len(heads) > 1:
            findings.append(Finding(
                code="multiple_heads", severity="error",
                detail=f"{len(heads)} heads: {', '.join(heads)}. `alembic upgrade head` "
                       f"is ambiguous and will refuse to run.",
                fix="Create a merge revision: `alembic merge -m 'merge' " + " ".join(heads[:2]) + "`",
                files=sorted({by_rev[h][0] for h in heads if h in by_rev}),
            ))
        if len(bases) > 1 and not self.allow_multiple_bases:
            findings.append(Finding(
                code="multiple_bases", severity="warn",
                detail=f"{len(bases)} roots: {', '.join(bases)}. Legitimate for genuinely "
                       f"independent branches, but usually a down_revision left unset.",
                fix="Check each root really is meant to start a chain. Set "
                    "allow_multiple_bases if this is intentional.",
                files=sorted({by_rev[b][0] for b in bases if b in by_rev}),
            ))

        # --- cycles + order (Kahn; leftovers are exactly the cycle) -------
        indeg = {r: sum(1 for p in downs.get(r, []) if p in known) for r in known}
        queue = sorted(r for r in known if indeg[r] == 0)
        order: List[str] = []
        while queue:
            node = queue.pop(0)
            order.append(node)
            for child in sorted(children.get(node, [])):
                indeg[child] -= 1
                if indeg[child] == 0:
                    queue.append(child)
        stuck = sorted(known - set(order))
        if stuck:
            findings.append(Finding(
                code="cycle", severity="critical",
                detail=f"{len(stuck)} revision(s) form a cycle and can never be ordered: "
                       f"{', '.join(stuck)}",
                fix="Follow each down_revision until it returns to where you started, "
                    "then break the loop.",
                files=sorted({by_rev[r][0] for r in stuck if r in by_rev}),
            ))
            order = []

        rank = {"critical": 0, "error": 1, "warn": 2}
        findings.sort(key=lambda f: (rank.get(f.severity, 3), f.code))

        sev = {f.severity for f in findings}
        if sev & {"critical", "error"}:
            verdict = "broken"
        elif sev:
            verdict = "warn"
        else:
            verdict = "clean"

        if verdict == "clean":
            summary = (f"clean — {len(known)} revisions, single head "
                       f"{heads[0] if heads else 'n/a'}, chain intact")
        else:
            top = ", ".join(sorted({f.code for f in findings})[:3])
            # Surface the file/revision gap explicitly. "3 files, 2 revisions" is
            # the duplicate bug stated as arithmetic -- a file that resolves to no
            # distinct revision is a file Alembic will never run.
            counted = (f"{len(files)} files, {len(known)} revisions"
                       if len(known) != len(files) else f"{len(known)} revisions")
            summary = f"{verdict} — {len(findings)} finding(s) across {counted}: {top}"

        if ast_count < len(files):
            logger.warning("%d of %d file(s) needed the regex fallback",
                           len(files) - ast_count, len(files))
        logger.info("verdict=%s findings=%d heads=%d bases=%d",
                    verdict, len(findings), len(heads), len(bases))

        return RunOutput(
            verdict=verdict, total_files=len(files), total_revisions=len(known),
            heads=heads, bases=bases, findings=findings, apply_order=order,
            revisions=infos, summary=summary,
        )
