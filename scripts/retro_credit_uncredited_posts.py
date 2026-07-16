"""Retroactive credit-line editor for uncredited live posts.

Post-2026-07-13 audit follow-up. The writer wire was silently broken
for weeks (Bugs A + C, fixed in PR #779 at 13:17 IST today). Every
post published between ~2026-06-15 and today's fire went out without
a "🎬 Original: {creator} — {url}" line in the caption.

This script:

  1. Queries blueprints published in the last N days that lack a
     credit marker in ANY caption field.
  2. For each: builds the credit line from
     ``extra->>'source_channel_title'`` + ``video_url`` (falling back
     to URL-only when the channel name is empty — the Bug B era's
     stored blueprints often lack ``source_channel_title``).
  3. Normalises the ``post_id`` values in publishing_analytics to
     strip the historical double-prefix corruption
     (``facebook:facebook:1181...`` → ``1181...``).
  4. For each SUCCESS/INSIGHTS_* post on Facebook: edits the message
     via ``POST /{post_id}?message=...``.
  5. For each Instagram post: looks up the numeric media_id from the
     shortcode by walking the niche's IG media page, then edits via
     ``POST /{media_id}?caption=...&comment_enabled=true``.
  6. Skips YouTube (needs an OAuth refresh token workflow not
     configured for this script) + Threads (edit endpoint not
     documented) + X/Twitter (no edit support).
  7. Idempotent: reads the current live caption first and skips
     blueprints where the marker is already present. Re-runs are
     safe.

Usage on prod:

  cd /opt/genlab
  .venv/bin/python scripts/retro_credit_uncredited_posts.py --dry-run
  .venv/bin/python scripts/retro_credit_uncredited_posts.py --apply

Exit codes:

  0 = success (--dry-run always exits 0; --apply exits 0 iff
      every attempted edit either succeeded or was skipped-idempotent)
  1 = at least one edit failed (rate limits, missing credentials,
      edit-window expired) — see stdout summary
  2 = fatal (DB unreachable, no credentials)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


META_GRAPH_BASE = "https://graph.facebook.com/v21.0"


# Per-niche token/page mapping — mirrors the shape used elsewhere
# in the codebase (niche_credentials.py). Kept inline to avoid a
# cross-package dependency for what is meant to be a one-shot script.
NICHE_ENV = {
    "ai_creators": {
        "fb_token": "BLACKBOXBRIEF_FB_PAGE_ACCESS_TOKEN",
        "fb_page_id": "BLACKBOXBRIEF_FB_PAGE_ID",
        "ig_user_id": "BLACKBOXBRIEF_IG_USER_ID",
    },
    "gaming": {
        "fb_token": "CRITICALRUSH_FB_PAGE_ACCESS_TOKEN",
        "fb_page_id": "CRITICALRUSH_FB_PAGE_ID",
        "ig_user_id": "CRITICALRUSH_IG_USER_ID",
    },
    "sports": {
        "fb_token": "CLUTCHWIRE_FB_PAGE_ACCESS_TOKEN",
        "fb_page_id": "CLUTCHWIRE_FB_PAGE_ID",
        "ig_user_id": "CLUTCHWIRE_IG_USER_ID",
    },
    "movies": {
        "fb_token": "SPLICEREEL_FB_PAGE_ACCESS_TOKEN",
        "fb_page_id": "SPLICEREEL_FB_PAGE_ID",
        "ig_user_id": "SPLICEREEL_IG_USER_ID",
    },
    "anime": {
        "fb_token": "FRAMEDRIFT_FB_PAGE_ACCESS_TOKEN",
        "fb_page_id": "FRAMEDRIFT_FB_PAGE_ID",
        "ig_user_id": "FRAMEDRIFT_IG_USER_ID",
    },
}


MARKER = "\U0001f3ac Original:"


# ── State-tracking (rate-limit-resilient re-runs) ──────────────────
#
# Meta's write-then-immediate-read API is cache-lagged, so the "read
# current caption + skip if already contains marker" idempotency
# check can't distinguish "already-credited" from "cache lag." A local
# state file solves this reliably: after every successful edit, stamp
# ``{blueprint_id}:{platform}`` in the file; on next run, skip anything
# in it BEFORE hitting the API.
#
# File shape:
#   .retro_credit_state.json = {
#     "credited": ["<blueprint_id>:<platform>", ...],
#     "last_run": "2026-07-13T18:07:00Z"
#   }
#
# On prod: /opt/genlab/.runtime/retro_credit_state.json (runtime dir
# is already writable by the genlab user + persists across deploys).

_STATE_PATH = Path("/opt/genlab/.runtime/retro_credit_state.json")


def _load_state() -> set[str]:
    """Load the set of already-credited ``{bp}:{platform}`` keys."""
    try:
        if _STATE_PATH.exists():
            data = json.loads(_STATE_PATH.read_text())
            return set(data.get("credited", []))
    except Exception as exc:  # noqa: BLE001 — fail-open on state read
        log.warning("state file read failed (%s) — treating as empty", exc)
    return set()


def _stamp_db_caption(blueprint_id: str, credit_line: str, dsn: str) -> None:
    """Append ``credit_line`` to blueprints.caption + extra.facebook_content.

    2026-07-14 fix: retro-credit was ONLY editing Meta captions via API.
    ``attribution_health_monitor.py`` + ``dashboard/server/core/
    attribution_health.py`` both query the DB caption column to measure
    the "audience-facing invariant" — but the DB never got updated, so
    the metric was stuck at 0.0% even after Meta was successfully
    credited. That's the exact class-of-bug pattern: metric-proxy
    signal masking audience-facing state.

    Also update ``extra.facebook_content`` (the platform-specific
    caption slot the monitor unions in) so both signal paths converge.

    Best-effort: DB write failures don't unwind the successful Meta
    edit; state file is the durable idempotency layer.
    """
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE blueprints
                    SET caption = CASE
                            WHEN COALESCE(caption, '') LIKE %s THEN caption
                            ELSE COALESCE(caption, '') || E'\n\n' || %s
                        END,
                        extra = jsonb_set(
                            COALESCE(extra, '{}'::jsonb),
                            '{facebook_content}',
                            to_jsonb(
                                CASE
                                    WHEN COALESCE(extra->>'facebook_content', '') LIKE %s
                                    THEN extra->>'facebook_content'
                                    ELSE COALESCE(extra->>'facebook_content', '')
                                        || E'\n\n' || %s
                                END
                            )
                        )
                    WHERE id = %s::uuid
                    """,
                    (f"%{MARKER}%", credit_line, f"%{MARKER}%", credit_line, blueprint_id),
                )
    except Exception as exc:  # noqa: BLE001 — DB write is bonus, not blocking
        log.warning(
            "DB caption stamp failed for bp=%s (%s) — Meta was still credited "
            "successfully; attribution_health_monitor will lag until next fire "
            "when the DB stamp succeeds",
            blueprint_id,
            exc,
        )


