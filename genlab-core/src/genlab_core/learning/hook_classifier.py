"""XGBoost-based hook quality classifier.

Predicts the probability that a hook will perform above the 75th
percentile of engagement (reward_48h). Gracefully degrades when:
  - xgboost is not installed (returns 0.5)
  - Model file does not exist (returns 0.5)
  - Training data < MIN_EXAMPLES (skips training)

Model is persisted as JSON at:
    genlab-core/models/hook_classifier_{niche_id}.json
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Any

from genlab_core.learning.hook_features import build_feature_vector
from genlab_core.learning.hook_training_data import MIN_EXAMPLES

logger = logging.getLogger(__name__)

# 2026-08-11 Session 1: LLM-based scorer as an alternative to the
# XGBoost classifier. Motivated by strategist diagnostic showing
# XGBoost Spearman=0.0 (near-random) — the 8 lexical features
# (word_count, has_question, etc.) can't distinguish curiosity-gap
# hooks from generic ones. LLM scoring uses semantic judgment via
# Claude Haiku (~$0.0001 per hook). Flag-gated to preserve backward
# compat; operator flips after verifying signal quality on staging.
_LLM_ENABLED_ENV_VAR = "GENLAB_HOOK_CLASSIFIER_LLM_ENABLED"
_HAIKU_MODEL = "claude-haiku-4-5-20251001"

# In-process cache — same hook scored multiple times within a run
# (across gates, ensemble votes, calibration writes) returns from
# cache. Keyed by (niche_id, sha256(hook_text)). Small memory
# footprint at 5 niches × ~50 unique hooks/day.
_LLM_SCORE_CACHE: dict[tuple[str, str], float] = {}

_LLM_HOOK_SCORER_PROMPT = """\
Rate this {niche} short-form video hook on a 0.0 to 1.0 scale.

Hook: "{hook_text}"

Consider:
* Curiosity gap: does it create a compelling reason to watch?
* Specificity: concrete details vs generic templates?
* Platform fit: appropriate for TikTok / Reels / YouTube Shorts?
* Truthfulness: honest without over-promising?

Score guidelines:
* 0.9-1.0: viral-worthy — strong curiosity, specific, honest promise
* 0.7-0.8: solid — would perform above niche average
* 0.5-0.6: average — nothing wrong but nothing standout
* 0.3-0.4: weak — generic template energy, low curiosity
* 0.0-0.2: broken — placeholder text, LLM refusal, bare title, or
  hook contradicting itself

