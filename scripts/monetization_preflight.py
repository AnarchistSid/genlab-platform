#!/usr/bin/env python3
"""Monetization Layer 3 stack readiness preflight (2026-07-07).

One-command "is monetization ready to fire?" report. Checks every
piece of the L3 stack the operator activated across ~18 PRs and
reports per-niche readiness scores.

## What it checks

Per niche (5 niches):
  1. affiliate_enabled in the catalog YAML
  2. Product arms registered (count from bandit_arms WHERE
     arm_type='product')
  3. Blueprints have product_slug backfilled (count)
  4. Selector divergence flag state (GENLAB_PRODUCT_SELECTOR_ENABLED)

Cross-niche (once):
  5. Alembic head matches expected migration (c0z1a2b3c4d5)
  6. affiliate_clicks + affiliate_conversions +
     selector_divergences tables exist
  7. Systemd timers armed:
       - genlab-register-click-rewards.timer (06:00 UTC)
       - genlab-register-conversion-rewards.timer (06:15 UTC)
       - genlab-import-amazon-conversions.timer (05:45 UTC)
  8. Amazon CSV drop dir exists at $GENLAB_TMP/amazon-reports/
     (informational — not required for the click-only path)

## Output modes

Default: human-readable per-niche table + overall score.

--json: machine-readable JSON blob for pipeline integration:
    {
      "overall_ready": bool,
      "score_pct": 0..100,
      "niches": {niche_id: {readiness}},
      "cross": {check_name: bool},
      "warnings": [...],
      "generated_at": ISO8601
    }

## Exit codes

    0 — all checks passed OR --allow-partial
    1 — critical gap (e.g. migration missing, no arms registered)
    2 — DATABASE_URL unset / DB unreachable
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("monetization_preflight")


KNOWN_NICHES = ("ai_creators", "gaming", "sports", "movies", "anime")

# Expected head migration once L3 PR 12a lands. Update when future
# L3 migrations ship; the preflight is deliberately anchored to
# the head so alembic drift is caught early.
EXPECTED_ALEMBIC_HEAD = "c0z1a2b3c4d5"

# Tables expected to exist after all L3 migrations apply.
REQUIRED_TABLES = (
    "affiliate_clicks",
    "affiliate_conversions",
    "selector_divergences",
    "bandit_arms",
    "blueprints",
)

# Timers we expect the operator to enable after deploy. Missing timers
# are WARN-level (they might be intentionally disabled during
# activation ramp), never CRITICAL.
EXPECTED_TIMERS = (
    "genlab-register-click-rewards.timer",
    "genlab-register-conversion-rewards.timer",
    "genlab-import-amazon-conversions.timer",
)


@dataclass
class NicheReadiness:
    niche_id: str
    affiliate_enabled: bool | None = None  # None = catalog unavailable
    n_arms_registered: int = 0
    n_blueprints_with_slug: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def score(self) -> int:
        """0-100 readiness. 3 dimensions × ~33% each.

        A None ``affiliate_enabled`` scores 0 — the catalog being
        unavailable is a legitimate operator-facing gap, not a "give
        partial credit" state. Better to under-score honestly than
        over-score optimistically."""
        pts = 0
        if self.affiliate_enabled is True:
            pts += 34
        if self.n_arms_registered > 0:
            pts += 33
        if self.n_blueprints_with_slug > 0:
            pts += 33
        return pts

    def to_dict(self) -> dict[str, Any]:
        return {
            "niche_id": self.niche_id,
            "affiliate_enabled": self.affiliate_enabled,
            "n_arms_registered": self.n_arms_registered,
            "n_blueprints_with_slug": self.n_blueprints_with_slug,
            "score_pct": self.score,
            "warnings": self.warnings,
        }


@dataclass
class CrossChecks:
    alembic_head: str | None = None
    alembic_head_matches: bool = False
    tables_present: dict[str, bool] = field(default_factory=dict)
    timers_active: dict[str, bool] = field(default_factory=dict)
    csv_dir_exists: bool = False
    selector_flag_enabled: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def critical_gaps(self) -> list[str]:
        """Cross-cutting things that must be CONFIRMED broken (not just
        unverifiable). ``alembic_head is None`` = 'DB was not queried'
        rather than 'DB says no head' — treat as unverified, not
        critical."""
        gaps = []
        if self.alembic_head is not None and not self.alembic_head_matches:
            gaps.append(
                f"alembic head is {self.alembic_head!r} but expected "
                f"{EXPECTED_ALEMBIC_HEAD!r} — migrations not applied"
            )
        # tables_present is only populated when the DB is reachable.
        # If it's empty, we couldn't check — not a critical gap.
        if self.tables_present:
            for t in REQUIRED_TABLES:
                if not self.tables_present.get(t, False):
                    gaps.append(f"table {t} missing from DB")
        return gaps

    def to_dict(self) -> dict[str, Any]:
        return {
            "alembic_head": self.alembic_head,
            "alembic_head_matches": self.alembic_head_matches,
            "tables_present": self.tables_present,
            "timers_active": self.timers_active,
            "csv_dir_exists": self.csv_dir_exists,
            "selector_flag_enabled": self.selector_flag_enabled,
            "warnings": self.warnings,
        }


# ── Individual check functions ──────────────────────────────────


def _load_catalog() -> dict | None:
    """Load affiliate_catalog.yaml if present. None on failure."""
    catalog_path = (
        Path(__file__).resolve().parents[1] / "genlab-core" / "config" / "affiliate_catalog.yaml"
    )
    if not catalog_path.is_file():
        return None
    try:
        import yaml

        return yaml.safe_load(catalog_path.read_text()) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[preflight] catalog load failed: %s", exc)
        return None


def _check_alembic_head(dsn: str) -> tuple[str | None, bool]:
    """Return (current_head_revision, matches_expected)."""
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version_num FROM alembic_version LIMIT 1")
                row = cur.fetchone()
                head = row[0] if row else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("[preflight] alembic_version query failed: %s", exc)
        return None, False
    return head, head == EXPECTED_ALEMBIC_HEAD


def _check_tables(dsn: str) -> dict[str, bool]:
    """Return {table: exists}. Uses information_schema so no privileges
    beyond SELECT are needed.

    Returns EMPTY dict on failure (not all-False) so the caller can
    distinguish "couldn't check" from "checked and confirmed missing".
    """
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' "
                    "AND table_name = ANY(%s)",
                    (list(REQUIRED_TABLES),),
                )
                found = {name for (name,) in cur.fetchall()}
        return {t: (t in found) for t in REQUIRED_TABLES}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[preflight] tables query failed: %s", exc)
        return {}


def _check_arm_counts(dsn: str) -> dict[str, int]:
    """Return {niche_id: count} for arm_type='product'."""
    out: dict[str, int] = dict.fromkeys(KNOWN_NICHES, 0)
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT niche_id, COUNT(*) FROM bandit_arms "
                    "WHERE arm_type = 'product' "
                    "GROUP BY niche_id"
                )
                for niche, count in cur.fetchall():
                    if niche in out:
                        out[niche] = int(count or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[preflight] arm counts query failed: %s", exc)
    return out


def _check_blueprints_with_slug(dsn: str) -> dict[str, int]:
    """Return {niche_id: count of blueprints with product_slug populated}."""
    out: dict[str, int] = dict.fromkeys(KNOWN_NICHES, 0)
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT niche_id, COUNT(*) FROM blueprints "
                    "WHERE product_slug IS NOT NULL "
                    "  AND product_slug != '' "
                    "GROUP BY niche_id"
                )
                for niche, count in cur.fetchall():
                    if niche in out:
                        out[niche] = int(count or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[preflight] blueprint slug count failed: %s", exc)
    return out


def _check_systemd_timers() -> dict[str, bool]:
    """Return {timer_name: is_active}. Only works if the script runs
    as a user who can call ``systemctl is-active``. Returns all False
    (with a warning) when systemctl isn't available (e.g. running the
    preflight from a laptop, not the prod host)."""
    import subprocess  # noqa: S404 — explicit subprocess call

    out: dict[str, bool] = dict.fromkeys(EXPECTED_TIMERS, False)
    for timer in EXPECTED_TIMERS:
        try:
            result = subprocess.run(  # noqa: S603
                ["systemctl", "is-active", timer],
                capture_output=True,
                text=True,
                timeout=5,
            )
            out[timer] = result.stdout.strip() == "active"
        except FileNotFoundError:
            # systemctl unavailable — running preflight elsewhere.
            # Break early so we don't retry the same failure per timer.
            logger.info("[preflight] systemctl not available — skipping timer checks")
            break
        except Exception as exc:  # noqa: BLE001
            logger.debug("[preflight] %s status check failed: %s", timer, exc)
    return out


def _check_csv_dir() -> bool:
    tmp = os.environ.get("GENLAB_TMP")
    root = Path(tmp) if tmp else Path.cwd() / ".tmp"
    return (root / "amazon-reports").is_dir()


def _check_selector_flag() -> bool:
    # 2026-07-14: env_true unifies with product_selector.py + product_bandit.py.
    from genlab_core.settings import env_true

    return env_true("GENLAB_PRODUCT_SELECTOR_ENABLED")


# ── Composition ─────────────────────────────────────────────────


def collect(dsn: str | None) -> tuple[dict[str, NicheReadiness], CrossChecks]:
    """Run all checks. Returns (per-niche state, cross-cutting checks)."""
    per_niche = {n: NicheReadiness(niche_id=n) for n in KNOWN_NICHES}
    cross = CrossChecks()

    # ── Catalog-side checks ─────────────────────────────────────
    catalog = _load_catalog()
    if catalog is None:
        for niche in per_niche.values():
            niche.warnings.append(
                "catalog YAML not found on disk (gitignored — expected on non-prod)"
            )
    else:
        catalog_niches = catalog.get("niches") or {}
        for niche_id, niche_state in per_niche.items():
            niche_cfg = catalog_niches.get(niche_id) or {}
            # Default True in the source, but explicit None = "we
            # didn't see this niche at all in the catalog" which is
            # different from "we saw it and it's enabled". Only set
            # to True/False when the key was present or absent-but-
            # the-niche-existed in the catalog.
            if niche_id in catalog_niches:
                niche_state.affiliate_enabled = bool(niche_cfg.get("affiliate_enabled", True))
            else:
                niche_state.warnings.append(f"niche {niche_id} absent from catalog YAML")

    # ── DB-side checks ──────────────────────────────────────────
    if dsn:
        cross.alembic_head, cross.alembic_head_matches = _check_alembic_head(dsn)
        cross.tables_present = _check_tables(dsn)
        arm_counts = _check_arm_counts(dsn)
        slug_counts = _check_blueprints_with_slug(dsn)
        for niche_id, niche_state in per_niche.items():
            niche_state.n_arms_registered = arm_counts.get(niche_id, 0)
            niche_state.n_blueprints_with_slug = slug_counts.get(niche_id, 0)
    else:
        cross.warnings.append(
            "DATABASE_URL not set — DB checks skipped (arms + blueprints + tables + alembic)"
        )

    # ── System-side checks ──────────────────────────────────────
    cross.timers_active = _check_systemd_timers()
    cross.csv_dir_exists = _check_csv_dir()
    cross.selector_flag_enabled = _check_selector_flag()

    if not cross.selector_flag_enabled:
        cross.warnings.append(
            "GENLAB_PRODUCT_SELECTOR_ENABLED is off — divergence data won't accumulate; "
            "L3 PR 12b's auto-flip depends on this being flipped for ≥7d first"
        )
    if not cross.csv_dir_exists:
        cross.warnings.append(
            "amazon-reports dir absent — L3 PR 11 will run in click-only fallback "
            "until CSVs arrive (this is expected on day 0)"
        )

    return per_niche, cross


def _pct_bar(pct: int, width: int = 20) -> str:
    """▓▓▓▓▓░░░░░ style bar. Renders as ASCII fallback if terminal
    doesn't support the block glyphs."""
    filled = int(round(pct * width / 100))
    return "▓" * filled + "░" * (width - filled)


