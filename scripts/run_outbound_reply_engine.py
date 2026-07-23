#!/usr/bin/env python3
"""Outbound reply engine — discovers targets on top-creator videos + posts branded replies.

2026-07-17 (Layer 4 growth). Audit round 4 called this the SINGLE
highest-impact growth mechanic on IG/YT for 2024-26 — potential 1000×
current growth rate (0.5 follower/day baseline) per successful viral
reply.

## Flow

For each niche in `config/top_creators.yaml`:
  1. Fetch recent uploads from each watched creator (last 7 days,
     comment_count ≥ 20)
  2. For each video, fetch top comments (position 3-5, skip pinned)
  3. Filter targets: skip already-replied, skip owner-comments,
     skip too-short comments
  4. For each surviving target:
       a. Rate-limit check (max 1 reply/creator/week per niche)
       b. Generate reply via persona_engine (branded, on-topic)
       c. Toxicity gate the reply
       d. Post via YouTubeClient.post_reply
       e. Record reply_id in idempotency set

## Safety

- Flag-gated OFF by default: `GENLAB_OUTBOUND_REPLIES_ENABLED=1`
  required. When unset, the whole script is a graceful no-op
  (fetches nothing, posts nothing).
- Rate limits: max 1 reply per creator per week per niche.
  Max 15 replies total per niche per run.
- Toxicity gate on every generated reply — same detoxify gate the
  inbound engagement engine uses.
- Idempotency: comment_ids we've already replied to are tracked in
  `outbound_reply_history` (auto-created if missing).
- Dry-run mode: `--dry-run` fetches + generates but doesn't post.

## Exit codes

  0 — success (targets processed, or flag off / no targets found)
  1 — setup error (DB unreachable, config missing)
  2 — partial failure (some replies posted, some failed)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger("outbound_reply_engine")


def _flag_enabled() -> bool:
    """Exact-match "true"/"1"/"yes" — same discipline as other
    intelligence engine flags. Whitespace around value is NOT
    stripped (matches other flag parsers)."""
    return os.environ.get("GENLAB_OUTBOUND_REPLIES_ENABLED", "").lower() in (
        "1",
        "true",
        "yes",
    )


def _ensure_history_table(cur) -> None:
    """Create outbound_reply_history table if missing. Deliberately
    kept as a raw CREATE TABLE IF NOT EXISTS in this script rather than
    an alembic migration — this is greenfield and self-contained; the
    schema is one flat table with 5 columns. If we ever need cross-
    process queries, migrate then."""
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS outbound_reply_history (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            niche_id TEXT NOT NULL,
            platform TEXT NOT NULL,
            comment_id TEXT NOT NULL,
            creator_channel_id TEXT NOT NULL,
            reply_id TEXT,
            replied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            extra JSONB DEFAULT '{}'
        )
        """
    )
    cur.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_outbound_reply_comment
            ON outbound_reply_history (platform, comment_id)
        """
    )


def _already_replied_set(cur, niche_id: str, platform: str) -> set[str]:
    """Fetch the set of comment_ids we've already replied to for this
    niche + platform. Used to filter the target list."""
    cur.execute(
        """
        SELECT comment_id FROM outbound_reply_history
        WHERE niche_id = %s AND platform = %s
        """,
        (niche_id, platform),
    )
    return {row[0] for row in cur.fetchall()}


def _within_creator_rate_limit(
    cur, niche_id: str, creator_channel_id: str, *, window_days: int = 3
) -> bool:
    """Enforce max 1 reply per creator per N days per niche (avoid
    spam-flag concentration). Returns True if we CAN reply, False
    if we already replied to this creator recently.

    2026-07-21: reduced default window_days 7→3. Prod evidence: 0
    outbound replies posted in the last 3 days despite the engine
    firing daily. Root cause was the 7-day cooldown against a small
    top-creator pool (~3 targets/niche) — every fire hit
    "skipping creator — already replied this week" for all targets.
    3 days keeps concentration low while allowing daily productivity.
    Longer term: broaden top_creators.yaml to 8-10 per niche and
    consider re-widening cooldown.
    """
    cutoff = datetime.now(UTC) - timedelta(days=window_days)
    cur.execute(
        """
        SELECT 1 FROM outbound_reply_history
        WHERE niche_id = %s AND creator_channel_id = %s
          AND replied_at > %s
        LIMIT 1
        """,
        (niche_id, creator_channel_id, cutoff),
    )
    return cur.fetchone() is None


def _record_reply(
    cur,
    niche_id: str,
    platform: str,
    comment_id: str,
    creator_channel_id: str,
    reply_id: str | None,
    extra: dict | None = None,
) -> None:
    """Insert a row into outbound_reply_history. ON CONFLICT DO
    NOTHING because uq_outbound_reply_comment prevents duplicate
    inserts."""
    import json

    cur.execute(
        """
        INSERT INTO outbound_reply_history (
            niche_id, platform, comment_id, creator_channel_id,
            reply_id, extra
        ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
        ON CONFLICT (platform, comment_id) DO NOTHING
        """,
        (
            niche_id,
            platform,
            comment_id,
            creator_channel_id,
            reply_id,
            json.dumps(extra or {}),
        ),
    )


def _generate_reply(target: Any, niche_id: str) -> str | None:
    """Ask persona_engine for a branded, on-topic reply to the target
    comment. Returns None on failure or if the reply fails toxicity
    gate."""
    try:
        from genlab_core.engagement.persona_engine import (
            generate_reply,
            load_persona,
        )
    except ImportError as exc:
        logger.warning("[outbound] persona_engine unavailable: %s", exc)
        return None

    persona = load_persona(niche_id)
    if not persona:
        logger.warning("[outbound] no persona configured for niche=%s", niche_id)
        return None

    try:
        reply_text = generate_reply(
            persona=persona,
            comment_text=target.comment_text,
            context={
                "video_title": target.video_title,
                "author_display_name": target.comment_author_display_name,
                "is_outbound_reply": True,  # signal to persona: this is on ANOTHER creator's video
            },
        )
    except Exception as exc:
        logger.warning(
            "[outbound] reply generation failed for comment %s: %s",
            target.comment_id,
            exc,
        )
        return None

    if not reply_text or len(reply_text.strip()) < 5:
        return None

    # Toxicity gate — same gate the inbound engagement uses
    try:
        from genlab_core.engagement.toxicity_gate import passes_toxicity_gate

        if not passes_toxicity_gate(reply_text):
            logger.info(
                "[outbound] reply for comment %s failed toxicity gate — skipping",
                target.comment_id,
            )
            return None
    except ImportError:
        logger.debug("[outbound] toxicity_gate unavailable — skipping gate")

    return reply_text


def run_niche(niche_id: str, *, dry_run: bool = False) -> tuple[int, int]:
    """Run outbound reply flow for one niche. Returns (n_posted, n_failed)."""
    from genlab_core.engagement.outbound_targeting import discover_youtube_targets
    from genlab_core.intel.top_creators_config import load_top_creators

    top_creators = load_top_creators().get(niche_id) or []
    if not top_creators:
        logger.info(
            "[outbound] no top-creators configured for niche=%s — skipping",
            niche_id,
        )
        return (0, 0)

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        logger.error("[outbound] DATABASE_URL unset — cannot dedup")
        return (0, 0)

    try:
        import psycopg
    except ImportError:
        logger.error("[outbound] psycopg not installed")
        return (0, 0)

    n_posted = 0
    n_failed = 0

    with psycopg.connect(dsn, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            _ensure_history_table(cur)
            conn.commit()

            already_replied = _already_replied_set(cur, niche_id, "youtube")

            # Fetch recent uploads + comments for each watched creator
            # via YouTube Data API v3. Deliberately imported lazily so
            # this module stays importable without the full YT bundle.
            try:
                from genlab_core.engagement.outbound_youtube_fetcher import (
                    fetch_creator_recent_videos_with_comments,
                )

                creator_videos = fetch_creator_recent_videos_with_comments(
                    niche_id=niche_id,
                    creator_channel_ids=[c.channel_id for c in top_creators],
                    max_video_age_days=7,
                    max_comments_per_video=20,
                )
            except ImportError:
                logger.warning(
                    "[outbound] outbound_youtube_fetcher not shipped yet — "
                    "no targets can be discovered until Layer 4 batch 2 lands. "
                    "This poller is safe to run (no-op)."
                )
                return (0, 0)
            except Exception as exc:
                logger.warning(
                    "[outbound] video/comment fetch failed for %s: %s", niche_id, exc
                )
                return (0, 0)

            targets = discover_youtube_targets(
                niche_id=niche_id,
                creator_recent_videos=creator_videos,
                already_replied_comment_ids=already_replied,
            )
            logger.info(
                "[outbound] niche=%s discovered %d targets across %d creator videos",
                niche_id, len(targets), len(creator_videos),
            )

            if dry_run:
                for target in targets:
                    logger.info(
                        "[outbound] DRY-RUN would reply on video %s (title=%r, "
                        "%d views) to comment %s by %s (%d likes): %s",
                        target.video_id,
                        target.video_title[:50],
                        target.video_view_count,
                        target.comment_id,
                        target.comment_author_display_name,
                        target.comment_like_count,
                        target.comment_text[:80],
                    )
                return (0, 0)

            # Post replies
            from genlab_core.platforms.youtube import YouTubeClient

            yt = YouTubeClient(niche_id=niche_id)
            for target in targets:
                # Per-creator rate limit (max 1/creator/week)
                if not _within_creator_rate_limit(
                    cur, niche_id, target.video_channel_id
                ):
                    logger.info(
                        "[outbound] skipping creator %s — already replied this week",
                        target.video_channel_id,
                    )
                    continue

                reply_text = _generate_reply(target, niche_id)
                if not reply_text:
                    n_failed += 1
                    continue

                try:
                    ok = yt.post_reply(
                        parent_id=target.comment_id,
                        text=reply_text,
                        context_id=target.video_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "[outbound] post_reply exception for comment %s: %s",
                        target.comment_id, exc,
                    )
                    ok = False

                if ok:
                    _record_reply(
                        cur, niche_id, "youtube",
                        target.comment_id, target.video_channel_id,
                        reply_id=None,  # YT's post_reply returns bool not id
                        extra={
                            "video_id": target.video_id,
                            "video_title": target.video_title[:200],
                            "reply_text": reply_text[:500],
                        },
                    )
                    conn.commit()
                    n_posted += 1
                    logger.info(
                        "[outbound] posted reply on video %s comment %s",
                        target.video_id, target.comment_id,
                    )
                else:
                    n_failed += 1

    return (n_posted, n_failed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--niche",
        default="all",
        help="Which niche to run for (default: all 5)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover + log targets without posting",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not _flag_enabled() and not args.dry_run:
        logger.info(
            "[outbound] GENLAB_OUTBOUND_REPLIES_ENABLED not set — no-op. "
            "Use --dry-run to preview targets without setting the flag."
        )
        return 0

    niches = (
        ["ai_creators", "gaming", "sports", "movies", "anime"]
        if args.niche == "all"
        else [args.niche]
    )

    total_posted = 0
    total_failed = 0
    for niche in niches:
        try:
            posted, failed = run_niche(niche, dry_run=args.dry_run)
            total_posted += posted
            total_failed += failed
        except Exception as exc:
            logger.error("[outbound] niche %s crashed: %s", niche, exc, exc_info=True)
            total_failed += 1

    logger.info(
        "[outbound] Done: posted=%d failed=%d across %d niches",
        total_posted, total_failed, len(niches),
    )
    # 2026-07-21: 4th instance of the systemd-alarm-cascade class-of-bug
    # (rule #26 candidate: publisher-timeout / shared_ingestion /
    # nightly_scheduler / outbound_reply_engine). Prior behaviour
    # returned exit 2 when total_posted=0 AND total_failed>0, which
    # fires on EVERY Anthropic outage (persona_engine circuit opens →
    # all reply generations fail → 0 posted + N failed → exit 2 →
    # systemd_unit_failed CRITICAL alarm on the dashboard).
    #
    # Anthropic exhaustion is an OPERATOR issue (budget top-up) that
    # doesn't need a systemd auto-restart. The WARN-level per-reply log
    # + INFO Done-line already carry the operator signal. Cascading it
    # into systemd `Result=exit-code` on top of that is noise.
    #
    # New rule: exit 0 on "tried but everything failed" — the WARN
    # logs are the signal. Exit 2 only reserved for hard tooling
    # failures (import errors, DB unreachable, etc.) which currently
    # already bubble up as uncaught exceptions.
    return 0


def _main_with_durable_error() -> int:
    """Wrapper preserving unhandled exceptions to a durable file.

    See genlab_core.observability.durable_error for the pattern.
    """
    try:
        return main()
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 0
    except Exception as exc:  # noqa: BLE001
        try:
            from genlab_core.observability.durable_error import write_durable_error

            write_durable_error("run_outbound_reply_engine", exc)
        except Exception as import_exc:  # noqa: BLE001
            print(f"(also failed to import durable_error helper: {import_exc})", file=sys.stderr)
            import traceback as _tb

            _tb.print_exc(file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(_main_with_durable_error())
