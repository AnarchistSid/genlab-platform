"""Pin Phase 3.C session 1 outreach draft generator:

  * `_personalize` fills [BRAND] + [NAME] AND the "Hi [BRAND]"
    greeting, favouring contact_first_name when present
  * `_personalize` falls back to brand_name when contact_first_name
    is missing so no raw [BRAND] token ships to operator
  * Runner exits 1 when DATABASE_URL is unset
  * `_ELIGIBLE_TIERS` excludes 'tracking' — never pitch a niche
    that isn't sponsor-ready
  * Dedup window constant not accidentally 0
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "generate_sponsorship_outreach",
    _ROOT / "scripts" / "generate_sponsorship_outreach.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules["generate_sponsorship_outreach"] = _MOD
_SPEC.loader.exec_module(_MOD)


class TestPersonalize:
    _TEMPLATE = (
        "Hi [BRAND],\n\n"
        "I'm reaching out about MyChannel — [NAME] here.\n\n"
        "Best,\n[NAME]"
    )

    def test_contact_first_name_used_for_greeting(self):
        out = _MOD._personalize(
            self._TEMPLATE,
            brand_name="AcmeCo",
            contact_first_name="Sarah",
            sender_name="Aditya",
        )
        assert "Hi Sarah," in out
        assert "AcmeCo" not in out or "Hi AcmeCo," not in out
        assert "[BRAND]" not in out
        assert "[NAME]" not in out
        assert "Aditya" in out

    def test_missing_contact_falls_back_to_brand_name(self):
        out = _MOD._personalize(
            self._TEMPLATE,
            brand_name="AcmeCo",
            contact_first_name=None,
            sender_name="Aditya",
        )
        assert "Hi AcmeCo," in out
        assert "[BRAND]" not in out

    def test_empty_string_contact_falls_back_to_brand_name(self):
        out = _MOD._personalize(
            self._TEMPLATE,
            brand_name="AcmeCo",
            contact_first_name="",
            sender_name="Aditya",
        )
        assert "Hi AcmeCo," in out

    def test_whitespace_only_contact_falls_back(self):
        out = _MOD._personalize(
            self._TEMPLATE,
            brand_name="AcmeCo",
            contact_first_name="   ",
            sender_name="Aditya",
        )
        # After .strip(), "" is falsy → brand_name fallback
        assert "Hi AcmeCo," in out

    def test_sender_name_replaces_all_NAME_tokens(self):
        template = "Best,\n[NAME]\n\n-- [NAME]"
        out = _MOD._personalize(
            template, brand_name="B", contact_first_name="C",
            sender_name="Aditya",
        )
        # Both [NAME] tokens replaced
        assert "[NAME]" not in out
        assert out.count("Aditya") == 2


class TestEligibleTiers:
    def test_tracking_excluded(self):
        """Tracking = niche not sponsor-ready. Any auto-outreach
        would be dishonest ('we're eligible for sponsorships' when
        we're not). Must NOT be in eligible set."""
        assert "tracking" not in _MOD._ELIGIBLE_TIERS

    def test_eligible_now_included(self):
        assert "eligible_now" in _MOD._ELIGIBLE_TIERS

    def test_within_2_months_included(self):
        assert "within_2_months" in _MOD._ELIGIBLE_TIERS

    def test_within_6_months_included(self):
        assert "within_6_months" in _MOD._ELIGIBLE_TIERS


class TestSafetyConstants:
    def test_dedup_window_at_least_two_weeks(self):
        """Prevents pestering brands and prevents duplicate-draft
        pileup if the timer double-fires. 2wk is the industry
        minimum for cold-email follow-ups."""
        assert _MOD._DEDUP_WINDOW_DAYS >= 14


class TestMainExitCodes:
    def test_missing_dsn_exits_1(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert _MOD.main(["--dry-run"]) == 1


class TestActiveNiches:
    def test_five_niches_configured(self):
        assert set(_MOD.ACTIVE_NICHES) == {
            "ai_creators", "anime", "gaming", "movies", "sports",
        }