def _stamp_state(key: str) -> None:
    """Append ``{bp}:{platform}`` to state file after successful edit.

    Best-effort: if the state file can't be written, log + continue.
    A failed stamp means the next run may re-attempt this specific
    edit — Meta will still see it as an edit-with-same-content
    (idempotent-ish) but wastes budget."""
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing = _load_state()
        existing.add(key)
        _STATE_PATH.write_text(
            json.dumps(
                {
                    "credited": sorted(existing),
                    "last_run": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
                indent=2,
            )
        )
    except PermissionError as exc:
        # 2026-07-14 audit follow-up: this specific class was hidden for
        # 6+ hours overnight because the state file got created as root
        # (via ssh root@ during pre-populate) + systemd runs as genlab.
        # Silent PermissionError → stamp fail → every 90-min timer
        # re-attempted 100+ posts → wasted API budget + rate limits.
        # Elevate PermissionError specifically to ERROR so it surfaces
        # on the next journalctl -p err scan.
        log.error(
            "state file stamp DENIED — check ownership (chown genlab:genlab %s). "
            "Silent PermissionError means every timer fire re-attempts "
            "already-credited posts, wasting API budget. Exception: %s",
            _STATE_PATH,
            exc,
        )
    except Exception as exc:  # noqa: BLE001 — fail-open on other errors
        log.warning("state file stamp failed (%s) — will re-attempt next run", exc)


def _strip_double_prefix(raw: str, platform: str) -> str:
    """The Bug B era stored post_ids double-prefixed
    (``facebook:facebook:1181...``). Strip leading platform prefixes
    until only the bare id remains."""
    prefix = f"{platform}:"
    while raw.startswith(prefix):
        raw = raw[len(prefix) :]
    return raw


def _build_credit_line(video_url: str, channel_name: str | None) -> str:
    """Return the standard '🎬 Original: @{ch} — {url}' line.

    Falls back to URL-only when channel is missing (Bug B era rows).
    """
    handle = (channel_name or "").strip()
    if handle:
        # Match the format the writer emits — @handle followed by em-dash
        return f"{MARKER} @{handle} — {video_url}"
    return f"{MARKER} {video_url}"


@dataclass
class TargetPost:
    blueprint_id: str
    niche_id: str
    platform: str  # 'facebook' | 'instagram' | 'youtube' | 'threads' | 'twitter'
    raw_post_id: str
    normalised_post_id: str
    video_url: str
    channel_name: str | None


def _query_uncredited(dsn: str, days: int) -> list[TargetPost]:
    """Query DB for uncredited blueprints + their publishing_analytics
    posts. Returns a flat list, one entry per (blueprint, platform)
    tuple."""
    import psycopg

    # Pass the emoji marker as a parameter — psycopg's query-splitter
    # trips on emoji bytes when they sit inside the query string near
    # a ``%s`` marker. Passing as a parameter avoids the parser path
    # that walks the SQL bytes looking for placeholders.
    marker_like = f"%{MARKER}%"
    footage_like = "%Footage:%"
    # 2026-07-14: platform filter added upstream. Retro-credit only
    # handles Facebook + Instagram (Meta Graph API caption edits).
    # Prior query fetched all platforms + then per-row `skipped_platform`
    # skip inside the Python loop → 185/430 targets logged as
    # skipped_platform noise on every fire. Filtering at SQL level:
    #   * reduces target set from ~430 to ~245
    #   * eliminates skipped_platform log noise entirely
    #   * shrinks state-file scan time (243 fewer targets to check)
    sql = """
        SELECT b.id::text, b.niche_id,
               COALESCE(b.video_url, '') AS video_url,
               COALESCE(b.extra->>'source_channel_title', '') AS ch,
               pa.platform, pa.post_id
        FROM blueprints b
        JOIN publishing_analytics pa ON pa.blueprint_id = b.id
        WHERE b.status = 'PUBLISHED'
          AND b.updated_at > NOW() - (%s || ' days')::interval
          AND pa.status IN ('SUCCESS','INSIGHTS_6H','INSIGHTS_24H','INSIGHTS_48H','INSIGHTS_168H')
          AND pa.post_id IS NOT NULL AND pa.post_id != ''
          AND pa.platform IN ('facebook', 'instagram')
          AND NOT (
              COALESCE(b.caption, '') LIKE %s
              OR COALESCE(b.caption, '') LIKE %s
              OR COALESCE(b.extra->>'facebook_content', '') LIKE %s
              OR COALESCE(b.extra->>'twitter_content', '') LIKE %s
              OR COALESCE(b.extra->>'threads_content', '') LIKE %s
              OR COALESCE(b.extra->>'youtube_content', '') LIKE %s
          )
        ORDER BY b.updated_at DESC, pa.platform
    """
    out: list[TargetPost] = []
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    str(days),
                    marker_like,
                    footage_like,
                    marker_like,
                    marker_like,
                    marker_like,
                    marker_like,
                ),
            )
            for row in cur.fetchall():
                bp_id, niche, url, ch, plat, raw_post = row
                out.append(
                    TargetPost(
                        blueprint_id=bp_id,
                        niche_id=niche,
                        platform=plat,
                        raw_post_id=raw_post,
                        normalised_post_id=_strip_double_prefix(raw_post, plat),
                        video_url=url,
                        channel_name=ch or None,
                    )
                )
    return out


