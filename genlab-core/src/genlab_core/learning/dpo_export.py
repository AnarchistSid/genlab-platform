"""Export preference_data rows to DPO-format JSONL files.

The 2026-06-22 roadmap identified DPO from operator+engagement edits
as the highest single intelligence lever — preference_collector has
been populating ``preference_data`` weekly since shipping, but no
consumer EXPORTS those pairs in a training-consumable format. This
module closes that gap.

Pipeline (each step is a separate systemd timer):
  1. preference_collector → INSERT pairs into preference_data
  2. THIS module → SELECT unexported pairs, write JSONL, mark
     used_in_training=true
  3. (future PR) fine-tune Haiku on accumulated JSONL files +
     canary eval + promote

JSONL output format (compatible with Anthropic fine-tune API +
HuggingFace TRL/DPO trainer + most preference-learning libraries):

  {"prompt": "...", "chosen": "...", "rejected": "..."}

The prompt is reconstructed from generation_context if available,
else falls back to a generic per-(niche, platform) template. Chosen
and rejected are the hook texts.

Idempotency: the ``used_in_training`` column on preference_data
gates re-export. Rows marked true are skipped. A botched export
(failed file write after marking) is recoverable by manually
UPDATEing ``used_in_training=false`` for the affected created_at
window — the SQL is in the docstring of mark_rows_exported.

Output path: ``$GENLAB_PROJECT_ROOT/.tmp/dpo/preference_data_<YYYY-MM-DD>.jsonl``
Naming includes the export date so multiple runs in the same day
overwrite cleanly (idempotent re-run).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def _build_prompt(niche_id: str, platform: str, context: dict) -> str:
    """Reconstruct the generation prompt from row context.

    For early rows that lack generation_context, falls back to a
    generic per-(niche, platform) template that matches how
    llm_hook_generator constructs its system prompt. The fallback is
    less faithful than a real recorded prompt but still useful as
    DPO training context (the chosen/rejected delta is the strong
    signal; the prompt is the conditioning).
    """
    if context and isinstance(context, dict):
        # Prefer a stored prompt if the future generator records one
        stored = context.get("prompt") or context.get("system_prompt")
        if stored and isinstance(stored, str):
            return stored
        story_title = context.get("story_title") or context.get("title", "")
        if story_title:
            return f"Write a viral hook for {niche_id} on {platform}. Story: {story_title}"
    return f"Write a viral hook for {niche_id} on {platform}."


def fetch_unexported_pairs(*, limit: int | None = None) -> list[dict]:
    """SELECT preference_data rows where used_in_training=false.

    Returns rows ordered by created_at ASC so older pairs flow to
    JSONL first. None limit = all rows (training infra is the chooser).
    """
    from psycopg.rows import dict_row

    from genlab_core.storage.tenant_context import pg_connect

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        logger.warning("[dpo-export] DATABASE_URL not set — nothing to export")
        return []

    with pg_connect(dsn, row_factory=dict_row, niche_id="all") as conn:
        with conn.cursor() as cur:
            # niche_id='all' admin mode — preference_data SELECT is
            # cross-niche by design (the training set spans all niches)
            cur.execute("SELECT set_config('app.niche_id', '', true)")
            sql = """
                SELECT id, niche_id, platform, generation_context,
                       chosen_hook, chosen_caption,
                       rejected_hook, rejected_caption,
                       engagement_ratio, created_at
                FROM preference_data
                WHERE used_in_training = false
                  AND chosen_hook IS NOT NULL
                  AND rejected_hook IS NOT NULL
                  AND chosen_hook != ''
                  AND rejected_hook != ''
                ORDER BY created_at ASC
            """
            if limit:
                sql += f" LIMIT {int(limit)}"
            cur.execute(sql)
            rows = cur.fetchall()
            return [dict(r) for r in rows]


def write_jsonl(rows: list[dict], output_path: Path) -> int:
    """Write rows to JSONL in DPO format. Returns count written.

    Each line is a single JSON object — no trailing comma, no array
    wrapper. This matches the format TRL/HuggingFace consume directly.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with output_path.open("w") as f:
        for r in rows:
            context = r.get("generation_context") or {}
            line = {
                "prompt": _build_prompt(r["niche_id"], r["platform"], context),
                "chosen": r["chosen_hook"],
                "rejected": r["rejected_hook"],
                # Extras: training infra can filter or weight by these
                "metadata": {
                    "niche_id": r["niche_id"],
                    "platform": r["platform"],
                    "engagement_ratio": r.get("engagement_ratio"),
                    "preference_id": str(r["id"]),
                },
            }
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
            written += 1
    return written


def mark_rows_exported(row_ids: list[str]) -> int:
    """Set used_in_training=true for the exported row IDs.

    Recovery if export file is lost after marking:
        UPDATE preference_data SET used_in_training = false
        WHERE created_at BETWEEN '<start>' AND '<end>';

    Returns updated row count for verification.
    """
    if not row_ids:
        return 0
    from genlab_core.storage.tenant_context import pg_connect

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return 0

    with pg_connect(dsn, niche_id="all") as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.niche_id', '', true)")
            cur.execute(
                "UPDATE preference_data SET used_in_training = true "
                "WHERE id = ANY(%s::uuid[]) AND used_in_training = false",
                (row_ids,),
            )
            return cur.rowcount


def export(
    *,
    output_dir: Path | None = None,
    mark_exported: bool = True,
    limit: int | None = None,
) -> int:
    """End-to-end export: fetch → write JSONL → mark exported.

    Returns the count of pairs written. Returns 0 (not None) when no
    pairs are available — caller can check log for diagnostic.

    The mark_exported=False mode is for dry-run / re-export scenarios
    where the operator wants to inspect the file BEFORE committing the
    used_in_training flag (which is harder to reverse than re-writing
    the file).
    """
    output_dir = output_dir or (Path(os.environ.get("GENLAB_PROJECT_ROOT", ".")) / ".tmp" / "dpo")
    rows = fetch_unexported_pairs(limit=limit)
    if not rows:
        logger.info("[dpo-export] no unexported preference_data rows — nothing to write")
        return 0

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    output_path = output_dir / f"preference_data_{today}.jsonl"
    written = write_jsonl(rows, output_path)
    logger.info("[dpo-export] wrote %d pairs to %s", written, output_path)

    if mark_exported:
        marked = mark_rows_exported([str(r["id"]) for r in rows])
        logger.info("[dpo-export] marked %d rows used_in_training=true", marked)

    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dpo_export",
        description="Export preference_data rows to JSONL for DPO fine-tuning.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write the JSONL but do NOT mark rows as exported. "
        "Operator can inspect output before committing.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Override output dir (default $GENLAB_PROJECT_ROOT/.tmp/dpo)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max rows to export this run (default: all unexported).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    written = export(
        output_dir=args.output_dir,
        mark_exported=not args.dry_run,
        limit=args.limit,
    )
    # Print result for systemd journalctl + operator scripts
    print(f"DPO export: {written} pairs written")
    return 0 if written >= 0 else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
