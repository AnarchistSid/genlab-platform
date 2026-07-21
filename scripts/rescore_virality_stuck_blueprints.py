"""One-shot: re-score virality on blueprints stuck at VISUAL_READY
with virality_score=0.0 (pre-2026-07-21-fix leftovers).

Context
-------
Before 2026-07-21, all 4 non-AI niches lacked a `virality_scoring:`
section in their config → ViralityScoring stage fell back to
DEFAULT_PATTERNS (100% AI-industry vocabulary) → non-AI hooks matched
zero patterns → virality_score=0.0 → auto_approval_gate hard-rejected
at the >=0.05 threshold → blueprints stuck at VISUAL_READY forever.

The config fix (per-niche patterns) ships in the same commit as this
script. But the stuck blueprints already have `virality_score=0.0`
persisted in the DB — the pipeline doesn't retroactively re-score.
This script re-runs the scoring stage against the current (fixed)
config and updates blueprints.extra.virality_score in-place.

## Contract

- --dry-run (default): print planned updates, write nothing
- --commit: write
- Idempotent: only rescores blueprints where virality_score is
  currently exactly 0.0 (rescoring an already-updated one is a no-op)
- Fail-open per row: one bad blueprint doesn't abort the batch
- Scoped to VISUAL_READY status only — avoids touching DRAFTED
  (pipeline is still working on them) or ARCHIVED / PUBLISHED (immutable)

## Usage (on prod)

    cd /opt/genlab
    sudo -u genlab -H bash -c 'set -a; source .env; set +a; \\
      .venv/bin/python scripts/rescore_virality_stuck_blueprints.py --commit'
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("rescore_virality_stuck_blueprints")
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


# Repo-root-relative config paths — mirror test_niche_virality_configs.py.
_REPO_ROOT = Path(os.environ.get("GENLAB_PROJECT_ROOT", "/opt/genlab"))
NICHE_CONFIG_PATHS: dict[str, Path] = {
    "sports": _REPO_ROOT / "ClutchWire" / "config" / "scoring_weights.yaml",
    "gaming": _REPO_ROOT / "CriticalRush" / "niches" / "gaming" / "config" / "scoring_weights.yaml",
    "movies": _REPO_ROOT / "SpliceReel" / "config" / "scoring_weights.yaml",
    "anime": _REPO_ROOT / "FrameDrift" / "config" / "scoring_weights.yaml",
    # ai_creators intentionally skipped — its default patterns match its
    # vocabulary, so its historical scores were correct, not stuck.
}


def _load_niche_patterns() -> dict[str, dict]:
    """Load virality_scoring patterns for each non-AI niche. Fail-open
    per niche: a missing/bad config produces empty patterns (which
    would score everything 0 — same as before the fix)."""
    import yaml

    out: dict[str, dict] = {}
    for niche_id, path in NICHE_CONFIG_PATHS.items():
        if not path.exists():
            logger.warning("[%s] config missing at %s, skipping", niche_id, path)
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            virality = data.get("virality_scoring") or {}
            out[niche_id] = virality
        except Exception as exc:  # noqa: BLE001 — fail-open
            logger.warning("[%s] config load failed: %s", niche_id, exc)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true",
                        help="Actually write. Default is dry-run.")
    args = parser.parse_args()

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        logger.error("DATABASE_URL not set — cannot connect")
        return 2

    # Lazy imports so --help works before genlab_core is importable
    try:
        from genlab_core.pipeline.stages.virality_scoring import (
            DEFAULT_WEIGHTS,
            ViralityScoring,
            _compile_patterns,
        )
        from genlab_core.storage.tenant_context import pg_connect
    except ImportError as exc:
        logger.error("genlab_core import failed: %s", exc)
        return 2

    niche_patterns = _load_niche_patterns()

    # Compile once per niche — avoid re-compiling in the row loop
    compiled: dict[str, tuple[dict, dict]] = {}
    for niche_id, virality_cfg in niche_patterns.items():
        weights = virality_cfg.get("weights") or DEFAULT_WEIGHTS
        patterns = _compile_patterns(virality_cfg.get("patterns"))
        compiled[niche_id] = (weights, patterns)

    scorer = ViralityScoring()
    planned = 0
    updated = 0
    skipped_no_change = 0

    with pg_connect(dsn, niche_id="all", connect_timeout=10) as conn:
        # Load candidates in one shot — 6-20 rows expected, safe to
        # materialise. LIMIT prevents the pathological "table just grew"
        # case from making this a runaway.
        rows = conn.execute(
            """
            SELECT id, niche_id, hook, title, caption
            FROM blueprints
            WHERE status = 'VISUAL_READY'
              AND scheduled_for IS NULL
              AND niche_id IN ('sports', 'gaming', 'movies', 'anime')
              AND ((extra->>'virality_score')::float = 0.0
                   OR extra->>'virality_score' IS NULL)
            LIMIT 100
            """
        ).fetchall()

        for row in rows:
            if isinstance(row, dict):
                bp_id = row["id"]
                niche_id = row["niche_id"]
                hook = row.get("hook") or ""
                title = row.get("title") or ""
                caption = row.get("caption") or ""
            else:
                bp_id, niche_id, hook, title, caption = row[0], row[1], row[2] or "", row[3] or "", row[4] or ""

            if niche_id not in compiled:
                continue

            weights, patterns = compiled[niche_id]

            # Feed the scorer the same shape it sees during pipeline run:
            # bp dict with hook/title/caption at top level (matches
            # ViralityScoring._score's field lookups).
            fake_bp = {
                "hook": hook,
                "title": title,
                "caption": caption,
                "body": "",
                "content": {},
                "candidate_id": str(bp_id),
            }

            try:
                matched, score = scorer._score(fake_bp, weights, patterns)
            except Exception as exc:  # noqa: BLE001 — fail-open per row
                logger.warning("[skip] %s: scoring raised %s", bp_id, exc)
                continue

            rounded = round(score, 4)

            if rounded == 0.0:
                skipped_no_change += 1
                logger.info(
                    "[skip] %s (%s): still scores 0.0 with new patterns — hook=%r",
                    bp_id, niche_id, hook[:60],
                )
                continue

            planned += 1
            logger.info(
                "[plan] %s (%s): 0.0 → %.4f matched=%s hook=%r",
                bp_id, niche_id, rounded, matched, hook[:60],
            )

            if args.commit:
                try:
                    # Merge into extra JSONB — preserves all other keys.
                    # jsonb_set is more surgical than reading + rewriting
                    # the whole object.
                    conn.execute(
                        """
                        UPDATE blueprints
                        SET extra = jsonb_set(
                                jsonb_set(
                                    extra,
                                    '{virality_score}',
                                    to_jsonb(%s::float)
                                ),
                                '{virality_features}',
                                %s::jsonb
                            ),
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (rounded, json.dumps(matched), str(bp_id)),
                    )
                    updated += 1
                    logger.info("[done] %s: virality_score updated to %.4f", bp_id, rounded)
                except Exception as exc:  # noqa: BLE001 — fail-open per row
                    logger.warning("[fail] %s: UPDATE raised %s", bp_id, exc)

    logger.info(
        "SUMMARY: planned=%d updated=%d skip_no_change=%d dry_run=%s",
        planned, updated, skipped_no_change, not args.commit,
    )
    if not args.commit and planned:
        logger.info("Re-run with --commit to actually write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