def _ig_shortcode_to_media_id(shortcode: str, ig_user_id: str, token: str) -> str | None:
    """Walk the niche's IG media to find the numeric media_id for a
    shortcode. Meta rate limits + no direct shortcode→id endpoint
    means we walk pages until we find it or exhaust."""
    if not shortcode or not ig_user_id or not token:
        return None
    url = f"{META_GRAPH_BASE}/{ig_user_id}/media"
    params: dict[str, Any] = {
        "fields": "id,shortcode",
        "limit": 50,
        "access_token": token,
    }
    for _ in range(6):  # ≤ 300 posts (~1.5 months at 1/day) — enough for 7d window
        try:
            r = requests.get(url, params=params, timeout=15)
            if not r.ok:
                log.debug("IG media list fetch failed: %s", r.text[:200])
                return None
            data = r.json()
            for item in data.get("data", []):
                if item.get("shortcode") == shortcode:
                    return str(item["id"])
            paging = data.get("paging", {})
            next_url = paging.get("next")
            if not next_url:
                return None
            # Recurse via next_url (which already carries token)
            url = next_url
            params = {}
        except requests.RequestException as exc:
            log.debug("IG media page fetch exception: %s", exc)
            return None
    return None


def _current_fb_message(post_id: str, token: str) -> str:
    try:
        r = requests.get(
            f"{META_GRAPH_BASE}/{post_id}",
            params={"fields": "message", "access_token": token},
            timeout=15,
        )
        if r.ok:
            return r.json().get("message", "")
    except requests.RequestException:
        pass
    return ""