Respond with ONLY a decimal number 0.0-1.0. Nothing else.
"""

# Negative lookaround with BOTH digits AND `.` in the exclusion
# class — otherwise "2.0" would match the trailing "0" (preceded by
# ".", not a digit) and score 0.0. Accepts standalone 0, 1, 0.x,
# 1.x, .x — nothing that's part of a larger number.
_DECIMAL_RE = re.compile(r"(?<![\d.])([01](?:\.\d+)?|\.\d+)(?![\d.])")


def _is_llm_enabled() -> bool:
    """Env-flag gate for the LLM-based scorer. Default OFF so shipping
    this code causes zero behavior change until operator opts in."""
    return os.environ.get(_LLM_ENABLED_ENV_VAR, "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

# Resolve model directory relative to genlab-core package root
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_MODELS_DIR = _PACKAGE_ROOT / "models"

# Feature order must be consistent between training and inference
FEATURE_NAMES = [
    "word_count",
    "has_question",
    "has_number",
    "emoji_count",
    "has_superlative",
    "starts_with_you",
    "avg_word_length",
    "unique_word_ratio",
]

# Check xgboost availability
try:
    import xgboost as xgb

    _HAS_XGBOOST = True
except ImportError:
    xgb = None  # type: ignore[assignment]
    _HAS_XGBOOST = False

try:
    import numpy as np

    _HAS_NUMPY = True
except ImportError:
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False


def _cache_key(niche_id: str, hook_text: str) -> tuple[str, str]:
    digest = hashlib.sha256(hook_text.strip().encode("utf-8")).hexdigest()
    return (niche_id, digest)


def _parse_llm_score(raw: str) -> float | None:
    """Extract a 0-1 decimal from the LLM response. Returns None on
    unparseable output — caller falls back to the XGBoost path."""
    if not raw:
        return None
    match = _DECIMAL_RE.search(raw.strip())
    if not match:
        return None
    try:
        value = float(match.group(1))
    except (TypeError, ValueError):
        return None
    if value < 0.0 or value > 1.0:
        return None
    return value


def _llm_score_hook_impl(hook_text: str, niche_id: str) -> float | None:
    """Score a hook via Claude Haiku. Returns 0-1 on success, None on
    any failure (unavailable client, network error, unparseable output).
    Caller falls back to XGBoost on None.

    Uses the same Anthropic → OpenAI fallback pattern as post_rca.py
    so a transient Anthropic outage doesn't blackhole every gate call.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key and not openai_key:
        return None

    try:
        import anthropic  # type: ignore[import-not-found]
    except ImportError:
        logger.debug("[hook_clf_llm] anthropic package not installed")
        return None

    try:
        from genlab_core.llm.cache import with_prompt_cache
    except ImportError:
        def with_prompt_cache(x: str) -> str:  # type: ignore[misc]
            return x

    try:
        from genlab_core.llm.fallback import (
            call_openai_fallback as _call_openai_fallback,
            cb_is_open as _cb_is_open,
            cb_record_exhaustion as _cb_record_exhaustion,
            cb_record_success as _cb_record_success,
            fallback_enabled as _fallback_enabled,
            should_fallback as _should_fallback,
        )
    except ImportError:
        _call_openai_fallback = None  # type: ignore[assignment]
        _cb_is_open = lambda: False  # noqa: E731
        _cb_record_exhaustion = lambda: None  # noqa: E731
        _cb_record_success = lambda: None  # noqa: E731
        _fallback_enabled = lambda: False  # noqa: E731
        _should_fallback = lambda _e: False  # noqa: E731

    system_prompt = _LLM_HOOK_SCORER_PROMPT.format(
        niche=niche_id.replace("_", " "),
        hook_text=hook_text.strip().replace('"', "'"),
    )
    raw = ""

    if _fallback_enabled() and _cb_is_open() and openai_key and _call_openai_fallback:
        raw = _call_openai_fallback(system_prompt, "", 8, 0.0, openai_key)
    else:
        try:
            client = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model=_HAIKU_MODEL,
                max_tokens=8,
                temperature=0.0,
                system=with_prompt_cache(system_prompt),
                messages=[{"role": "user", "content": "Score:"}],
            )
        except Exception as anthropic_exc:
            if (
                _fallback_enabled()
                and _should_fallback(anthropic_exc)
                and openai_key
                and _call_openai_fallback
            ):
                _cb_record_exhaustion()
                try:
                    from genlab_core.llm.errors import classify_llm_error

                    logger.warning(
                        "[hook_clf_llm] Anthropic failed (reason=%s) -> OpenAI fallback",
                        classify_llm_error(anthropic_exc),
                    )
                except Exception:
                    logger.warning(
                        "[hook_clf_llm] Anthropic failed: %s -> OpenAI fallback",
                        anthropic_exc,
                    )
                raw = _call_openai_fallback(system_prompt, "", 8, 0.0, openai_key)
            else:
                logger.warning(
                    "[hook_clf_llm] Anthropic call failed no-fallback: %s",
                    anthropic_exc,
                )
                return None
        else:
            _cb_record_success()
            try:
                from genlab_core.intelligence.cost_accumulator import (
                    record_anthropic_usage,
                )

                record_anthropic_usage(_HAIKU_MODEL, response)
            except Exception:
                pass
            raw = response.content[0].text.strip() if response.content else ""

    return _parse_llm_score(raw)


