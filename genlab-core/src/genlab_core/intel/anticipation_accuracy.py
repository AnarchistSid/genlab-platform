"""Anticipation-score accuracy measurement (Session 3b, 2026-07-01).

Answers the fundamental validity question for
:mod:`genlab_core.intel.trend_anticipation`: **is the anticipation
score actually predicting future peaks?**

Session 1-3 built the module + collected artifacts. Session 3b
compares:

  * ``composite_score`` at write time (T=0)
  * observed pytrends peak intensity in the window ``[T, T+7d]``

for each historical artifact, and returns the Spearman rank
correlation between them per niche. If ``r ≥ 0.4`` sustained across
multiple weeks, the anticipation score is a real signal — the
operator can safely flip ``GENLAB_TREND_ANTICIPATION_ENABLED=true``
to activate pipeline steering. If ``r ≈ 0`` (or negative), the
signal is noise and the composite weights need re-tuning before
the pipeline should act on it.

Why Spearman (not Pearson) — the anticipation score's ABSOLUTE
value is calibrated by construction (log10 mappings, sigmoid,
etc.); what we care about is whether the RANKING it produces
matches the ranking of actual peaks. Spearman is scale-invariant
and monotone-transformation-invariant, so it measures exactly that.

Rollout flag
============

``GENLAB_ANTICIPATION_ACCURACY_ENABLED`` gates the pytrends re-
fetch loop. Without the flag, the accuracy runner short-circuits
and writes an empty artifact — same "shipping-but-inactive"
pattern the paid signals use in Sessions 2-3.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger(__name__)

# ── Feature flag ────────────────────────────────────────────────────

_ENABLE_ENV_VAR: Final[str] = "GENLAB_ANTICIPATION_ACCURACY_ENABLED"


def _integration_enabled() -> bool:
    """Exact-match feature flag. Same fail-closed pattern as the rest
    of the intelligence package. The accuracy runner writes an
    empty artifact when off — the dashboard can still show the
    "measurement disabled" state without special-casing."""
    return os.environ.get(_ENABLE_ENV_VAR, "") in ("true", "TRUE", "True")


# Minimum sample size for a meaningful Spearman. Below this, we
# still emit the coefficient but flag it as "underpowered" in the
# artifact so the dashboard can show "N < 5, r not yet meaningful."
_MIN_TOPICS_FOR_MEANINGFUL_R: Final[int] = 5


# ── Data classes ───────────────────────────────────────────────────


@dataclass(frozen=True)
class AccuracyMeasurement:
    """One niche's anticipation-accuracy result at one point in time.

    * ``spearman_r`` — [-1, 1] rank correlation. ``+1`` means the
      anticipation ranking exactly matches the observed peak
      ranking; ``0`` means no relationship; ``-1`` means inverse
      (a red-flag "your composite is doing the OPPOSITE of what
      you think").
    * ``p_value`` — Standard Spearman p-value from scipy. When
      ``p_value < 0.05`` and ``spearman_r ≥ 0.4``, treat the signal
      as real enough to consider pipeline-steering activation.
    * ``n_topics`` — how many (predicted, observed) pairs went into
      the correlation. Below :data:`_MIN_TOPICS_FOR_MEANINGFUL_R`
      the coefficient is emitted but flagged as underpowered.
    * ``artifact_date`` — the write-time date of the historical
      anticipation artifact this measurement corresponds to.
    * ``measured_at`` — when this measurement itself ran (the peak
      re-fetch time).
    """

    niche_id: str
    spearman_r: float | None
    p_value: float | None
    n_topics: int
    artifact_date: str
    measured_at: str
    reasons: list[str] = field(default_factory=list)
    # Phase 3.B (2026-08-14): secondary signal when Spearman is
    # undefined. Fraction of topics with observed peak > 0 — coarse
    # "did anticipation identify ANY topic that trended?" hit rate.
    # None when zero usable topics.
    observed_hit_rate: float | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def is_underpowered(self) -> bool:
        return self.n_topics < _MIN_TOPICS_FOR_MEANINGFUL_R


# ── Peak-fetch adapters ────────────────────────────────────────────


def _fetch_observed_peak(
    topic: str,
    window_start: datetime,
    window_end: datetime,
) -> float | None:
    """Return the maximum interest value observed for the topic in
    the window. Uses pytrends ``interest_over_time`` with an
    explicit timeframe covering ``[window_start, window_end]``.

    Fail-open: any error (missing pytrends, rate limit, network,
    empty series) returns None. The overall measurement drops
    that topic from the correlation.
    """
    try:
        from pytrends.request import TrendReq
    except ImportError:
        # 2026-07-13 audit follow-up: pytrends was previously an
        # UNDECLARED dep. Silent ImportError meant this fn returned
        # None for every topic; correlation validation never became
        # "ready" so ``GENLAB_TREND_ANTICIPATION_ENABLED`` stayed
        # dormant indefinitely. pytrends now declared in
        # genlab-core/pyproject.toml. If this ImportError still fires,
        # it means the venv sync didn't pick it up — WARN-level so
        # operator sees the misconfiguration in the journal.
        logger.warning(
            "[anticipation_accuracy] pytrends not installed — "
            "add to genlab-core dependencies + uv sync. All peak "
            "fetches will return None until fixed."
        )
        return None

    try:
        # pytrends timeframe format: "YYYY-MM-DD YYYY-MM-DD"
        tf = f"{window_start.strftime('%Y-%m-%d')} {window_end.strftime('%Y-%m-%d')}"
        pt = TrendReq(hl="en-US", tz=330)
        pt.build_payload([topic], timeframe=tf, geo="US")
        df = pt.interest_over_time()
    except Exception as exc:
        # 2026-07-13 audit follow-up: was DEBUG, so 429s + timeouts
        # were invisible in prod journal. Bumped to INFO so the
        # operator can see the fetch-failure rate + decide whether
        # pytrends is being rate-limited too aggressively to be
        # useful (adjust polling cadence + retry backoff if so).
        logger.info("[anticipation_accuracy] peak fetch failed for %r: %s", topic, exc)
        return None

    if df is None or df.empty or topic not in df.columns:
        return None
    values = [float(v) for v in df[topic].tolist() if v is not None]
    if not values:
        return None
    return max(values)


# ── Correlation ───────────────────────────────────────────────────


def _diagnose_constant_input(
    predicted: list[float], observed: list[float],
) -> str | None:
    """Phase 3.B (2026-08-14): distinguish which side of the input
    lacks variance. Prior 'correlation undefined (constant input or
    scipy missing)' reason string hid whether the anticipation
    ranking was degenerate (predicted collapsed) or pytrends had no
    signal for the topics (observed all-zero). Returns:

      * ``'predicted_all_same'``           — ranking algo emitting
        identical scores (algo bug or all topics tied at ceiling)
      * ``'observed_all_zero'``            — pytrends returned 0 for
        every topic (topics too obscure OR pytrends throttled)
      * ``'observed_all_same_nonzero'``    — pytrends returned same
        nonzero value for all topics (rare but possible with
        aggressive normalization windows)
      * ``None``                           — both sides have variance
        so a genuine correlation is computable
    """
    if len(set(predicted)) < 2:
        return "predicted_all_same"
    if len(set(observed)) < 2:
        if all(v == 0 for v in observed):
            return "observed_all_zero"
        return "observed_all_same_nonzero"
    return None


def spearman_rank_correlation(
    predicted: list[float],
    observed: list[float],
) -> tuple[float | None, float | None]:
    """Return ``(spearman_r, p_value)`` or ``(None, None)`` when the
    input is too small or degenerate.

    Uses scipy.stats.spearmanr when available; degrades to a pure-
    numpy rank correlation with p=None otherwise. This lets the
    module work in environments where scipy isn't installed while
    still emitting the r value for dashboard consumption.

    Constant-input handling: when either the predicted or observed
    series has zero variance (all values equal), spearmanr issues
    a ConstantInputWarning and returns NaN. We return
    ``(None, None)`` in that case — the correlation is genuinely
    undefined, not zero. Callers should use
    :func:`_diagnose_constant_input` to distinguish WHICH side is
    degenerate so the accuracy card renders a specific reason.
    """
    if len(predicted) < 2 or len(predicted) != len(observed):
        return None, None

    try:
        from scipy import stats

        # Suppress ConstantInputWarning by pre-checking.
        if len(set(predicted)) < 2 or len(set(observed)) < 2:
            return None, None
        r, p = stats.spearmanr(predicted, observed)
        if r != r:  # NaN check
            return None, None
        return float(r), float(p)
    except ImportError:
        pass

    # scipy unavailable — fall back to numpy on ranks.
    try:
        import numpy as np

        if len(set(predicted)) < 2 or len(set(observed)) < 2:
            return None, None
        # Compute ranks manually.
        pred_ranks = np.argsort(np.argsort(predicted)).astype(float)
        obs_ranks = np.argsort(np.argsort(observed)).astype(float)
        r_matrix = np.corrcoef(pred_ranks, obs_ranks)
        r = float(r_matrix[0, 1])
        if r != r:
            return None, None
        return r, None
    except Exception as exc:
        # WARNING (not DEBUG): silent failure here masks numpy/scipy
        # regressions as "insufficient data" → `ready` badge never
        # fires → GENLAB_TREND_ANTICIPATION_ENABLED stays off forever.
        # Same class-of-bug as the pytrends silent-ImportError episode.
        logger.warning(
            "[anticipation_accuracy] correlation failed (n_predicted=%d, n_observed=%d): %s",
            len(predicted) if predicted is not None else -1,
            len(observed) if observed is not None else -1,
            exc,
        )
        return None, None


# ── Measurement runner ────────────────────────────────────────────


def _artifact_path(niche_id: str, artifact_date: datetime) -> Path:
    """Resolve the anticipation-artifact path (mirrors
    :func:`~genlab_core.intel.trend_anticipation._resolve_output_dir`)."""
    tmp = os.environ.get("GENLAB_TMP")
    root = Path(tmp) if tmp else Path.cwd() / ".tmp"
    stamp = artifact_date.strftime("%Y%m%d")
    return root / "trend-anticipation" / f"{stamp}-{niche_id}.json"


def measure_niche_accuracy(
    niche_id: str,
    *,
    lookback_days: int = 7,
    now: datetime | None = None,
) -> AccuracyMeasurement:
    """Measure anticipation accuracy for one niche.

    Args:
        niche_id: The niche to measure.
        lookback_days: How many days back to load the artifact from.
            7 is the default — that's enough time for the peak to
            have materialised (anticipation predicts 6-24h ahead)
            plus buffer for the peak to complete before we measure.
        now: Injectable "current time" for tests.

    Returns:
        :class:`AccuracyMeasurement`. When the flag is off, the
        artifact is missing, or peak-fetch fails for too many
        topics, the fields are populated defensively with the
        failure mode in ``reasons``.
    """
    now = now or datetime.now(UTC)
    artifact_dt = now - timedelta(days=lookback_days)
    measured_at = now.isoformat()
    artifact_date = artifact_dt.strftime("%Y%m%d")

    if not _integration_enabled():
        return AccuracyMeasurement(
            niche_id=niche_id,
            spearman_r=None,
            p_value=None,
            n_topics=0,
            artifact_date=artifact_date,
            measured_at=measured_at,
            reasons=[f"{_ENABLE_ENV_VAR} not set to 'true'"],
        )

    path = _artifact_path(niche_id, artifact_dt)
    if not path.exists():
        return AccuracyMeasurement(
            niche_id=niche_id,
            spearman_r=None,
            p_value=None,
            n_topics=0,
            artifact_date=artifact_date,
            measured_at=measured_at,
            reasons=[f"no artifact at {path.name}"],
        )

    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        return AccuracyMeasurement(
            niche_id=niche_id,
            spearman_r=None,
            p_value=None,
            n_topics=0,
            artifact_date=artifact_date,
            measured_at=measured_at,
            reasons=[f"artifact parse failed: {exc}"],
        )

    ranking = payload.get("ranking") if isinstance(payload, dict) else None
    if not isinstance(ranking, list) or not ranking:
        return AccuracyMeasurement(
            niche_id=niche_id,
            spearman_r=None,
            p_value=None,
            n_topics=0,
            artifact_date=artifact_date,
            measured_at=measured_at,
            reasons=["artifact has no ranking rows"],
        )

    predicted: list[float] = []
    observed: list[float] = []
    n_fetch_failed = 0
    for row in ranking:
        if not isinstance(row, dict):
            continue
        topic = row.get("topic")
        composite = row.get("composite_score")
        if not topic or composite is None:
            continue
        peak = _fetch_observed_peak(topic, artifact_dt, now)
        if peak is None:
            n_fetch_failed += 1
            continue
        try:
            predicted.append(float(composite))
            observed.append(float(peak))
        except (TypeError, ValueError):
            continue

    n_topics = len(predicted)
    if n_topics < 2:
        # Phase 3.B: still emit hit_rate when we have at least 1 topic
        # so the card can render "1/1 trended" even if correlation
        # isn't computable.
        thin_hit_rate = (
            sum(1 for v in observed if v > 0) / len(observed)
            if observed else None
        )
        return AccuracyMeasurement(
            niche_id=niche_id,
            spearman_r=None,
            p_value=None,
            n_topics=n_topics,
            artifact_date=artifact_date,
            measured_at=measured_at,
            reasons=[f"only {n_topics} usable topics ({n_fetch_failed} peak-fetch failures)"],
            observed_hit_rate=thin_hit_rate,
        )

    r, p = spearman_rank_correlation(predicted, observed)
    # Phase 3.B: coarse secondary signal — "did anticipation identify
    # ANY topic that trended?" hit rate. Works even when Spearman is
    # undefined (e.g., all observed values are 0 or identical).
    observed_hit_rate = (
        sum(1 for v in observed if v > 0) / len(observed)
        if observed else None
    )
    reasons = []
    if r is None:
        # Phase 3.B: distinguish WHICH side collapsed so the operator
        # gets a targeted signal on the accuracy card.
        diag = _diagnose_constant_input(predicted, observed)
        if diag == "predicted_all_same":
            reasons.append(
                "predicted_all_same — anticipation ranking degenerate "
                "(all composite scores identical)"
            )
        elif diag == "observed_all_zero":
            reasons.append(
                "observed_all_zero — pytrends returned 0 for every topic "
                "(topics too obscure OR pytrends throttled)"
            )
        elif diag == "observed_all_same_nonzero":
            reasons.append(
                "observed_all_same_nonzero — pytrends returned identical "
                "nonzero value for every topic (rare normalization case)"
            )
        else:
            reasons.append("correlation undefined (scipy/numpy unavailable)")
    elif n_topics < _MIN_TOPICS_FOR_MEANINGFUL_R:
        reasons.append(f"underpowered — n_topics={n_topics} < {_MIN_TOPICS_FOR_MEANINGFUL_R}")
    if n_fetch_failed:
        reasons.append(f"{n_fetch_failed} topics dropped due to peak-fetch failure")

    return AccuracyMeasurement(
        niche_id=niche_id,
        spearman_r=r,
        p_value=p,
        n_topics=n_topics,
        artifact_date=artifact_date,
        measured_at=measured_at,
        reasons=reasons,
        observed_hit_rate=observed_hit_rate,
    )


# ── Persistence ────────────────────────────────────────────────────


def _accuracy_output_dir() -> Path:
    """``$GENLAB_TMP/anticipation-accuracy`` or fallback."""
    tmp = os.environ.get("GENLAB_TMP")
    root = Path(tmp) if tmp else Path.cwd() / ".tmp"
    return root / "anticipation-accuracy"


def persist_measurement(
    measurement: AccuracyMeasurement,
    *,
    output_dir: Path | None = None,
) -> Path:
    """Write one measurement's JSON to
    ``$GENLAB_TMP/anticipation-accuracy/YYYYMMDD-<niche>.json``.
    Overwrite-per-day per niche = idempotent runner."""
    out_dir = output_dir or _accuracy_output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    path = out_dir / f"{stamp}-{measurement.niche_id}.json"
    path.write_text(json.dumps(measurement.to_json(), indent=2, default=str))
    return path


def read_latest_measurement(
    niche_id: str,
    *,
    output_dir: Path | None = None,
) -> AccuracyMeasurement | None:
    """Return today's persisted measurement for a niche, or None."""
    out_dir = output_dir or _accuracy_output_dir()
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    path = out_dir / f"{stamp}-{niche_id}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    try:
        return AccuracyMeasurement(
            niche_id=str(payload.get("niche_id", niche_id)),
            spearman_r=payload.get("spearman_r"),
            p_value=payload.get("p_value"),
            n_topics=int(payload.get("n_topics", 0)),
            artifact_date=str(payload.get("artifact_date", "")),
            measured_at=str(payload.get("measured_at", "")),
            reasons=list(payload.get("reasons") or []),
        )
    except (TypeError, ValueError):
        return None


__all__ = [
    "AccuracyMeasurement",
    "measure_niche_accuracy",
    "spearman_rank_correlation",
    "persist_measurement",
    "read_latest_measurement",
    "_MIN_TOPICS_FOR_MEANINGFUL_R",
]