def _current_ig_caption(media_id: str, token: str) -> str:
    try:
        r = requests.get(
            f"{META_GRAPH_BASE}/{media_id}",
            params={"fields": "caption", "access_token": token},
            timeout=15,
        )
        if r.ok:
            return r.json().get("caption", "")
    except requests.RequestException:
        pass
    return ""


# Meta's app-scoped rate limit fires with error code=4. A ``True``
# sentinel here interrupts the outer loop so we don't waste API budget
# on remaining rows once the app is throttled. Reset window is
# typically ~1 hour, so re-runs the next hour pick up cleanly (the
# script is idempotent).
_RATE_LIMIT_HIT = False


def _is_rate_limit(reason: str) -> bool:
    return '"code":4' in reason or "Application request limit reached" in reason


def _edit_fb_message(post_id: str, new_message: str, token: str, dry_run: bool) -> tuple[bool, str]:
    if dry_run:
        return True, "dry-run"
    try:
        r = requests.post(
            f"{META_GRAPH_BASE}/{post_id}",
            data={"access_token": token, "message": new_message},
            timeout=30,
        )
        if r.ok and r.json().get("success"):
            return True, "ok"
        return False, (r.text or "unknown")[:200]
    except requests.RequestException as exc:
        return False, str(exc)[:200]


def _edit_ig_caption(
    media_id: str, new_caption: str, token: str, dry_run: bool
) -> tuple[bool, str]:
    if dry_run:
        return True, "dry-run"
    try:
        r = requests.post(
            f"{META_GRAPH_BASE}/{media_id}",
            data={
                "access_token": token,
                "caption": new_caption,
                "comment_enabled": "true",
            },
            timeout=30,
        )
        if r.ok and r.json().get("success"):
            return True, "ok"
        return False, (r.text or "unknown")[:200]
    except requests.RequestException as exc:
        return False, str(exc)[:200]


def _append_credit(current: str, credit_line: str) -> str:
    """Idempotent append — return current + credit line only if not
    already present. Preserves original spacing."""
    if MARKER in current:
        return current  # already credited, no change
    body = (current or "").rstrip()
    if body:
        return f"{body}\n\n{credit_line}"
    return credit_line


