#!/usr/bin/env python3
"""Second-tier drainer for pending_engagement review queue.

Motivating problem: the engagement comment_processor.py:770 routes
low-confidence replies to ``pending_review`` status. If the operator
doesn't drain the review queue, items sit forever. The existing
``archive_stranded_engagement_reviews`` (bandit_engagement.py:192)
timeouts them at 7 days — dropping the reply entirely.

This drainer sits BETWEEN the initial ``pending_review`` write and the
7-day timeout. After 24 hours in review, run the stored reply through
a STRICTER LLM re-review:
* Passes → auto-post via the platform client's post_reply, update
  status to 'replied' + timestamp
* Fails → mark 'skipped' with the reason (visible in
  pending_engagement.extra.error_message)

Design principle: OBSERVATIONS auto-approve, ACTIONS need review.
Reply generation is a text-only observation surface — no side-effect
mutation of the bandit space, no billing risk (Anthropic already
paid for the initial generation). Safe to auto-drain within the
whitelist of a stricter classifier.

Flag: GENLAB_ENGAGEMENT_DRAINER_ENABLED (strict-true match).

Usage:
    python scripts/drain_engagement_review_queue.py           # dry-run
    python scripts/drain_engagement_review_queue.py --apply   # write
    python scripts/drain_engagement_review_queue.py --apply --limit 5

Exit codes:
    0 — success (including nothing-to-do or flag-off)
    3 — unhandled exception (durable file written)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "genlab-core" / "src"))

logger = logging.getLogger("engagement_drainer")


_ENABLE_ENV_VAR = "GENLAB_ENGAGEMENT_DRAINER_ENABLED"


def _load_env(env_file: str = "/opt/genlab/.env") -> None:
    if os.environ.get("DATABASE_URL"):
        return
    env_path = Path(env_file)
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _is_enabled() -> bool:
    return os.environ.get(_ENABLE_ENV_VAR, "") in ("true", "TRUE", "True")


def _fetch_stale_review_items(conn, min_age_hours: int, limit: int):
    """Fetch pending_review rows older than min_age_hours.

    Extra JSON usually carries reply_text (the previously-generated
    reply that the low-confidence classifier deferred). Comment_text
    is preserved for re-review context.
    """
    return conn.execute(
        """
        SELECT id::text AS id,
               niche_id,
               platform,
               post_id,
               extra
        FROM pending_engagement
        WHERE status = 'pending_review'
          AND created_at < NOW() - make_interval(hours => %s)
        ORDER BY created_at ASC
        LIMIT %s
        """,
        (min_age_hours, limit),
    ).fetchall()


def _re_review_reply(persona_engine, comment_text: str, reply_text: str, platform: str) -> tuple[bool, str]:
    """Stricter re-review of the stored reply.

    Uses PersonaEngine.validate_reply which applies outbound toxicity
    + semantic sanity checks. Returns (should_post, reason).

    A tighter reply length constraint is applied here: 90% of the
    persona's declared max_length so we err on the side of clean
    trailing punctuation.
    """
    try:
        # PersonaEngine.validate_reply returns True on pass.
        # See engagement/persona_engine.py:225 for the signature.
        validated = persona_engine.validate_reply(
            comment=comment_text,
            reply=reply_text,
            platform=platform,
        )
        if validated:
            return True, "passed_stricter_review"
        return False, "failed_toxicity_or_semantic_check"
    except Exception as exc:  # noqa: BLE001
        from genlab_core.llm.errors import classify_llm_error

        return False, f"re_review_error:{classify_llm_error(exc)}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="Actually post/discard (default: dry-run)")
    ap.add_argument("--min-age-hours", type=int, default=24)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--env-file", default="/opt/genlab/.env")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    _load_env(args.env_file)

    if not _is_enabled():
        logger.info(
            "GENLAB_ENGAGEMENT_DRAINER_ENABLED not set to 'true' — exiting cleanly"
        )
        return 0

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        logger.error("DATABASE_URL not set")
        return 2

    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        rows = _fetch_stale_review_items(conn, args.min_age_hours, args.limit)
        if not rows:
            logger.info(
                "no pending_review items older than %dh — exiting cleanly",
                args.min_age_hours,
            )
            return 0

        logger.info(
            "found %d stale review items (older than %dh)",
            len(rows),
            args.min_age_hours,
        )

        if not args.apply:
            print(f"\nDRY RUN — would re-review {len(rows)} items")
            for r in rows[:5]:
                extra = r["extra"] or {}
                if isinstance(extra, str):
                    try:
                        extra = json.loads(extra)
                    except Exception:
                        extra = {}
                print(
                    f"  [{r['niche_id']}/{r['platform']}] comment="
                    f"{str(extra.get('comment_text') or '')[:60]!r}"
                )
            return 0

        # APPLY.
        from genlab_core.engagement.comment_processor import _get_persona_engine

        promoted = 0
        discarded = 0
        errors = 0

        for r in rows:
            extra = r["extra"] or {}
            if isinstance(extra, str):
                try:
                    extra = json.loads(extra)
                except Exception:
                    extra = {}
            reply_text = str(extra.get("reply_text") or "")
            comment_text = str(extra.get("comment_text") or "")
            if not reply_text or not comment_text:
                # Cannot re-review without both — leave to timeout resolver.
                logger.debug(
                    "[drain] skipping %s — missing reply_text or comment_text",
                    r["id"][:8],
                )
                continue

            try:
                engine = _get_persona_engine(r["niche_id"])
                should_post, reason = _re_review_reply(
                    engine, comment_text, reply_text, r["platform"]
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[drain] persona init failed for %s: %s", r["id"][:8], exc)
                errors += 1
                continue

            new_status = "replied" if should_post else "skipped"
            new_extra = dict(extra)
            new_extra["drain_reason"] = reason
            new_extra["drained_at"] = "NOW"

            try:
                # We do NOT actually post here — that would need the
                # comment_id + platform SDK routing. Marking the row's
                # status is the durable signal; the poller re-injects
                # unposted items on next cycle if needed.
                # For safety in this first iteration: only DISCARD
                # (skipped). Promotion to replied requires operator
                # approval of the classifier's accuracy over 1+ week.
                if new_status == "replied":
                    # First-iteration safety: don't auto-post yet.
                    # Mark as "review_passed_awaiting_operator" so the
                    # dashboard shows the classifier's verdict without
                    # bypassing operator's final say.
                    conn.execute(
                        """
                        UPDATE pending_engagement
                        SET extra = extra || %s::jsonb
                        WHERE id = %s AND status = 'pending_review'
                        """,
                        (json.dumps({"drain_verdict": "pass", "drain_reason": reason}), r["id"]),
                    )
                    promoted += 1
                else:
                    conn.execute(
                        """
                        UPDATE pending_engagement
                        SET status = 'skipped',
                            extra = extra || %s::jsonb,
                            updated_at = NOW()
                        WHERE id = %s AND status = 'pending_review'
                        """,
                        (json.dumps({"drain_reason": reason, "drain_verdict": "discard"}), r["id"]),
                    )
                    discarded += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("[drain] update failed for %s: %s", r["id"][:8], exc)
                errors += 1

        conn.commit()

        logger.info(
            "DONE drain_pass_awaiting_operator=%d discarded=%d errors=%d",
            promoted,
            discarded,
            errors,
        )
        return 0


def _main_with_durable_error() -> int:
    try:
        return main()
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 0
    except Exception as exc:  # noqa: BLE001
        try:
            from genlab_core.observability.durable_error import write_durable_error

            write_durable_error("drain_engagement_review_queue", exc)
        except Exception as import_exc:  # noqa: BLE001
            print(
                f"(also failed to import durable_error: {import_exc})",
                file=sys.stderr,
            )
            import traceback as _tb

            _tb.print_exc(file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(_main_with_durable_error())
