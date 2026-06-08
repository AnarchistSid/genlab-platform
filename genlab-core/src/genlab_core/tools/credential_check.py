"""Check required credentials before starting the pipeline or engagement poller.

Validates that every env var the platform genuinely needs is set, and
optionally that the WARP SOCKS proxy is reachable. Originally only
covered 5 platforms; audit M-5 expanded coverage to the 20+ infra +
LLM + DB + proxy env vars the pipeline silently depends on.

Usage:
    # Just the global checks (pipeline startup, engagement poller startup)
    python -m genlab_core.tools.credential_check

    # Plus the per-niche prefixed variants (CRITICALRUSH_*, CLUTCHWIRE_*, ...)
    python -m genlab_core.tools.credential_check --niche gaming

    # Verify the WARP proxy actually works (Hetzner ops gate)
    python -m genlab_core.tools.credential_check --check-proxy

    # All of the above; exit non-zero on any required failure
    python -m genlab_core.tools.credential_check --niche gaming --check-proxy
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Required global credentials (any missing → exit non-zero)
# ---------------------------------------------------------------------------
#
# These are the env vars whose absence has historically caused silent
# pipeline failures debugged at 3am. Each entry maps a logical service
# name → the env vars that ALL must be present for it to function.
#
# Per-niche-prefixed variants (e.g. CRITICALRUSH_META_ACCESS_TOKEN) are
# resolved via niche_credentials.py and checked separately via
# ``--niche <id>``; this list covers the global / per-process fallbacks.

REQUIRED_PLATFORMS: dict[str, list[str]] = {
    "YouTube (publish)": [
        "YOUTUBE_CLIENT_ID",
        "YOUTUBE_CLIENT_SECRET",
        "YOUTUBE_REFRESH_TOKEN",
    ],
    "Instagram/Meta (publish)": [
        "META_ACCESS_TOKEN",
    ],
    "Facebook (publish)": [
        "FB_PAGE_ACCESS_TOKEN",
    ],
}

REQUIRED_INFRA: dict[str, list[str]] = {
    "LLM (content writing)": [
        "ANTHROPIC_API_KEY",  # primary content + hook generation
    ],
    "Database": [
        "DATABASE_URL",  # Postgres connection string for the primary data store
    ],
    "YouTube Data API (trending discovery)": [
        "YOUTUBE_API_KEY",  # quota-bound Data API v3 key for trending/search
    ],
    "Microsoft Graph (SharePoint backlog)": [
        "AZURE_TENANT_ID",
        "AZURE_CLIENT_ID",
        "AZURE_CLIENT_SECRET",
        "SHAREPOINT_SITE_ID",
    ],
}

OPTIONAL_PLATFORMS: dict[str, list[str]] = {
    "X/Twitter (publish)": [
        "X_BEARER_TOKEN",
        "X_API_KEY",
        "X_API_SECRET",
        "X_ACCESS_TOKEN",
        "X_ACCESS_SECRET",
    ],
    "TikTok (publish)": [
        "TIKTOK_ACCESS_TOKEN",
    ],
    "Threads (publish)": [
        "THREADS_ACCESS_TOKEN",
    ],
}

OPTIONAL_INFRA: dict[str, list[str]] = {
    "Image generation (LLM)": [
        "OPENAI_API_KEY",  # GPT-Image-1 for thumbnails; fallback if missing
    ],
    "TTS cascade (audio overlay)": [
        # cascade fallback: ElevenLabs → OpenAI TTS → Edge-TTS → gTTS.
        # All-missing → degraded silent reel; warn but don't block.
        "ELEVENLABS_API_KEY",
    ],
    "Movie metadata": [
        "TMDB_API_KEY",
        "OMDB_API_KEY",
    ],
    "Meta webhooks (engagement)": [
        "META_APP_SECRET",
        "META_WEBHOOK_VERIFY_TOKEN",
    ],
}

# Per-niche env var prefixes — used with ``--niche`` to validate the
# niche-scoped variants of the global vars above.
NICHE_PREFIXES: dict[str, str] = {
    "ai_creators": "BLACKBOXBRIEF",
    "gaming": "CRITICALRUSH",
    "sports": "CLUTCHWIRE",
    "movies": "SPLICEREEL",
    "anime": "FRAMEDRIFT",
}

# Per-niche env var SUFFIXES that should exist for each niche (mirror the
# global REQUIRED_PLATFORMS keys). Missing → MISSING in the niche-scoped
# section; tells the operator which channel can't publish where.
NICHE_REQUIRED_SUFFIXES: dict[str, list[str]] = {
    "YouTube": ["YOUTUBE_REFRESH_TOKEN", "YT_CHANNEL_ID"],
    "Instagram/Meta": ["META_ACCESS_TOKEN", "IG_USER_ID"],
    "Facebook": ["FB_PAGE_ACCESS_TOKEN", "FB_PAGE_ID"],
    "X/Twitter": ["X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"],
    "Threads": ["THREADS_ACCESS_TOKEN", "THREADS_USER_ID"],
}


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _missing_from_group(group: dict[str, list[str]]) -> dict[str, list[str]]:
    """Return a {service_name: missing_var_names} for the input group."""
    out: dict[str, list[str]] = {}
    for service, env_vars in group.items():
        missing = [v for v in env_vars if not os.environ.get(v, "").strip()]
        if missing:
            out[service] = missing
    return out


def check_credentials() -> dict[str, dict[str, list[str]]]:
    """Run all four global credential groups + return a structured result.

    Output shape:
        {
            "required_platforms": {service: [missing_var, ...], ...},
            "required_infra":     {service: [missing_var, ...], ...},
            "optional_platforms": {service: [missing_var, ...], ...},
            "optional_infra":     {service: [missing_var, ...], ...},
        }
    """
    return {
        "required_platforms": _missing_from_group(REQUIRED_PLATFORMS),
        "required_infra": _missing_from_group(REQUIRED_INFRA),
        "optional_platforms": _missing_from_group(OPTIONAL_PLATFORMS),
        "optional_infra": _missing_from_group(OPTIONAL_INFRA),
    }


def check_niche_credentials(niche_id: str) -> dict[str, list[str]]:
    """Return missing per-niche prefixed env vars for ``niche_id``.

    Each platform entry that is missing one or more prefixed vars appears
    in the output dict mapped to the missing var names. Unknown niches
    return an empty dict (the orchestrator validates niche_id elsewhere).
    """
    prefix = NICHE_PREFIXES.get(niche_id)
    if not prefix:
        return {}
    missing: dict[str, list[str]] = {}
    for platform, suffixes in NICHE_REQUIRED_SUFFIXES.items():
        absent = [
            f"{prefix}_{suffix}"
            for suffix in suffixes
            if not os.environ.get(f"{prefix}_{suffix}", "").strip()
        ]
        if absent:
            missing[platform] = absent
    return missing


def check_warp_proxy(proxy_env_var: str = "GENLAB_HTTP_SOCKS_PROXY") -> tuple[bool, str]:
    """Verify the WARP SOCKS proxy is reachable on its declared host:port.

    Returns ``(ok, message)`` — ``ok=False`` when the env var is unset OR
    when the configured host:port refuses a TCP connection. The yt-dlp
    download path NEEDS this proxy on Hetzner (datacenter IPs get bot-
    walled by YouTube without it).
    """
    proxy_url = os.environ.get(proxy_env_var, "").strip()
    if not proxy_url:
        # Also honour the older env var name some launchd plists may use.
        proxy_url = os.environ.get("YT_DLP_PROXY", "").strip()
    if not proxy_url:
        return False, f"{proxy_env_var} (and YT_DLP_PROXY fallback) both unset"

    parsed = urlparse(proxy_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 40000
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2.0)
    try:
        sock.connect((host, port))
        return True, f"reachable at {host}:{port}"
    except OSError as exc:
        return False, f"connect to {host}:{port} failed: {exc}"
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_summary(
    result: dict[str, dict[str, list[str]]],
    niche_missing: dict[str, list[str]] | None = None,
    proxy_status: tuple[bool, str] | None = None,
) -> None:
    """Print the full credential-check report."""
    print("\n=== Gen Lab Credential Check ===\n")

    def _section(title: str, group: dict[str, list[str]], total_known: int) -> None:
        present = total_known - len(group)
        print(f"--- {title} ({present}/{total_known} ready) ---")
        for service, missing in group.items():
            print(f"  MISSING  {service}: {', '.join(missing)}")
        if not group:
            print("  (all ready)")
        print()

    _section("Required platforms", result["required_platforms"], len(REQUIRED_PLATFORMS))
    _section("Required infra", result["required_infra"], len(REQUIRED_INFRA))
    _section("Optional platforms", result["optional_platforms"], len(OPTIONAL_PLATFORMS))
    _section("Optional infra", result["optional_infra"], len(OPTIONAL_INFRA))

    if niche_missing is not None:
        present = len(NICHE_REQUIRED_SUFFIXES) - len(niche_missing)
        print(f"--- Per-niche credentials ({present}/{len(NICHE_REQUIRED_SUFFIXES)} ready) ---")
        for platform, missing in niche_missing.items():
            print(f"  MISSING  {platform}: {', '.join(missing)}")
        if not niche_missing:
            print("  (all ready)")
        print()

    if proxy_status is not None:
        ok, message = proxy_status
        label = "READY" if ok else "MISSING"
        print(f"--- WARP SOCKS proxy ({label}) ---")
        print(f"  {message}")
        print()


def main() -> int:
    """CLI entry point.

    Returns 1 (failure) if any REQUIRED check fails, otherwise 0. The
    ``--niche`` and ``--check-proxy`` flags add additional REQUIRED
    sections — both have to pass when used.
    """
    parser = argparse.ArgumentParser(description="Verify Gen Lab credentials.")
    parser.add_argument(
        "--niche",
        choices=sorted(NICHE_PREFIXES.keys()),
        help="Also check the per-niche prefixed env vars for this niche",
    )
    parser.add_argument(
        "--check-proxy",
        action="store_true",
        help="Also TCP-probe the WARP SOCKS proxy (yt-dlp gate on Hetzner)",
    )
    args = parser.parse_args()

    result = check_credentials()
    niche_missing = check_niche_credentials(args.niche) if args.niche else None
    proxy_status = check_warp_proxy() if args.check_proxy else None

    print_summary(result, niche_missing, proxy_status)

    failed = (
        bool(result["required_platforms"])
        or bool(result["required_infra"])
        or bool(niche_missing)
        or (proxy_status is not None and not proxy_status[0])
    )
    if failed:
        print("FAILED — one or more required credentials are missing.")
        return 1

    print("All required credentials are present.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