class HookClassifier:
    """Pre-publish hook quality predictor.

    Wraps an XGBoost binary classifier that predicts the probability
    of a hook achieving above-median engagement. Falls back to neutral
    scores (0.5) when xgboost is unavailable or no model is loaded.

    Args:
        niche_id: Niche identifier (e.g. "ai_creators", "gaming").
            Determines which model file to load.
        models_dir: Override for model directory. Defaults to
            genlab-core/models/.
    """

    def __init__(
        self,
        niche_id: str = "ai_creators",
        models_dir: Path | None = None,
    ) -> None:
        self.niche_id = niche_id
        self._models_dir = models_dir or _MODELS_DIR
        self._model: Any = None
        self._loaded = False

        # Attempt to load existing model
        self._try_load()

    @property
    def model_path(self) -> Path:
        return self._models_dir / f"hook_classifier_{self.niche_id}.json"

    def _try_load(self) -> None:
        """Attempt to load a persisted model from disk."""
        if not _HAS_XGBOOST:
            logger.debug("[hook_clf] xgboost not installed — using neutral fallback")
            return

        path = self.model_path
        if not path.exists():
            logger.debug("[hook_clf] No model at %s — using neutral fallback", path)
            return

        try:
            model = xgb.XGBClassifier()
            model.load_model(str(path))
            self._model = model
            self._loaded = True
            logger.info("[hook_clf] Loaded model from %s", path)
        except Exception as exc:
            logger.warning("[hook_clf] Failed to load model from %s: %s", path, exc)

    def predict_proba(self, features: dict[str, float]) -> float:
        """Predict probability of high engagement for a feature vector.

        Args:
            features: Dict of feature_name -> float from build_feature_vector().

        Returns:
            Probability in [0, 1]. Returns 0.5 (neutral) when no model
            is available.
        """
        if not self._loaded or self._model is None:
            return 0.5

        if not _HAS_NUMPY:
            return 0.5

        try:
            # Build ordered feature array
            x = np.array(
                [[features.get(f, 0.0) for f in FEATURE_NAMES]],
                dtype=np.float32,
            )
            proba = self._model.predict_proba(x)
            # proba shape: (1, 2) — [P(class=0), P(class=1)]
            return float(proba[0][1])
        except Exception as exc:
            logger.warning("[hook_clf] predict_proba failed: %s", exc)
            return 0.5

    def score_hook(self, hook_text: str) -> float:
        """Score a single hook text.

        When `GENLAB_HOOK_CLASSIFIER_LLM_ENABLED=1`, routes through the
        Claude Haiku LLM scorer (semantic judgment on curiosity gap +
        specificity + platform fit). Falls back to the XGBoost lexical
        classifier if the LLM path returns None (unparseable, network
        error, missing key).

        The flag is OFF by default so shipping this code causes zero
        behavior change; operator flips per-niche after verifying
        signal quality on staging.

        Args:
            hook_text: The hook text to score.

        Returns:
            Probability in [0, 1]. Returns 0.5 on any error.
        """
        if not hook_text or not hook_text.strip():
            return 0.5

        # LLM path (flag-gated). In-process cache keyed by
        # (niche_id, sha256(hook)) — the same hook is scored many
        # times per pipeline (gate, ensemble, calibration).
        if _is_llm_enabled():
            key = _cache_key(self.niche_id, hook_text)
            cached = _LLM_SCORE_CACHE.get(key)
            if cached is not None:
                return cached
            try:
                llm_score = _llm_score_hook_impl(hook_text, self.niche_id)
            except Exception as exc:
                logger.warning(
                    "[hook_clf] LLM path raised (%s) — falling back to XGBoost",
                    exc,
                )
                llm_score = None
            if llm_score is not None:
                _LLM_SCORE_CACHE[key] = llm_score
                return llm_score
            # Fall through to XGBoost below on LLM failure.

        try:
            features = build_feature_vector(hook_text)
            if not features:
                return 0.5
            return self.predict_proba(features)
        except Exception as exc:
            logger.warning("[hook_clf] score_hook failed: %s", exc)
            return 0.5


