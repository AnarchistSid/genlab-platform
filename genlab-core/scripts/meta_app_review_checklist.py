#!/usr/bin/env python3
"""Meta App Review readiness checker.

Validates that all prerequisites for instagram_manage_comments permission
are in place before submitting the App Review.

Run: python scripts/meta_app_review_checklist.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    msg = f"[{status}] {label}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    return condition


def main() -> int:
    results: list[bool] = []

    print("=" * 60)
    print("Meta App Review Readiness — instagram_manage_comments")
    print("=" * 60)
    print()

    # 1. Environment variables
    print("--- Environment Variables ---")
    results.append(_check(
        "META_ACCESS_TOKEN",
        bool(os.environ.get("META_ACCESS_TOKEN")),
        "Page access token for Graph API",
    ))
    results.append(_check(
        "META_APP_SECRET",
        bool(os.environ.get("META_APP_SECRET")),
        "Used for webhook signature verification",
    ))
    results.append(_check(
        "META_WEBHOOK_VERIFY_TOKEN",
        bool(os.environ.get("META_WEBHOOK_VERIFY_TOKEN")),
        "Verification token for webhook subscription",
    ))

    # 2. Required files
    print("\n--- Required Files ---")
    genlab_root = Path(os.environ.get("GENLAB_ROOT", "/Users/anarchistsid/GenLab"))
    core_root = genlab_root / "genlab-core" / "src" / "genlab_core"

    results.append(_check(
        "Instagram platform client",
        (core_root / "engagement" / "platform_clients" / "instagram.py").exists(),
    ))
    results.append(_check(
        "Comment processor pipeline",
        (core_root / "engagement" / "comment_processor.py").exists(),
    ))
    results.append(_check(
        "Toxicity gate",
        (core_root / "engagement" / "toxicity_gate.py").exists(),
    ))
    results.append(_check(
        "Persona engine",
        (core_root / "engagement" / "persona_engine.py").exists(),
    ))
    results.append(_check(
        "Spam filter",
        (core_root / "engagement" / "spam_filter.py").exists(),
    ))
    results.append(_check(
        "Webhook receiver",
        (core_root / "engagement" / "webhook.py").exists(),
    ))

    # 3. Webhook endpoint
    print("\n--- Webhook Infrastructure ---")
    webhook_plist = Path("/Users/anarchistsid/Library/LaunchAgents/com.genlab.engagement.webhook.plist")
    results.append(_check(
        "Webhook launchd plist",
        webhook_plist.exists(),
    ))

    # 4. Permission requirements
    print("\n--- Meta Permission Checklist ---")
    print("  [ ] Facebook Business Page connected to Instagram Professional Account")
    print("  [ ] App in Live mode (not Development)")
    print("  [ ] Privacy Policy URL configured in App Settings")
    print("  [ ] Data Deletion callback URL configured")
    print("  [ ] Webhook subscription for 'comments' field on 'instagram' object")
    print("  [ ] Screen recording showing comment reply flow")
    print("  [ ] Written explanation of comment engagement use case")

    # 5. Specific API permissions needed
    print("\n--- Required Permissions ---")
    print("  instagram_basic              — Read profile info")
    print("  instagram_manage_comments    — Read and reply to comments (NEEDS APP REVIEW)")
    print("  pages_show_list              — List pages (auto-granted)")
    print("  pages_read_engagement        — Read page engagement metrics")
    print()

    passed = sum(results)
    total = len(results)
    print(f"\nResult: {passed}/{total} automated checks passed")

    if passed < total:
        print("\nAction items:")
        print("1. Set missing environment variables in the webhook plist")
        print("2. Ensure all required files exist")
        print("3. Complete the manual checklist items above")
        print("4. Submit App Review at: https://developers.facebook.com/apps/2203397347132949/review/")
        return 1

    print("\nAll automated checks passed. Complete manual checklist, then submit App Review.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
