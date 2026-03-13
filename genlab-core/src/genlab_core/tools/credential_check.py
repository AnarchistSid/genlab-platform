"""Check platform credentials before starting the engagement poller.

Validates that all required environment variables are set for each
platform. Optional platforms (TikTok, Threads) produce warnings instead
of failures.

Usage:
    python -m genlab_core.tools.credential_check
"""
from __future__ import annotations

import os
import sys

# ---------------------------------------------------------------------------
# Platform credential requirements
# ---------------------------------------------------------------------------

REQUIRED_PLATFORMS: dict[str, list[str]] = {
    "YouTube": [
        "YOUTUBE_CLIENT_ID",
        "YOUTUBE_CLIENT_SECRET",
        "YOUTUBE_REFRESH_TOKEN",
    ],
    "Instagram/Meta": [
        "META_ACCESS_TOKEN",
    ],
    "Facebook": [
        "FB_PAGE_ACCESS_TOKEN",
    ],
}

OPTIONAL_PLATFORMS: dict[str, list[str]] = {
    "X/Twitter": [
        "X_BEARER_TOKEN",
        "X_API_KEY",
        "X_API_SECRET",
        "X_ACCESS_TOKEN",
        "X_ACCESS_SECRET",
    ],
    "TikTok": [
        "TIKTOK_ACCESS_TOKEN",
    ],
    "Threads": [
        "THREADS_ACCESS_TOKEN",
    ],
}


def check_credentials() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Check all platform env vars and return (missing_required, missing_optional).

    Each dict maps platform name to list of missing env var names.
    """
    missing_required: dict[str, list[str]] = {}
    missing_optional: dict[str, list[str]] = {}

    for platform, env_vars in REQUIRED_PLATFORMS.items():
        missing = [v for v in env_vars if not os.environ.get(v, "").strip()]
        if missing:
            missing_required[platform] = missing

    for platform, env_vars in OPTIONAL_PLATFORMS.items():
        missing = [v for v in env_vars if not os.environ.get(v, "").strip()]
        if missing:
            missing_optional[platform] = missing

    return missing_required, missing_optional


def print_summary(
    missing_required: dict[str, list[str]],
    missing_optional: dict[str, list[str]],
) -> None:
    """Print a summary table of credential status."""
    all_platforms = list(REQUIRED_PLATFORMS.keys()) + list(OPTIONAL_PLATFORMS.keys())

    print("\n--- Platform Credential Check ---\n")
    print(f"{'Platform':<20} {'Status':<12} {'Missing'}")
    print("-" * 60)

    for platform in all_platforms:
        if platform in missing_required:
            status = "MISSING"
            missing = ", ".join(missing_required[platform])
        elif platform in missing_optional:
            status = "WARN"
            missing = ", ".join(missing_optional[platform])
        else:
            status = "READY"
            missing = ""
        print(f"{platform:<20} {status:<12} {missing}")

    print("-" * 60)

    if missing_required:
        print(f"\nFAILED: {len(missing_required)} required platform(s) missing credentials.")
    else:
        print("\nAll required platforms are ready.")

    if missing_optional:
        print(f"WARNING: {len(missing_optional)} optional platform(s) missing credentials.")

    print()


def main() -> int:
    """Run credential check and return exit code."""
    missing_required, missing_optional = check_credentials()
    print_summary(missing_required, missing_optional)
    return 1 if missing_required else 0


if __name__ == "__main__":
    sys.exit(main())
