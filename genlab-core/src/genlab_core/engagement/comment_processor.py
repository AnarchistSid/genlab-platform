"""Core engagement logic: receives an event dict from a Dramatiq task,
decides whether to reply/like, generates the reply, and posts it.

Idempotency is non-negotiable. The YouTube poller runs every 5 minutes.
Without a "already replied" check, the same comment receives multiple
replies before the rate limiter catches up. Uses a simple append-only
JSONL file keyed by (comment_id, platform) as the replied-set.

Confidence routing (hybrid auto-reply):
  auto    — confidence >= 0.85 AND toxicity < 0.15 AND safe pattern AND <100 chars
  review  — confidence >= 0.5 AND toxicity < 0.3
  discard — low confidence OR high toxicity
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Literal

import dramatiq

from genlab_core.engagement.persona_engine import PersonaEngine
from genlab_core.engagement.persona_schema import NichePersona
from genlab_core.engagement.rate_limiter import EngagementRateLimiter
from genlab_core.engagement.spam_filter import is_spam
from genlab_core.engagement.timing import human_delay
from genlab_core.engagement.toxicity_gate import ToxicityGate
from genlab_core.utils.env import get_agent_root

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Confidence routing — hybrid auto-reply
# ---------------------------------------------------------------------------

# Safe patterns for auto-reply. If the generated reply matches any of these
# AND confidence/toxicity thresholds are met, the reply is posted immediately.
SAFE_PATTERNS = [
    re.compile(r"^(thanks|thank you|glad you (liked|enjoyed)|appreciate)", re.I),
    re.compile(r"^(right\??|ikr|fr|exactly|so true|facts)", re.I),
    re.compile(r"^(check out|watch|subscribe|follow)", re.I),
    re.compile(r"^[\U0001F300-\U0001F9FF\u2600-\u27BF\s!]+$"),  # emoji-only
]


def _is_safe_reply(text: str) -> bool:
    """Return True if the reply text matches a known-safe pattern.

    Safe replies are short (< 100 chars) acknowledgments, agreements,
    CTAs, or emoji-only messages that carry minimal risk of controversy.
    """
    if len(text) > 100:
        return False
    return any(p.search(text.strip()) for p in SAFE_PATTERNS)


def classify_reply_action(
    reply_text: str,
    confidence: float,
    toxicity: float,
) -> Literal["auto", "review", "discard"]:
    """Classify a generated reply into an action tier.

    Tiers:
      auto    — post immediately (high confidence, low toxicity, safe pattern)
      review  — queue for human approval
      discard — log and drop

    Args:
        reply_text: The generated reply text.
        confidence: Persona engine confidence score (0.0-1.0).
        toxicity: Outbound toxicity score (0.0-1.0).

    Returns:
        One of "auto", "review", or "discard".
    """
    if toxicity >= 0.3:
        return "discard"
    if confidence < 0.5:
        return "discard"
    if confidence >= 0.85 and toxicity < 0.15 and _is_safe_reply(reply_text):
        return "auto"
    return "review"


_BOT_DISCLOSURE_SUFFIX = " [automated reply]"


def _append_bot_disclosure(text: str, max_len: int | None = None) -> str:
    """Append bot disclosure suffix to a reply for transparency.

    Ensures the disclosure is not duplicated if already present. When
    ``max_len`` is given, the REPLY (never the disclosure) is trimmed so the
    total stays within the platform's character budget (R-78): the LLM
    generates up to ``max_length_chars`` but the suffix is appended afterward,
    so without this the reply+suffix could overflow tight limits (X 280,
    Threads 500) and be rejected.
    """
    text = text.rstrip()
    if text.endswith("[automated reply]"):
        return text
    if max_len is not None and len(text) + len(_BOT_DISCLOSURE_SUFFIX) > max_len:
        keep = max(0, max_len - len(_BOT_DISCLOSURE_SUFFIX))
        text = text[:keep].rstrip()
    return text + _BOT_DISCLOSURE_SUFFIX


# Per-platform reply rate caps (actions per hour).
RATE_CAPS: dict[str, int] = {
    "instagram": 20,
    "youtube": 10,
    "facebook": 20,
    "x_twitter": 4,  # 50/day / ~12 active hours
    "threads": 3,  # 15/day / ~5 active session hours
}

# Token refill interval (milliseconds) per platform, used as the
# dramatiq retry min_backoff so a rate-limited message waits the
# expected time for a token to free up instead of bouncing through
# dramatiq's default exponential backoff (which capped out before
# the slowest platforms — threads, x_twitter — could refill even
# one token, multiplying every rate-limited message into 4 errors).
RATE_REFILL_MS: dict[str, int] = {
    platform: max(60_000, int(3_600_000 / max(rate, 1))) for platform, rate in RATE_CAPS.items()
}

_rate_limiter = EngagementRateLimiter(RATE_CAPS)

# Module-level singleton — avoids re-loading the Detoxify model on every call
_toxicity_gate = ToxicityGate()


# 2026-06-21 (perf): cache PersonaEngine per niche_id. Was rebuilt on every
# reply — ``_load_persona`` re-read + parsed the persona YAML on each call,
# and ``PersonaEngine.__init__`` rebuilt the (deterministic) system prompt.
# On the hot engagement-worker path (multiple replies/min per platform) this
# was wasted disk + CPU. Same singleton pattern as ``_toxicity_gate`` above;
# safe under Dramatiq concurrency for the same reason (the dict-set race is
# benign: worst case is two replies for the same niche each build a fresh
# engine, one wins and the other is GC'd — no corruption, no data loss).
_persona_engine_cache: dict[str, PersonaEngine] = {}


def _get_persona_engine(niche_id: str) -> PersonaEngine:
    """Return a cached PersonaEngine for the niche, building on first request.

    Tests that need a clean slate can ``_persona_engine_cache.clear()``.
    """
    engine = _persona_engine_cache.get(niche_id)
    if engine is None:
        persona = _load_persona(niche_id)
        engine = PersonaEngine(persona=persona, toxicity_gate=_toxicity_gate)
        _persona_engine_cache[niche_id] = engine
    return engine


_backlog_client_singleton = None


def _get_backlog_client():
    """Lazily load BacklogClient singleton. Returns None if not configured."""
    global _backlog_client_singleton
    if _backlog_client_singleton is None:
        try:
            from genlab_core.http.backlog_client import BacklogClient

            _backlog_client_singleton = BacklogClient()
        except Exception:
            return None
    return _backlog_client_singleton


def _get_agent_root() -> Path:
    """Resolve AGENT_ROOT — delegates to shared utility."""
    return get_agent_root()


# Platform post IDs are alphanumeric (+ _ -); validate before interpolating into
# a lookup formula (R-76 + defends against R-60-style formula injection).
_POST_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def _resolve_post_context(post_id: str, platform: str) -> str:
    """Resolve the original post's hook/title as reply context (R-76).

    Engagement replies previously passed ``post_context=""`` so the reply LLM
    never knew what the clip was about — producing generic/hallucinated replies.
    Resolve it: post_id → publishing_analytics → blueprint → title + hook.
    Fail-open (empty string) on any error; the persona handles "" gracefully.
    """
    if not post_id or not _POST_ID_RE.match(post_id):
        return ""
    try:
        bl = _get_backlog_client()
        if bl is None:
            return ""
        recs = bl.publishing_analytics.all(
            formula=f"AND({{platform}}='{platform}', SEARCH('{post_id}', {{post_id}}))",
            max_records=1,
        )
        bp_id = recs[0].get("fields", {}).get("blueprint_id", "") if recs else ""
        if not bp_id or not _POST_ID_RE.match(str(bp_id)):
            return ""
        bps = bl.blueprints.all(formula=f"{{blueprint_id}}='{bp_id}'", max_records=1)
        if not bps:
            return ""
        fields = bps[0].get("fields", {})
        title = (fields.get("title") or "").strip()
        hook = (fields.get("hook") or fields.get("hook_text") or "").strip()
        return " — ".join(p for p in (title, hook) if p)[:300]
    except Exception as exc:
        logger.debug("[engagement] post-context resolution failed for %s: %s", post_id, exc)
        return ""


def _replied_set_path() -> Path:
    """Path to the append-only idempotency log."""
    return _get_agent_root() / ".engagement_replied.jsonl"


def _load_replied_set() -> set:
    """Load replied (comment_id, platform) pairs from the JSONL file.

    Returns a set of ``json.dumps({"c": ..., "p": ...})`` strings for O(1)
    membership checks. Only loads records from the last 30 days to prevent
    unbounded growth. Triggers rotation when file exceeds 50K lines.
    """
    from datetime import UTC, timedelta
    from datetime import datetime as _dt

    path = _replied_set_path()
    if not path.exists():
        return set()
    try:
        result: set[str] = set()
        cutoff = (_dt.now(UTC) - timedelta(days=30)).isoformat()
        total_lines = 0
        with open(path) as f:
            for line in f:
                total_lines += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    # Skip records older than 30 days
                    ts = rec.get("ts", "")
                    if ts and ts < cutoff:
                        continue
                    result.add(json.dumps({"c": rec["c"], "p": rec["p"]}))
                except (json.JSONDecodeError, KeyError):
                    result.add(line)

        # Rotate file if it exceeds 50K lines
        if total_lines > 50_000:
            _rotate_replied_file(path, cutoff)

        return result
    except OSError:
        return set()


def _rotate_replied_file(path: Path, cutoff_iso: str) -> None:
    """Rewrite the replied JSONL file keeping only records newer than cutoff."""
    try:
        kept: list[str] = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("ts", "") >= cutoff_iso:
                        kept.append(line)
                except (json.JSONDecodeError, KeyError):
                    pass
        with open(path, "w") as f:
            f.write("\n".join(kept) + "\n" if kept else "")
        logger.info("[Engagement] Rotated replied file: kept %d records", len(kept))
    except OSError as e:
        logger.warning("[Engagement] Replied file rotation failed: %s", e)


# Module-level cache — populated once per batch via _ensure_replied_set_loaded()
_replied_set_cache: set | None = None


def _ensure_replied_set_loaded() -> set:
    """Return the cached replied set, loading it on first call."""
    global _replied_set_cache
    if _replied_set_cache is None:
        _replied_set_cache = _load_replied_set()
    return _replied_set_cache


def _invalidate_replied_cache() -> None:
    """Reset the cache so the next check reloads from disk."""
    global _replied_set_cache
    _replied_set_cache = None


def _has_replied(comment_id: str, platform: str) -> bool:
    """Check if we have already replied to this (comment_id, platform) pair.

    Uses a pre-loaded set for O(1) lookups instead of scanning the file
    on every call.
    """
    needle = json.dumps({"c": comment_id, "p": platform})
    return needle in _ensure_replied_set_loaded()


def _mark_replied(comment_id: str, platform: str) -> None:
    """Append an idempotency record. Uses file-level locking for safety."""
    from datetime import UTC
    from datetime import datetime as _dt

    path = _replied_set_path()
    # Cache key uses only (c, p) for O(1) lookup compatibility with _has_replied
    cache_key = json.dumps({"c": comment_id, "p": platform})
    # File record includes ISO timestamp so the dashboard can filter by date
    record_str = json.dumps({"c": comment_id, "p": platform, "ts": _dt.now(UTC).isoformat()})
    record = record_str + "\n"
    # Update in-memory cache so subsequent checks within the same batch see it
    cache = _ensure_replied_set_loaded()
    cache.add(cache_key)
    try:
        with open(path, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(record)
            fcntl.flock(f, fcntl.LOCK_UN)
    except OSError as e:
        logger.warning("Engagement: could not write idempotency record: %s", e)


def _load_persona(niche_id: str) -> NichePersona:
    """Load persona YAML. Raises FileNotFoundError if absent.

    Resolution order:

    1. Channel operator persona at ``<niche-root>/config/persona.yaml`` —
       gitignored by design (operators tune the voice per-deploy).
    2. Same path via the legacy AGENT_ROOT fallback.
    3. Packaged fallback at ``personas/{canonical_niche_id}.yaml`` — this
       is the SAFETY net that prevents 100% reply failure when (1)+(2)
       are missing from a fresh deploy.

    Steps (3) uses :func:`normalize_niche` so legacy IDs like ``ai_news``
    resolve to the canonical packaged file ``ai_creators.yaml``. Without
    this normalisation a Sprint-47-style rename could silently break
    every engagement reply for the affected niche (caught in prod on
    2026-06-20 — ``ai_creators.yaml`` was missing from the packaged
    fallback for months because the rename was incomplete).
    """
    import yaml

    from genlab_core.pipeline.cli import NICHE_DIR_NAMES
    from genlab_core.publishing.niche_validation import normalize_niche

    personas_dir = Path(__file__).parent / "personas"
    candidate_paths: list[Path] = []

    # Resolve the correct channel directory for this niche_id
    # (AGENT_ROOT may point to the wrong channel when the engagement
    # worker processes comments across multiple niches)
    try:
        genlab_root = _get_agent_root().parent
        channel_dir = NICHE_DIR_NAMES.get(niche_id)
        if channel_dir:
            candidate_paths.append(genlab_root / channel_dir / "config" / "persona.yaml")
    except Exception:
        pass

    # Also try AGENT_ROOT directly (legacy fallback)
    try:
        agent_root = _get_agent_root()
        candidate_paths.append(agent_root / "config" / "persona.yaml")
    except Exception:
        pass

    # Packaged fallback. Use the canonical ID so legacy aliases
    # (ai_news -> ai_creators) resolve to the right packaged file
    # instead of falling through to FileNotFoundError.
    canonical_id = normalize_niche(niche_id)
    candidate_paths.append(personas_dir / f"{canonical_id}.yaml")
    # If the caller passed a legacy alias, also try the alias filename
    # for back-compat with any operator who renamed their persona file
    # from canonical to alias (or never renamed it from alias to canonical).
    if canonical_id != niche_id:
        candidate_paths.append(personas_dir / f"{niche_id}.yaml")

    for path in candidate_paths:
        if path.exists():
            with open(path) as f:
                return NichePersona.model_validate(yaml.safe_load(f))
    raise FileNotFoundError(
        f"No persona.yaml found for niche '{niche_id}'. "
        f"Searched: {[str(p) for p in candidate_paths]}"
    )


def _queue_for_review(
    *,
    comment_id: str,
    platform: str,
    niche_id: str,
    comment_text: str,
    reply_text: str,
    author: str = "unknown",
    confidence: float = 0.0,
    tox_score: float = 0.0,
) -> None:
    """Write a pending reply to the dashboard's pending_replies.json file.

    Uses the same format as the dashboard's engagement API so the review
    UI can display and approve/reject these entries.
    """
    import uuid
    from datetime import UTC, datetime

    pending_file = _get_agent_root() / ".tmp" / "pending_replies.json"
    pending_file.parent.mkdir(parents=True, exist_ok=True)

    # Load existing entries
    entries: list[dict] = []
    if pending_file.is_file():
        try:
            with open(pending_file) as f:
                data = json.load(f)
            if isinstance(data, list):
                entries = data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Engagement: could not load pending_replies.json: %s", exc)

    # Append new entry in the dashboard's expected format
    entry = {
        "id": str(uuid.uuid4()),
        "comment_id": comment_id,
        "platform": platform,
        "niche_id": niche_id,
        "comment_text": comment_text,
        "reply_text": reply_text,
        "author": author,
        "status": "pending",
        "created_at": datetime.now(UTC).isoformat(),
        "reviewed_at": None,
        "confidence": round(float(confidence), 3),
        "tox_score": round(float(tox_score), 3),
    }
    entries.append(entry)

    try:
        with open(pending_file, "w") as f:
            json.dump(entries, f, indent=2)
    except OSError as exc:
        logger.error("Engagement: could not write pending_replies.json: %s", exc)


def process_reply_event(event: dict) -> None:
    """Full reply pipeline for one comment event:

    1. Idempotency check  — skip if already replied
    2. Spam filter        — fast regex, no model
    3. Inbound toxicity   — Detoxify gate
    4. Rate limit check   — token bucket per platform
    5. Persona reply gen  — LLM + outbound toxicity gate
    6. Timing jitter      — log-normal human delay
    7. Platform API call  — post the reply
    8. Mark replied       — idempotency record
    """
    comment_id = event["comment_id"]
    comment_text = event.get("comment_text", "")
    platform = event["platform"]
    niche_id = event["niche_id"]
    post_id = event["post_id"]
    post_context = event.get("post_context", "")
    # R-76: dispatchers pass post_context="" — resolve the original post's
    # hook/title so the reply LLM knows what the clip was about.
    if not post_context:
        post_context = _resolve_post_context(post_id, platform)

    # Sanitize external comment text against prompt injection
    from genlab_core.cache.text_sanitizer import check_for_injection

    if check_for_injection(comment_text):
        logger.warning(
            "Engagement: injection pattern in comment %s — skipping LLM reply",
            comment_id,
        )
        return

    # 1. Idempotency
    if _has_replied(comment_id, platform):
        logger.info("Engagement: already replied to %s on %s, skipping", comment_id, platform)
        return

    # Same idempotency for comments we previously decided to skip
    # (spam or toxic) — without this, the poller re-queues the same
    # comment indefinitely. 2026-05-21 forensics: 2 toxic comments
    # were getting reprocessed every ~10 minutes for days.
    if _has_replied(f"skip:{comment_id}", platform):
        logger.debug(
            "Engagement: previously skipped %s on %s, ignoring re-queue",
            comment_id,
            platform,
        )
        return

    # Likewise for comments already queued for human review — without
    # this the same comment gets a fresh persona invocation (Anthropic
    # call) every poller cycle while sitting in the review queue.
    if _has_replied(f"review:{comment_id}", platform):
        logger.debug(
            "Engagement: previously queued %s for review on %s, ignoring re-queue",
            comment_id,
            platform,
        )
        return

    # Record to SharePoint (optional — fails gracefully)
    bl = _get_backlog_client()
    sp_item_id = None
    if bl:
        sp_item_id = bl.write_pending_engagement(
            {
                "comment_id": comment_id,
                "platform": platform,
                "post_id": post_id,
                "text": comment_text,
                "author_name": event.get("author_name", ""),
                "created_at": event.get("created_at", ""),
                "niche_id": niche_id,
            }
        )

    # 2. Spam filter
    if is_spam(comment_text):
        logger.info("Engagement: skipping spam comment %s", comment_id)
        if bl and sp_item_id:
            bl.update_engagement_status(sp_item_id, "skipped")
        # Record the skip so the next poller pass doesn't re-queue the
        # same comment 10 minutes from now. Without this the engagement
        # worker spins forever on the same handful of unrepliable
        # comments — 2026-05-21 forensics found 2 toxic comments being
        # re-evaluated every poller cycle, log lines confirming "tox=
        # 0.95" → skip, again, repeat.
        _mark_replied(f"skip:{comment_id}", platform)
        return

    # 3. Inbound toxicity gate
    result = _toxicity_gate.check_inbound(comment_text)
    if result.is_toxic:
        logger.info(
            "Engagement: skipping toxic comment %s (%s=%.2f)",
            comment_id,
            result.max_dimension,
            result.max_score,
        )
        if bl and sp_item_id:
            bl.update_engagement_status(sp_item_id, "skipped")
        _mark_replied(f"skip:{comment_id}", platform)
        return

    # 4. Rate limit — bounce the message into the future via dramatiq.Retry
    # so it lands when a token is actually expected to be available.
    # Previous behaviour raised RuntimeError, which let dramatiq's
    # default exponential backoff (60s → 120s → 240s, max 3 retries)
    # chase a token bucket refilling at ~20-min intervals. Every
    # rate-limited message produced 4 error logs and rarely succeeded.
    # 2026-05-21 forensics: 888 failures / 0 successes in 2h.
    if not _rate_limiter.acquire(platform):
        if bl and sp_item_id:
            bl.update_engagement_status(sp_item_id, "rate_limited")
        backoff_ms = RATE_REFILL_MS.get(platform, 600_000)
        logger.info(
            "Engagement: rate limit reached for %s, retry in %.0fs (%s)",
            platform,
            backoff_ms / 1000,
            comment_id,
        )
        raise dramatiq.Retry(  # type: ignore[name-defined]
            message=f"rate_limited:{platform}",
            delay=backoff_ms,
        )

    # 5. Generate reply
    # Cached per niche_id at module level — see ``_get_persona_engine`` for the
    # perf rationale (was per-reply persona YAML reload + system-prompt rebuild).
    engine = _get_persona_engine(niche_id)
    persona = engine.persona  # downstream uses persona.reply_constraints below
    reply = engine.generate_reply(
        comment=comment_text,
        platform=platform,
        post_context=post_context,
    )
    if reply is None:
        logger.error("Engagement: failed to generate safe reply for %s", comment_id)
        if bl and sp_item_id:
            bl.update_engagement_status(sp_item_id, "failed", error_msg="Reply generation failed")
        return

    # 5b. Confidence heuristic (PersonaEngine doesn't return a score)
    is_question = bool(re.search(r"\?", comment_text))
    inbound_tox = result.max_score  # from step 3 inbound toxicity check
    confidence = 0.9  # base high confidence
    if len(comment_text) > 200:
        confidence -= 0.2  # long comments need more care
    if is_question:
        confidence -= 0.15  # questions need careful answers
    if inbound_tox > 0.1:
        confidence -= 0.2  # borderline inbound toxicity → be more cautious
    confidence = max(0.0, min(1.0, confidence))

    # 5c. Route on the OUTBOUND reply's toxicity (R-75). Previously the routing
    # used the INBOUND comment's toxicity, so the auto/review/discard decision
    # was unrelated to the reply we're about to post — a clean comment could
    # auto-post a toxic generated reply (and a spicy-but-clean reply to a
    # borderline comment could be needlessly discarded). Score the reply itself.
    outbound_tox = _toxicity_gate.check_outbound(reply).max_score

    # 2026-06-22 Lever — LLM-as-judge on reply quality (opt-in via
    # GENLAB_REPLY_CRITIC_ENABLED=1). Toxicity catches HARMFUL replies;
    # this judge catches OFF-TOPIC + WRONG-VOICE + GENERIC replies that
    # are clean but bad for the channel. When the judge scores below
    # 0.85, we downgrade `confidence` so the existing
    # `classify_reply_action` routes the reply to review queue instead
    # of auto-posting. Default off for safe shadow rollout per the
    # established opt-in pattern (Levers C, K).
    judge_reason = ""
    if os.environ.get("GENLAB_REPLY_CRITIC_ENABLED", "0") == "1":
        try:
            judge_score, judge_reason = engine.validate_reply(
                comment=comment_text,
                reply=reply,
                platform=platform,
                post_context=post_context,
            )
            # Multiplicative downgrade: if judge says 0.5, halve
            # confidence. Threshold 0.85 in classify_reply_action means
            # judge < 0.85 reliably forces routing to review.
            confidence = min(confidence, judge_score)
            logger.info(
                "Engagement: reply-critic judged %s → score=%.2f reason=%r "
                "(downgraded confidence to %.2f)",
                comment_id,
                judge_score,
                judge_reason[:60],
                confidence,
            )
        except Exception as exc:  # noqa: BLE001 — never block on critic
            # Critic failure must NOT halt the engagement pipeline.
            # Log and continue with the heuristic confidence.
            logger.warning(
                "Engagement: reply-critic raised for %s (using heuristic confidence): %s",
                comment_id,
                exc,
            )

    action = classify_reply_action(reply_text=reply, confidence=confidence, toxicity=outbound_tox)
    logger.info(
        "Engagement: classify %s → %s (confidence=%.2f, inbound_tox=%.2f, "
        "outbound_tox=%.2f, len=%d, question=%s)",
        comment_id,
        action,
        confidence,
        inbound_tox,
        outbound_tox,
        len(comment_text),
        is_question,
    )

    if action == "discard":
        logger.info(
            "Engagement: discarding reply for %s (low confidence or borderline toxicity)",
            comment_id,
        )
        if bl and sp_item_id:
            bl.update_engagement_status(sp_item_id, "skipped")
        return

    if action == "review":
        _queue_for_review(
            comment_id=comment_id,
            platform=platform,
            niche_id=niche_id,
            comment_text=comment_text,
            reply_text=_append_bot_disclosure(reply, persona.reply_constraints.max_length_chars),
            author=event.get("author_name", "unknown"),
            confidence=confidence,
            tox_score=outbound_tox,
        )
        logger.info("Engagement: queued reply for %s to human review", comment_id)
        if bl and sp_item_id:
            bl.update_engagement_status(sp_item_id, "pending_review", reply_text=reply)
        # Mark the comment as handled so the next poller cycle doesn't
        # re-queue it and force another LLM reply generation. Without
        # this, every comment routed to "review" gets a fresh persona
        # invocation each poll cycle (~10 min for YT, ~5 min for X),
        # burning Anthropic credits + creating duplicate review rows.
        # 2026-05-21 forensics: same 2 comment IDs queued every 7 min
        # for hours.
        _mark_replied(f"review:{comment_id}", platform)
        return

    # action == "auto" — post immediately
    # 5d. Append bot disclosure suffix for transparency (trim reply to fit, R-78)
    reply = _append_bot_disclosure(reply, persona.reply_constraints.max_length_chars)

    # 6. Human-like timing delay
    delay = human_delay()
    logger.debug("Engagement: sleeping %.1fs before posting reply", delay)
    time.sleep(delay)

    # 7. Post reply
    posted = _post_reply(
        platform=platform, post_id=post_id, comment_id=comment_id, reply_text=reply
    )

    # 8. Mark as replied — only if the reply was actually posted
    if posted:
        _mark_replied(comment_id, platform)
        if bl and sp_item_id:
            bl.update_engagement_status(sp_item_id, "replied", reply_text=reply)
    else:
        logger.warning(
            "Engagement: reply to %s on %s failed — NOT marking as replied", comment_id, platform
        )
        if bl and sp_item_id:
            bl.update_engagement_status(sp_item_id, "failed", error_msg="Platform API call failed")


def process_like_event(event: dict) -> None:
    """Like a positive comment. No LLM needed, but still rate-limited and idempotent."""
    comment_id = event["comment_id"]
    platform = event["platform"]
    post_id = event.get("post_id")

    like_key = f"like:{comment_id}"
    if _has_replied(like_key, platform):
        logger.info("Engagement: already liked %s on %s, skipping", comment_id, platform)
        return

    if not _rate_limiter.acquire(platform):
        backoff_ms = RATE_REFILL_MS.get(platform, 600_000)
        logger.info(
            "Engagement: rate limit reached for %s, retry like %s in %.0fs",
            platform,
            comment_id,
            backoff_ms / 1000,
        )
        raise dramatiq.Retry(  # type: ignore[name-defined]
            message=f"rate_limited:{platform}",
            delay=backoff_ms,
        )

    delay = human_delay()
    time.sleep(delay)

    try:
        _post_like(platform=platform, comment_id=comment_id, post_id=post_id)
        _mark_replied(like_key, platform)
        logger.info("Engagement: liked comment %s on %s", comment_id, platform)
    except Exception as e:
        logger.error("Engagement: failed to like %s on %s: %s", comment_id, platform, e)
        raise


def _post_reply(platform: str, post_id: str, comment_id: str, reply_text: str) -> bool:
    """Route to the correct platform client for posting the reply.

    Returns True if the reply was actually posted, False otherwise.
    Uses the unified platform registry (genlab_core.platforms).
    """
    from genlab_core.platforms import get_client
    from genlab_core.platforms.protocols import Engageable

    try:
        client = get_client(platform)
    except (ValueError, ImportError) as exc:
        if platform == "tiktok":
            logger.info(
                "Engagement: TikTok engagement not available via API — "
                "manual reply required for comment %s",
                comment_id,
            )
            return False
        logger.error(
            "Engagement: could not load client for '%s': %s",
            platform,
            exc,
        )
        return False

    if isinstance(client, Engageable):
        return client.post_reply(parent_id=comment_id, text=reply_text, context_id=post_id)

    logger.error(
        "Engagement: client for '%s' does not implement Engageable — cannot reply",
        platform,
    )
    return False


def _post_like(platform: str, comment_id: str, post_id: str | None = None) -> None:
    """Route to the correct platform client for liking a comment."""
    from genlab_core.platforms import get_client
    from genlab_core.platforms.protocols import Engageable

    try:
        client = get_client(platform)
    except (ValueError, ImportError) as exc:
        logger.warning(
            "Engagement: could not load client for '%s' to like comment %s: %s",
            platform,
            comment_id,
            exc,
        )
        return

    if isinstance(client, Engageable):
        client.like(target_id=comment_id, context_id=post_id or "")
    else:
        logger.warning(
            "Engagement: client for '%s' does not implement Engageable — cannot like comment %s",
            platform,
            comment_id,
        )