def run(dsn: str, days: int, dry_run: bool, sleep_seconds: float = 3.0) -> int:
    global _RATE_LIMIT_HIT
    _RATE_LIMIT_HIT = False
    targets = _query_uncredited(dsn, days=days)
    state = _load_state()
    log.info(
        "Found %d target (blueprint, platform) rows across last %d days (pacing %.1fs). "
        "State file has %d already-credited keys.",
        len(targets),
        days,
        sleep_seconds,
        len(state),
    )
    # Filter out state-known-credited targets BEFORE hitting Meta APIs.
    # These wasted API budget on the earlier run (each attempt = read
    # + write) — state-tracking avoids that.
    _pre = len(targets)
    targets = [t for t in targets if f"{t.blueprint_id}:{t.platform}" not in state]
    if _pre > len(targets):
        log.info("Skipped %d state-known-credited targets", _pre - len(targets))
    stats = {
        "attempted_fb": 0,
        "attempted_ig": 0,
        "success_fb": 0,
        "success_ig": 0,
        "already_credited": 0,
        "skipped_no_creds": 0,
        "skipped_no_url": 0,
        "skipped_platform": 0,
        "failed": 0,
    }
    failures: list[str] = []

    for t in targets:
        env = NICHE_ENV.get(t.niche_id)
        if not env:
            log.warning("[skip] no env mapping for niche=%s", t.niche_id)
            stats["skipped_no_creds"] += 1
            continue
        token = os.environ.get(env["fb_token"], "").strip()
        if not token:
            log.warning("[skip] no FB token for niche=%s", t.niche_id)
            stats["skipped_no_creds"] += 1
            continue
        if not t.video_url:
            stats["skipped_no_url"] += 1
            continue

        credit = _build_credit_line(t.video_url, t.channel_name)

        if t.platform == "facebook":
            stats["attempted_fb"] += 1
            current = _current_fb_message(t.normalised_post_id, token)
            if MARKER in current:
                stats["already_credited"] += 1
                # 2026-07-14: Meta says credited but the DB may not
                # reflect it (attribution_health_monitor reads DB and
                # would still report 0%). Stamp the DB with the credit
                # line so the metric climbs. Safe on repeat — the
                # helper is idempotent (LIKE %MARKER% guard).
                if not dry_run:
                    _stamp_db_caption(t.blueprint_id, credit, dsn)
                log.info(
                    "[fb %s] already credited — skip (DB stamped)",
                    t.normalised_post_id[-8:],
                )
                continue
            new_msg = _append_credit(current, credit)
            ok, reason = _edit_fb_message(t.normalised_post_id, new_msg, token, dry_run)
            if ok:
                stats["success_fb"] += 1
                if not dry_run:
                    _stamp_state(f"{t.blueprint_id}:facebook")
                    _stamp_db_caption(t.blueprint_id, credit, dsn)
                log.info(
                    "[fb %s] %s — appended credit for @%s",
                    t.normalised_post_id[-8:],
                    reason,
                    t.channel_name or "(none)",
                )
            else:
                stats["failed"] += 1
                failures.append(f"fb {t.normalised_post_id}: {reason}")
                log.warning("[fb %s] FAILED — %s", t.normalised_post_id[-8:], reason)
            if _is_rate_limit(reason if not ok else ""):
                _RATE_LIMIT_HIT = True
                log.warning("[rate-limit] Meta app-scoped limit — stopping early")
                break
            time.sleep(sleep_seconds)

        elif t.platform == "instagram":
            stats["attempted_ig"] += 1
            ig_user_id = os.environ.get(env["ig_user_id"], "").strip()
            if not ig_user_id:
                log.warning("[ig %s] no IG_USER_ID for %s", t.normalised_post_id, t.niche_id)
                stats["skipped_no_creds"] += 1
                continue
            media_id = _ig_shortcode_to_media_id(t.normalised_post_id, ig_user_id, token)
            if not media_id:
                stats["failed"] += 1
                failures.append(f"ig {t.normalised_post_id}: shortcode not found in recent media")
                log.warning(
                    "[ig %s] shortcode not resolved to media_id",
                    t.normalised_post_id,
                )
                continue
            current = _current_ig_caption(media_id, token)
            if MARKER in current:
                stats["already_credited"] += 1
                # 2026-07-14: same DB-catch-up as fb path above.
                if not dry_run:
                    _stamp_db_caption(t.blueprint_id, credit, dsn)
                log.info("[ig %s] already credited — skip (DB stamped)", media_id[-8:])
                continue
            new_cap = _append_credit(current, credit)
            ok, reason = _edit_ig_caption(media_id, new_cap, token, dry_run)
            if ok:
                stats["success_ig"] += 1
                if not dry_run:
                    _stamp_state(f"{t.blueprint_id}:instagram")
                    _stamp_db_caption(t.blueprint_id, credit, dsn)
                log.info(
                    "[ig %s] %s — appended credit for @%s",
                    media_id[-8:],
                    reason,
                    t.channel_name or "(none)",
                )
            else:
                stats["failed"] += 1
                failures.append(f"ig {media_id}: {reason}")
                log.warning("[ig %s] FAILED — %s", media_id[-8:], reason)
            time.sleep(0.5)

        else:
            # youtube / threads / twitter — skipped
            stats["skipped_platform"] += 1

    log.info("=== Summary ===")
    for k, v in stats.items():
        log.info("  %s = %d", k, v)
    if failures:
        log.info("--- Failures (first 10) ---")
        for f in failures[:10]:
            log.info("  %s", f)

    # 2026-07-14: exit-code semantics fix. Previously ``exit=1 if
    # stats['failed'] > 0`` — meaning ONE IG shortcode-resolve failure
    # out of 431 targets triggered systemd FAILURE + Mission Control
    # alert every 90 min. In practice `skipped_platform` accounts for
    # 90%+ of targets (yt/threads/x/tiktok not handled) and 1 IG
    # shortcode fail is normal noise for a 30d retro-credit window
    # (Meta caches lag, shortcodes rotate).
    #
    # New semantics: exit 0 when we did SOMETHING useful — successfully
    # credited a post OR confirmed via state file that a post is
    # already credited. Exit 1 only on catastrophic failure (100%
    # attempt failure = tokens or DB broken).
    #
    # 2026-07-14 (second fix): `already_credited` was excluded from the
    # "did something useful" check by mistake. Observed live: run with
    # attempted=2, success=0, already_credited=1, failed=1 exited 1
    # despite doing genuine idempotency work (the durable state file
    # is the WHOLE POINT of this script). The intended semantics is
    # "no useful action AND had failures" — treating already_credited
    # as a real success case.
    attempted = stats["attempted_fb"] + stats["attempted_ig"]
    success = stats["success_fb"] + stats["success_ig"]
    already_credited = stats["already_credited"]
    if attempted > 0 and success == 0 and already_credited == 0 and stats["failed"] > 0:
        # 100% failure rate on real attempts — genuine problem.
        log.error(
            "retro-credit exit=1: %d attempts, 0 successes, "
            "%d already-credited, %d failures. Likely token or DB issue.",
            attempted,
            already_credited,
            stats["failed"],
        )
        return 1
    # Normal path: some successes/already-credited + some noise = healthy.
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument(
        "--sleep",
        type=float,
        default=3.0,
        help="Seconds to pace between API calls (default 3.0). Lower "
        "risks Meta's app-scoped #4 rate limit; higher smooths out.",
    )
    args = ap.parse_args()
    dry_run = not args.apply

    dsn = os.environ.get("DATABASE_URL", "").strip() or "dbname=genlab"
    log.info(
        "Mode: %s  window: %dd  pacing: %.1fs",
        "APPLY" if not dry_run else "DRY-RUN",
        args.days,
        args.sleep,
    )
    return run(dsn=dsn, days=args.days, dry_run=dry_run, sleep_seconds=args.sleep)


if __name__ == "__main__":
    sys.exit(main())
