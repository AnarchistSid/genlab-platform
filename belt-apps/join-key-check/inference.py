"""Check a SQL join key before you trust a zero-row result.

A join that returns nothing is ambiguous in the worst way: it looks exactly
like a finding. No error is raised, the query is valid, the column names read
correctly — and the natural conclusion is that the data is missing. Often the
data is fine and the key is wrong.

The case this was built from:

    publishing_analytics.blueprint_id   uuid   -- the real foreign key
    blueprints.id                       uuid   -- what it actually references
    blueprints.blueprint_id             text   -- vestigial, empty on every row

Joining `p.blueprint_id = b.blueprint_id` matches on name, casts without
complaint, and returns 0 of 60. The conclusion drawn from it was that a write
path had been dead for 26 days. Nothing was dead. Re-run against `b.id` and
every row matched.

**A join returning exactly zero rows is more often a wrong key than a real
outage.** Real outages are partial and messy; total absence is a strong signal
that the two sides were never comparable in the first place.

## What it checks

- **decoy_column** — the target table has both `id` and `<name>_id`, and the
  join picked the one whose type does not match. The single highest-value check
  here, because the wrong column exists and reads correctly.
- **vestigial_column** — the join column is empty on every row, so the join can
  only ever return nothing. Detected from supplied null/distinct counts.
- **type_mismatch** — `uuid` against `text`, `int` against `text`. In Postgres
  these either error outright or, once someone adds `::text` to silence it,
  quietly compare representations that may never coincide.
- **cardinality** — joining on a column with few distinct values fans out. A
  "wrong count" bug rather than a "zero rows" one.
- **candidate ranking** — for any pair of tables, the columns that could
  plausibly join, ordered by type compatibility, population and name affinity.

Statistics are optional. Supply `null_count`, `distinct_count` and `row_count`
from a stats query and the vestigial and cardinality checks activate; without
them the tool still does type and decoy analysis and says which checks it
skipped rather than implying a clean bill of health.

Nothing connects to a database. You hand it schema, it hands back an opinion.
"""
import logging
import re
from typing import Dict, List, Optional, Tuple

from inferencesh import BaseApp, BaseAppSetup
from pydantic import BaseModel, Field

logger = logging.getLogger("join-key-check")

# Types that compare cleanly with each other. Anything across groups needs a
# cast, and a cast is where silent zero-row joins are born.
_FAMILIES: Dict[str, str] = {
    "uuid": "uuid",
    "text": "text", "varchar": "text", "character varying": "text",
    "char": "text", "character": "text", "citext": "text", "name": "text",
    "int": "int", "integer": "int", "int2": "int", "int4": "int", "int8": "int",
    "smallint": "int", "bigint": "int", "serial": "int", "bigserial": "int",
    "numeric": "num", "decimal": "num", "real": "num", "double precision": "num",
    "float": "num", "float4": "num", "float8": "num",
    "bool": "bool", "boolean": "bool",
    "date": "time", "timestamp": "time", "timestamptz": "time",
    "timestamp with time zone": "time", "timestamp without time zone": "time",
    "json": "json", "jsonb": "json",
}


def _family(sql_type: str) -> str:
    t = (sql_type or "").strip().lower()
    t = re.sub(r"\(.*?\)", "", t).strip()
    return _FAMILIES.get(t, t or "unknown")


class Column(BaseModel):
    name: str = Field(description="Column name")
    type: str = Field(description="SQL type, e.g. uuid, text, bigint")
    null_count: Optional[int] = Field(None, description="Rows where this column is NULL")
    distinct_count: Optional[int] = Field(None, description="Distinct non-null values")


class Table(BaseModel):
    name: str = Field(description="Table name")
    row_count: Optional[int] = Field(None, description="Total rows")
    columns: List[Column] = Field(description="Columns, from information_schema")


class ProposedJoin(BaseModel):
    left_table: str
    left_column: str
    right_table: str
    right_column: str


class Finding(BaseModel):
    code: str = Field(description="Stable machine-readable identifier")
    severity: str = Field(description="critical, error, warn or info")
    detail: str = Field(description="What is wrong with this key")
    fix: str = Field(description="Concrete suggestion")


class Candidate(BaseModel):
    left: str = Field(description="left_table.column")
    right: str = Field(description="right_table.column")
    score: float = Field(description="0-1 plausibility, from type, population and naming")
    why: str = Field(description="What earned or cost the score")


class AppSetup(BaseAppSetup):
    max_candidates: int = Field(default=6, description="How many alternative keys to return")


