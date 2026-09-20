"""Compare sibling config files and find the ones that quietly drifted apart.

Per-environment settings, per-tenant configs, i18n locale files, Kubernetes
overlays, CI matrices — any time N documents are supposed to share a shape,
that shape is a contract that nothing enforces. Files are edited one at a time,
usually under deadline, and a key added to four of five siblings is invisible.

The failure is silent in a specific and nasty way: the config loader does not
error on a missing key, it returns the default. So the feature is off for that
one tenant, or the threshold is the library's default rather than yours, and
everything downstream reports healthy. You find out from behaviour, weeks
later, and the behaviour is usually "why is this one different".

The worst version is a **typo**. `relevance_treshold` next to four files with
`relevance_threshold` is not a missing key you might notice — it is a key that
exists, reads fine in review, and is loaded by nothing. Both the intended
setting and the typo'd one are silently absent from where it counts. That is
why near-miss detection is the headline check here and rated critical: a
missing key is an omission, but a near-miss is an omission wearing a disguise.

## What it compares

Documents are flattened to dotted paths (`content_filter.threshold`) and
compared across the set. **Lists are treated as leaves** — their type and
emptiness are compared, their contents are not. Sibling configs differ in list
contents by design (that is usually the entire reason they are separate files),
so diffing elements would bury the structural signal in noise.

## What it deliberately does not do

Value differences are **off by default**. Two configs holding different
thresholds is not drift, it is configuration — the whole point of separate
files. Reporting it by default would make the tool cry wolf on its primary use
case. Turn on `report_value_outliers` when you expect the set to agree on
values and want to find the one that does not.
"""
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import yaml
from inferencesh import BaseApp, BaseAppSetup
from pydantic import BaseModel, Field

logger = logging.getLogger("config-drift-check")

# Below this, near-miss detection is noise: short keys like `id`/`ids` or
# `to`/`top` are two edits apart and legitimately distinct.
_MIN_NEAR_MISS_LEN = 5
_MAX_NEAR_MISS_DISTANCE = 2


