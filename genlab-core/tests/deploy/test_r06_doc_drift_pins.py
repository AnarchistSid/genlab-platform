"""R-06: regression pins for the documentation-drift items the audit
called out.

Strategy: grep the on-disk doc files for the specific stale strings.
A future regression that re-introduces "6 platform publishing" in
README, or stale UTC times in DEPLOY.md, will fail CI here — not on
the next audit-wave six months later.

Note on coverage: CLAUDE.md files and ``.claude/rules/`` are
**gitignored** (per commit 91c9727 — "remove internal AI tooling
files from tracking"). They can't be pinned in CI because CI doesn't
have them. The local copies are corrected inline for any dev who
has them; pins below cover the tracked README + DEPLOY.md surface.

The two gitignored-but-pin-able-locally checks are wrapped in
``if path.exists()`` so they enforce on dev machines while skipping
silently in CI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_GENLAB_ROOT = Path(__file__).resolve().parents[3]


# ── README.md (tracked) ────────────────────────────────────────────────


def test_r06_readme_no_6_platforms_claim() -> None:
    """README used to advertise 6-platform publishing including TikTok,
    but TikTok is a stub gated by ``TIKTOK_AUDIT_APPROVED``. Live
    count is 5."""
    body = (_GENLAB_ROOT / "README.md").read_text()
    assert "6 platform publishing" not in body, (
        "R-06 regression: README is back to claiming '6 platform publishing'. "
        "TikTok is a stub gated by TIKTOK_AUDIT_APPROVED — live count is 5."
    )


# ── deploy/DEPLOY.md (tracked) ─────────────────────────────────────────


def test_r06_deploy_md_no_stale_ai_creators_1800_time() -> None:
    """The DEPLOY.md schedule table claimed ai_creators fires at 18:00 IST,
    but the actual ``OnCalendar=`` value is 02:30 UTC (08:00 IST next day).
    Pin against the stale 18:00 entry returning."""
    body = (_GENLAB_ROOT / "deploy" / "DEPLOY.md").read_text()
    assert "ai_creators | 18:00 |" not in body, (
        "R-06 regression: DEPLOY.md schedule table is back to '| ai_creators "
        "| 18:00 |'. Actual systemd timer is 02:30 UTC = 08:00 IST."
    )


def test_r06_deploy_md_documents_grep_for_truth() -> None:
    """The corrected DEPLOY.md must point readers at the source of
    truth (``grep '^OnCalendar=' deploy/systemd-phase2/*.timer``)
    so future drift gets self-corrected, not papered over."""
    body = (_GENLAB_ROOT / "deploy" / "DEPLOY.md").read_text()
    assert "OnCalendar=' deploy/systemd-phase2/*.timer" in body, (
        "R-06 regression: DEPLOY.md no longer points readers at the "
        "live ``OnCalendar=`` source of truth — the grep one-liner is "
        "what keeps the table from drifting again."
    )


# ── Gitignored files: local-only pins ──────────────────────────────────
#
# CLAUDE.md and .claude/rules/ are gitignored. These tests enforce
# the corrections for anyone who has the files locally; in CI (where
# the files don't exist) they skip cleanly.


def _claude_md(rel: str) -> Path:
    return _GENLAB_ROOT / rel


@pytest.mark.parametrize(
    "rel,must_not_contain,hint",
    [
        (
            "CLAUDE.md",
            "legacy platform clients in `execution/utils/`",
            "BB legacy clients moved out of execution/utils/; "
            "the dir now holds config_loader/contracts/error_events/"
            "html_scraper/playwright_fetcher.",
        ),
        (
            "CriticalRush/CLAUDE.md",
            "orchestrates all 5 niches",
            "Real orchestrator is ``genlab_core.pipeline.cli``; "
            "CriticalRush's runner only carries gaming.",
        ),
    ],
)
def test_r06_local_claude_md_drift_pins(rel: str, must_not_contain: str, hint: str) -> None:
    """Local-only: enforce corrected facts on dev machines without
    breaking CI (where CLAUDE.md files don't exist)."""
    path = _claude_md(rel)
    if not path.exists():
        pytest.skip(f"{rel} not present (gitignored — expected in CI)")
    body = path.read_text()
    assert must_not_contain not in body, f"R-06 regression in {rel}: stale string is back. {hint}"


def test_r06_local_security_md_uses_cache_text_sanitizer() -> None:
    """Local-only: ``.claude/rules/security.md`` (gitignored) must
    point at ``genlab_core.cache.text_sanitizer`` for injection
    defence — the ``utils`` one is the Graph-API Unicode helper, NOT
    the injection guard. Misdirecting a SECURITY rule is the worst
    flavour of doc drift."""
    path = _GENLAB_ROOT / ".claude" / "rules" / "security.md"
    if not path.exists():
        pytest.skip("security.md not present (gitignored — expected in CI)")
    body = path.read_text()
    assert "genlab_core.cache.text_sanitizer" in body, (
        "R-06 regression: security.md no longer points at "
        "``genlab_core.cache.text_sanitizer`` for injection defence. "
        "That's where check_for_injection/sanitize_text live."
    )