def train_and_save(
    examples: list[Any],
    labels: list[int],
    niche_id: str = "ai_creators",
    models_dir: Path | None = None,
) -> bool:
    """Train an XGBoost classifier on hook examples and save to disk.

    Args:
        examples: List of HookExample instances.
        labels: Binary labels (0/1) from compute_engagement_labels().
        niche_id: Niche identifier for the model filename.
        models_dir: Override for model directory.

    Returns:
        True if training succeeded, False otherwise.
    """
    if not _HAS_XGBOOST:
        logger.warning(
            "[hook_clf] xgboost not installed — cannot train. Install with: pip install xgboost"
        )
        return False

    if not _HAS_NUMPY:
        logger.warning("[hook_clf] numpy not installed — cannot train")
        return False

    if len(examples) < MIN_EXAMPLES:
        logger.info(
            "[hook_clf] Only %d examples (need %d) — skipping training",
            len(examples),
            MIN_EXAMPLES,
        )
        return False

    if len(examples) != len(labels):
        logger.error(
            "[hook_clf] examples (%d) and labels (%d) length mismatch",
            len(examples),
            len(labels),
        )
        return False

    try:
        # Build feature matrix
        feature_dicts = [build_feature_vector(ex.hook_text) for ex in examples]
        X = np.array(
            [[fd.get(f, 0.0) for f in FEATURE_NAMES] for fd in feature_dicts],
            dtype=np.float32,
        )
        y = np.array(labels, dtype=np.int32)

        # Train XGBoost
        model = xgb.XGBClassifier(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.1,
            eval_metric="logloss",
            use_label_encoder=False,
        )
        model.fit(X, y)

        # Save model
        save_dir = models_dir or _MODELS_DIR
        save_dir.mkdir(parents=True, exist_ok=True)
        model_path = save_dir / f"hook_classifier_{niche_id}.json"
        model.save_model(str(model_path))

        # Sidecar meta file consumed by the dashboard's
        # /api/v1/learning/hook-classifier-status endpoint. Without it
        # the UI shows every niche as "Not trained" even when the model
        # file is on disk. Track n_examples + pos_rate so operators can
        # see the training distribution at a glance.
        import json
        from datetime import UTC, datetime

        pos_rate = float(np.mean(y)) if len(y) > 0 else 0.0
        meta_path = save_dir / f"hook_classifier_{niche_id}.meta.json"
        meta_path.write_text(
            json.dumps(
                {
                    "niche_id": niche_id,
                    "n_examples": len(examples),
                    "pos_rate": round(pos_rate, 4),
                    "feature_names": FEATURE_NAMES,
                    "trained_at": datetime.now(UTC).isoformat(),
                }
            )
        )

        logger.info(
            "[hook_clf] Trained on %d examples, saved to %s",
            len(examples),
            model_path,
        )
        return True

    except Exception as exc:
        logger.error("[hook_clf] Training failed: %s", exc)
        # 2026-06-14 (PR #200): emit a pipeline_alerts row so the
        # operator sees the failure on Mission Control instead of
        # having to grep journalctl. The 2026-05-21 → 2026-06-14
        # outage stayed silent for 24 days at ERROR log level alone.
        _emit_training_failure_alert(niche_id, str(exc))
        return False


def _emit_training_failure_alert(niche_id: str, error: str) -> None:
    """Best-effort pipeline_alerts insert. Never raises. Mirrors the
    fail-open shape of engagement.token_health.emit_token_expiry_alert."""
    try:
        import json
        import os

        import psycopg  # noqa: F401 — kept for the except below

        from genlab_core.storage.tenant_context import pg_connect

        dsn = os.environ.get("DATABASE_URL", "")
        if not dsn:
            return
        message = (
            f"Hook classifier training failed for {niche_id}: {error[:200]}. "
            "Inspect /opt/genlab/.logs/hook_trainer.log for the full trace; "
            "if Permission denied, run `chown -R genlab:genlab "
            "/opt/genlab/genlab-core/models` (PR #200's ExecStartPre handles "
            "this automatically going forward)."
        )
        # SR-A/C/D Tier-2 migration (2026-06-17).
        with pg_connect(dsn, niche_id=niche_id, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                # Dedup against any open alert for the same niche.
                cur.execute(
                    "SELECT id FROM pipeline_alerts "
                    "WHERE check_name = %s AND niche_id = %s AND resolved_at IS NULL",
                    ("hook_training_failed", niche_id),
                )
                if cur.fetchone():
                    return
                cur.execute(
                    "INSERT INTO pipeline_alerts "
                    "(niche_id, check_name, severity, message, details) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (
                        niche_id,
                        "hook_training_failed",
                        "warning",
                        message,
                        json.dumps({"error": error[:500]}),
                    ),
                )
                conn.commit()
    except Exception:
        # Alert emission must never re-raise into the training caller.
        pass