class RunInput(BaseModel):
    tables: List[Table] = Field(description="Two or more table schemas")
    join: Optional[ProposedJoin] = Field(
        None, description="The join to validate. Omit to just rank candidate keys."
    )


class RunOutput(BaseModel):
    verdict: str = Field(description="ok, suspect or broken — 'suspect' means it may silently return nothing")
    findings: List[Finding] = Field(description="Problems with the proposed join, worst first")
    candidates: List[Candidate] = Field(description="Better keys, best first")
    checks_skipped: List[str] = Field(description="Checks that needed statistics you did not supply")
    summary: str = Field(description="One-line human-readable result")


class App(BaseApp):

    async def setup(self, config: AppSetup):
        self.max_candidates = max(1, config.max_candidates)
        logger.info("join-key-check ready (max_candidates=%d)", self.max_candidates)

    @staticmethod
    def _find(tables: List[Table], name: str) -> Optional[Table]:
        for t in tables:
            if t.name.lower() == name.lower():
                return t
        return None

    @staticmethod
    def _col(table: Table, name: str) -> Optional[Column]:
        for c in table.columns:
            if c.name.lower() == name.lower():
                return c
        return None

    @staticmethod
    def _empty(col: Column, rows: Optional[int]) -> bool:
        """All-NULL, or zero distinct values. Either way the join cannot match."""
        if col.distinct_count == 0:
            return True
        if rows and col.null_count is not None and col.null_count >= rows:
            return True
        return False

    def _score(self, lt: Table, lc: Column, rt: Table, rc: Column) -> Tuple[float, str]:
        notes: List[str] = []
        score = 0.0
        if _family(lc.type) == _family(rc.type):
            score += 0.5
            notes.append(f"types match ({_family(lc.type)})")
        else:
            notes.append(f"type gap {lc.type}/{rc.type}")

        if self._empty(rc, rt.row_count) or self._empty(lc, lt.row_count):
            notes.append("one side is empty — cannot match")
            return 0.0, "; ".join(notes)
        if rc.null_count is not None and rt.row_count:
            filled = 1 - (rc.null_count / max(1, rt.row_count))
            score += 0.2 * filled
            notes.append(f"{filled:.0%} populated")

        ln, rn = lc.name.lower(), rc.name.lower()
        singular = rt.name.lower().rstrip("s")
        if ln == rn:
            score += 0.15
            notes.append("same name")
        if rn == "id" and ln in (f"{singular}_id", f"{rt.name.lower()}_id"):
            score += 0.3
            notes.append(f"'{ln}' is the conventional FK for {rt.name}.id")
        if rn.endswith("_id") or rn == "id":
            score += 0.05

        # A key that repeats is a fan-out risk, not a match.
        if rc.distinct_count is not None and rt.row_count:
            if rc.distinct_count < rt.row_count * 0.9:
                score -= 0.1
                notes.append(f"only {rc.distinct_count} distinct of {rt.row_count} rows")
        return max(0.0, min(1.0, score)), "; ".join(notes)

    async def run(self, input_data: RunInput) -> RunOutput:
        tables = input_data.tables
        if len(tables) < 2:
            raise RuntimeError("Need at least two tables — a join is a relationship.")

        findings: List[Finding] = []
        skipped: List[str] = []
        if not any(t.row_count is not None for t in tables):
            skipped.append("vestigial_column and cardinality — no row_count supplied")
        if not any(c.null_count is not None for t in tables for c in t.columns):
            skipped.append("population scoring — no null_count supplied")

        logger.info("checking %d table(s), join=%s", len(tables), bool(input_data.join))

        # --- validate the proposed join ---------------------------------
        lt = rt = lc = rc = None
        if input_data.join:
            j = input_data.join
            lt, rt = self._find(tables, j.left_table), self._find(tables, j.right_table)
            if lt is None or rt is None:
                raise RuntimeError(
                    f"Join references a table not supplied: "
                    f"{j.left_table if lt is None else j.right_table}"
                )
            lc, rc = self._col(lt, j.left_column), self._col(rt, j.right_column)
            for tbl, col, side in ((lt, lc, j.left_column), (rt, rc, j.right_column)):
                if col is None:
                    findings.append(Finding(
                        code="column_not_found", severity="critical",
                        detail=f"{tbl.name}.{side} does not exist in the supplied schema.",
                        fix=f"Available: {', '.join(c.name for c in tbl.columns[:8])}",
                    ))

            if lc and rc:
                lf, rf = _family(lc.type), _family(rc.type)

                if self._empty(rc, rt.row_count):
                    findings.append(Finding(
                        code="vestigial_column", severity="critical",
                        detail=(f"{rt.name}.{rc.name} is empty on every row "
                                f"(distinct={rc.distinct_count}, null={rc.null_count} of "
                                f"{rt.row_count}). This join can only ever return zero rows — "
                                f"not because data is missing, but because the column holds "
                                f"nothing to match against."),
                        fix=f"Almost certainly the wrong column. See candidates below.",
                    ))
                if self._empty(lc, lt.row_count):
                    findings.append(Finding(
                        code="vestigial_column", severity="critical",
                        detail=f"{lt.name}.{lc.name} is empty on every row.",
                        fix="Check whether this column was superseded and never dropped.",
                    ))

                if lf != rf:
                    findings.append(Finding(
                        code="type_mismatch", severity="error",
                        detail=(f"{lt.name}.{lc.name} is {lc.type} but {rt.name}.{rc.name} "
                                f"is {rc.type}. Postgres will refuse this outright; once "
                                f"someone silences it with ::text the two sides compare "
                                f"representations that may never coincide."),
                        fix="Join on columns of the same type rather than casting.",
                    ))

                # --- the decoy check: the highest-value one --------------
                decoys = [
                    c for c in rt.columns
                    if c.name.lower() != rc.name.lower()
                    and _family(c.type) == lf
                    and (c.name.lower() == "id"
                         or c.name.lower().endswith("_id")
                         or c.name.lower() == lc.name.lower())
                    and not self._empty(c, rt.row_count)
                ]
                if decoys and (lf != rf or self._empty(rc, rt.row_count)):
                    d = decoys[0]
                    findings.append(Finding(
                        code="decoy_column", severity="critical",
                        detail=(f"{rt.name} has BOTH {rc.name} ({rc.type}) and {d.name} "
                                f"({d.type}). The join picked {rc.name}, but {d.name} is the "
                                f"one whose type matches {lt.name}.{lc.name} ({lc.type}). "
                                f"The wrong column exists and reads correctly, which is why "
                                f"this survives review."),
                        fix=f"Join on {rt.name}.{d.name} instead.",
                    ))

                if (rc.distinct_count is not None and rt.row_count
                        and rc.distinct_count < rt.row_count * 0.9
                        and not self._empty(rc, rt.row_count)):
                    findings.append(Finding(
                        code="low_cardinality", severity="warn",
                        detail=(f"{rt.name}.{rc.name} has {rc.distinct_count} distinct values "
                                f"across {rt.row_count} rows, so this join fans out."),
                        fix="Expect duplicated left rows. If you wanted one-to-one, this is "
                            "not the key.",
                    ))

        # --- candidate ranking -------------------------------------------
        pairs: List[Candidate] = []
        lefts = [lt] if lt else tables
        rights = [rt] if rt else tables
        for a in lefts:
            for b in rights:
                if a.name == b.name:
                    continue
                for ca in a.columns:
                    if not (ca.name.lower().endswith("_id") or ca.name.lower() == "id"):
                        continue
                    for cb in b.columns:
                        if not (cb.name.lower().endswith("_id") or cb.name.lower() == "id"):
                            continue
                        if lc and rc and ca.name == lc.name and cb.name == rc.name:
                            continue          # don't re-suggest what was asked about
                        s, why = self._score(a, ca, b, cb)
                        if s > 0:
                            pairs.append(Candidate(
                                left=f"{a.name}.{ca.name}", right=f"{b.name}.{cb.name}",
                                score=round(s, 2), why=why,
                            ))
        pairs.sort(key=lambda c: -c.score)
        pairs = pairs[: self.max_candidates]

        sev = {f.severity for f in findings}
        if "critical" in sev:
            verdict = "broken"
        elif sev:
            verdict = "suspect"
        else:
            verdict = "ok"

        if not input_data.join:
            summary = f"{len(pairs)} candidate key(s) ranked across {len(tables)} tables"
            verdict = "ok"
        elif verdict == "ok":
            summary = "ok — the proposed key is type-compatible and populated"
        else:
            codes = ", ".join(sorted({f.code for f in findings})[:3])
            best = f"; best alternative {pairs[0].left} = {pairs[0].right}" if pairs else ""
            summary = f"{verdict} — {len(findings)} finding(s): {codes}{best}"

        logger.info("verdict=%s findings=%d candidates=%d",
                    verdict, len(findings), len(pairs))
        return RunOutput(verdict=verdict, findings=findings, candidates=pairs,
                         checks_skipped=skipped, summary=summary)