def render_human(
    per_niche: dict[str, NicheReadiness],
    cross: CrossChecks,
) -> str:
    """Two-section report: cross-checks + per-niche table."""
    lines: list[str] = []
    lines.append("━" * 72)
    lines.append("Monetization Layer 3 stack — preflight readiness report")
    lines.append(datetime.now(UTC).isoformat())
    lines.append("━" * 72)
    lines.append("")

    lines.append("== Cross-cutting checks ==")
    if cross.alembic_head is None:
        alembic_row = "  [?] alembic head (unable to check)"
    else:
        alembic_symbol = "✓" if cross.alembic_head_matches else "✗"
        alembic_row = (
            f"  [{alembic_symbol}] alembic head {cross.alembic_head} "
            f"(expected {EXPECTED_ALEMBIC_HEAD})"
        )
    lines.append(alembic_row)
    if not cross.tables_present:
        lines.append("  [?] table checks (unable to check)")
    else:
        for table, present in sorted(cross.tables_present.items()):
            lines.append(f"  [{'✓' if present else '✗'}] table {table}")
    for timer, active in cross.timers_active.items():
        lines.append(f"  [{'✓' if active else '·'}] timer {timer}")
    lines.append(f"  [{'✓' if cross.csv_dir_exists else '·'}] amazon-reports dir")
    lines.append(
        f"  [{'✓' if cross.selector_flag_enabled else '·'}] GENLAB_PRODUCT_SELECTOR_ENABLED"
    )
    lines.append("")

    critical = cross.critical_gaps
    if critical:
        lines.append("== CRITICAL gaps ==")
        for gap in critical:
            lines.append(f"  ✗ {gap}")
        lines.append("")

    if cross.warnings:
        lines.append("== Notes ==")
        for w in cross.warnings:
            lines.append(f"  · {w}")
        lines.append("")

    lines.append("== Per-niche readiness ==")
    lines.append(f"  {'niche':<14} {'flag':<7} {'arms':<6} {'bpts':<6} {'score':<6} readiness")
    for niche_id in KNOWN_NICHES:
        n = per_niche[niche_id]
        flag_display = (
            "on"
            if n.affiliate_enabled is True
            else ("off" if n.affiliate_enabled is False else "?")
        )
        lines.append(
            f"  {niche_id:<14} {flag_display:<7} "
            f"{n.n_arms_registered:<6} {n.n_blueprints_with_slug:<6} "
            f"{n.score:<4}% {_pct_bar(n.score)}"
        )
    lines.append("")

    total_score = sum(n.score for n in per_niche.values()) // len(per_niche)
    lines.append(f"Overall readiness: {total_score}%  {_pct_bar(total_score)}")
    if critical:
        lines.append("→ NOT READY: critical gaps must be resolved first")
    elif total_score >= 90:
        lines.append("→ READY: monetization stack fully activated")
    elif total_score >= 60:
        lines.append("→ PARTIAL: activation in progress; some niches ready")
    else:
        lines.append("→ EARLY: significant setup remaining")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of human report.",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Exit 0 even on critical gaps (useful for cron polling).",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable DEBUG logging.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    dsn = os.environ.get("DATABASE_URL", "").strip() or None

    per_niche, cross = collect(dsn)

    if args.json:
        payload = {
            "generated_at": datetime.now(UTC).isoformat(),
            "score_pct": sum(n.score for n in per_niche.values()) // len(per_niche),
            "overall_ready": (
                sum(n.score for n in per_niche.values()) // len(per_niche) >= 90
                and not cross.critical_gaps
            ),
            "niches": {n_id: n.to_dict() for n_id, n in per_niche.items()},
            "cross": cross.to_dict(),
            "critical_gaps": cross.critical_gaps,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(render_human(per_niche, cross))

    if cross.critical_gaps and not args.allow_partial:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