def _levenshtein(a: str, b: str, cap: int) -> int:
    """Edit distance, abandoned early once it exceeds `cap`.

    The cap matters: this runs across every unique key against every shared
    key, and the answer is only ever compared against a small threshold.
    """
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def _kind(v: Any) -> str:
    """Type name used for comparison. Bools are checked before ints on purpose:
    in Python `True` is an `int`, and a config flipping between `true` and `1`
    is exactly the drift worth catching."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if isinstance(v, list):
        return "list"
    if isinstance(v, dict):
        return "dict"
    return type(v).__name__


def _flatten(obj: Any, prefix: str = "") -> Dict[str, Any]:
    """Dotted paths. Lists stop here -- see the module docstring."""
    out: Dict[str, Any] = {}
    if isinstance(obj, dict):
        if not obj and prefix:
            out[prefix] = {}
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict) and v:
                out.update(_flatten(v, path))
            else:
                out[path] = v
    elif prefix:
        out[prefix] = obj
    return out


def _parse(name: str, content: str, fmt: str) -> Any:
    if fmt in ("json", "auto"):
        try:
            return json.loads(content)
        except Exception:
            if fmt == "json":
                raise RuntimeError(f"{name}: not valid JSON")
    try:
        return yaml.safe_load(content)
    except Exception as exc:
        raise RuntimeError(f"{name}: parsed as neither JSON nor YAML ({exc})")


class Document(BaseModel):
    name: str = Field(description="Identifier for this document, e.g. gaming/sources.yaml")
    content: str = Field(description="Full text of the config file")
    format: str = Field(default="auto", description="auto, json or yaml")


class Finding(BaseModel):
    code: str = Field(description="Stable machine-readable identifier")
    severity: str = Field(description="critical, error, warn or info")
    path: str = Field(description="Dotted config path the finding concerns")
    detail: str = Field(description="What differs, naming the documents")
    fix: str = Field(description="Concrete suggestion")
    documents: List[str] = Field(description="Documents implicated")


class AppSetup(BaseAppSetup):
    report_value_outliers: bool = Field(
        default=False,
        description="Report a document whose VALUE differs when all others agree. "
                    "Off by default: sibling configs differ in values by design.",
    )
    missing_key_threshold: float = Field(
        default=0.5,
        description="A key must appear in at least this fraction of documents before "
                    "its absence elsewhere is reported. Guards against one file's "
                    "private settings being read as everyone else's omission.",
    )


class RunInput(BaseModel):
    documents: List[Document] = Field(
        description="Two or more sibling configs that should share a shape. "
                    "Order does not matter; names are echoed in findings."
    )


class RunOutput(BaseModel):
    verdict: str = Field(description="aligned, drifted or broken")
    document_count: int = Field(description="Documents compared")
    total_paths: int = Field(description="Distinct dotted paths across the set")
    shared_paths: int = Field(description="Paths present in every document")
    findings: List[Finding] = Field(description="Problems found, worst first")
    most_divergent: Optional[str] = Field(
        None, description="Document appearing in the most findings"
    )
    per_document_paths: Dict[str, int] = Field(description="Path count per document")
    summary: str = Field(description="One-line human-readable result")


class App(BaseApp):

    async def setup(self, config: AppSetup):
        self.report_values = config.report_value_outliers
        self.threshold = min(1.0, max(0.0, config.missing_key_threshold))
        logger.info("config-drift-check ready (report_value_outliers=%s threshold=%.2f)",
                    self.report_values, self.threshold)

    async def run(self, input_data: RunInput) -> RunOutput:
        docs = input_data.documents
        if len(docs) < 2:
            raise RuntimeError("Need at least 2 documents to compare -- drift is a "
                               "relationship between siblings, not a property of one file.")

        names = [d.name for d in docs]
        if len(set(names)) != len(names):
            raise RuntimeError("Document names must be unique; they are how findings "
                               "identify which file to edit.")

        logger.info("comparing %d document(s)", len(docs))
        flat: Dict[str, Dict[str, Any]] = {}
        for d in docs:
            parsed = _parse(d.name, d.content, d.format)
            if parsed is None:
                parsed = {}
            if not isinstance(parsed, dict):
                raise RuntimeError(f"{d.name}: top level must be a mapping, got {_kind(parsed)}")
            flat[d.name] = _flatten(parsed)
            logger.info("%s -> %d path(s)", d.name, len(flat[d.name]))

        n = len(docs)
        all_paths = sorted({p for f in flat.values() for p in f})
        holders: Dict[str, List[str]] = {
            p: [nm for nm in names if p in flat[nm]] for p in all_paths
        }
        shared = [p for p in all_paths if len(holders[p]) == n]
        findings: List[Finding] = []

        # --- near-miss keys: the headline check --------------------------
        # A key held by exactly one document that is a small edit away from a
        # key the others share is almost always a typo, and a typo is worse
        # than an omission because it looks correct in review.
        unique = [p for p in all_paths if len(holders[p]) == 1]
        widely_held = [p for p in all_paths if len(holders[p]) >= max(2, n - 1)]
        for u in unique:
            u_parent, _, u_leaf = u.rpartition(".")
            if len(u_leaf) < _MIN_NEAR_MISS_LEN:
                continue
            for w in widely_held:
                w_parent, _, w_leaf = w.rpartition(".")
                if u_parent != w_parent or u_leaf == w_leaf:
                    continue
                dist = _levenshtein(u_leaf, w_leaf, _MAX_NEAR_MISS_DISTANCE)
                if dist <= _MAX_NEAR_MISS_DISTANCE:
                    owner = holders[u][0]
                    findings.append(Finding(
                        code="near_miss_key", severity="critical", path=u,
                        detail=(f"{owner} has {u_leaf!r} where {len(holders[w])} sibling(s) "
                                f"have {w_leaf!r} (edit distance {dist}). Almost certainly a "
                                f"typo: the intended key is absent, so its default applies, "
                                f"and the typo'd key is read by nothing."),
                        fix=f"Rename {u!r} to {w!r} in {owner}.",
                        documents=[owner],
                    ))
                    break

        near_miss_paths = {f.path for f in findings if f.code == "near_miss_key"}

        # --- missing keys -------------------------------------------------
        for p in all_paths:
            have = holders[p]
            if len(have) == n or p in near_miss_paths:
                continue
            frac = len(have) / n
            missing = [nm for nm in names if nm not in have]
            if len(have) == 1 and n >= 3:
                findings.append(Finding(
                    code="unique_key", severity="warn", path=p,
                    detail=f"{p!r} appears only in {have[0]} -- absent from the other "
                           f"{n - 1} document(s).",
                    fix="Either it is genuinely specific to this document, or it is a "
                        "leftover from a change that was never propagated.",
                    documents=have,
                ))
            elif frac >= self.threshold:
                sev = "error" if len(missing) == 1 else "warn"
                findings.append(Finding(
                    code="missing_key", severity=sev, path=p,
                    detail=f"{p!r} is present in {len(have)}/{n} documents; missing from "
                           f"{', '.join(missing)}. The loader will fall back to a default "
                           f"there rather than failing.",
                    fix=f"Add {p!r} to {', '.join(missing)}, or remove it from the rest if "
                        f"it is obsolete.",
                    documents=missing,
                ))

        # --- type / shape drift on shared keys ---------------------------
        for p in shared:
            # Nulls are checked BEFORE types, and excluded from the type
            # comparison. A null is "unset", not "a different type" -- folding it
            # into type_mismatch both mislabels it and makes this branch
            # unreachable, since a null beside a string always yields two kinds.
            nulls = [nm for nm in names if flat[nm][p] is None]
            if nulls and len(nulls) < n:
                findings.append(Finding(
                    code="null_value", severity="warn", path=p,
                    detail=f"{p!r} is null in {', '.join(nulls)} but populated elsewhere.",
                    fix="An explicit null usually means 'not filled in yet', not 'off'.",
                    documents=nulls,
                ))
                continue

            kinds: Dict[str, List[str]] = {}
            for nm in names:
                if flat[nm][p] is None:
                    continue
                kinds.setdefault(_kind(flat[nm][p]), []).append(nm)
            if len(kinds) > 1:
                desc = "; ".join(f"{k} in {', '.join(v)}" for k, v in sorted(kinds.items()))
                findings.append(Finding(
                    code="type_mismatch", severity="error", path=p,
                    detail=f"{p!r} has inconsistent types across the set: {desc}.",
                    fix="Pick one type. bool-vs-int and str-vs-int drift usually means a "
                        "quoted value in one file and a bare one in another.",
                    documents=sorted({nm for v in kinds.values() for nm in v}),
                ))
                continue

            empties = [nm for nm in names
                       if isinstance(flat[nm][p], (list, dict)) and not flat[nm][p]]
            if empties and len(empties) < n:
                findings.append(Finding(
                    code="empty_collection", severity="warn", path=p,
                    detail=f"{p!r} is empty in {', '.join(empties)} but populated elsewhere.",
                    fix="Confirm the emptiness is deliberate rather than an unfinished edit.",
                    documents=empties,
                ))
                continue

            if self.report_values:
                vals: Dict[str, List[str]] = {}
                for nm in names:
                    try:
                        key = json.dumps(flat[nm][p], sort_keys=True, default=str)
                    except Exception:
                        key = repr(flat[nm][p])
                    vals.setdefault(key, []).append(nm)
                if len(vals) == 2:
                    odd = min(vals.items(), key=lambda kv: len(kv[1]))
                    rest = max(vals.items(), key=lambda kv: len(kv[1]))
                    if len(odd[1]) == 1 and len(rest[1]) >= 2:
                        findings.append(Finding(
                            code="value_outlier", severity="info", path=p,
                            detail=f"{p!r} is {odd[0]} in {odd[1][0]} while {len(rest[1])} "
                                   f"sibling(s) agree on {rest[0]}.",
                            fix="Often deliberate. Worth a look when the set is meant to agree.",
                            documents=odd[1],
                        ))

        rank = {"critical": 0, "error": 1, "warn": 2, "info": 3}
        findings.sort(key=lambda f: (rank.get(f.severity, 4), f.path))

        tally: Dict[str, int] = {}
        for f in findings:
            for d in f.documents:
                tally[d] = tally.get(d, 0) + 1
        most = max(tally, key=lambda k: tally[k]) if tally else None

        sev = {f.severity for f in findings}
        if "critical" in sev:
            verdict = "broken"
        elif sev & {"error", "warn"}:
            verdict = "drifted"
        else:
            verdict = "aligned"

        problems = sum(1 for f in findings if f.severity != "info")
        if verdict == "aligned":
            summary = (f"aligned — {len(docs)} documents share all {len(shared)} paths"
                       + (f" ({len(findings)} note(s))" if findings else ""))
        else:
            codes = ", ".join(sorted({f.code for f in findings if f.severity != "info"})[:3])
            summary = (f"{verdict} — {problems} finding(s) across {len(docs)} documents, "
                       f"{len(shared)}/{len(all_paths)} paths shared: {codes}")

        logger.info("verdict=%s findings=%d shared=%d/%d",
                    verdict, len(findings), len(shared), len(all_paths))

        return RunOutput(
            verdict=verdict, document_count=len(docs), total_paths=len(all_paths),
            shared_paths=len(shared), findings=findings, most_divergent=most,
            per_document_paths={nm: len(flat[nm]) for nm in names}, summary=summary,
        )
