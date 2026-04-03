#!/usr/bin/env python3
"""One-time interactive setup: log into Amazon IN and US with 2FA,
check "Don't ask for codes on this device", and save the session to
the persistent Playwright profile used by the daily affiliate scraper.

Usage:
    python3 scripts/amazon_2fa_setup.py

After running this, the daily scraper at 23:00 IST will be able to
access Amazon Associates dashboards without requiring 2FA.
"""
import os
from pathlib import Path

GENLAB_ROOT = Path(
    os.environ.get(
        "GENLAB_PROJECT_ROOT",
        str(Path(__file__).resolve().parent.parent),
    )
)

# Use the MCP Playwright profile — shared with Claude Code's browser sessions.
# When Claude Code logs into Amazon via Playwright MCP, the session is saved here.
MCP_PROFILE = Path.home() / "Library" / "Caches" / "ms-playwright" / "mcp-chrome-d18f1a4"
FALLBACK_PROFILE = GENLAB_ROOT / ".tmp" / "playwright_profile"

if MCP_PROFILE.exists():
    USER_DATA_DIR = str(MCP_PROFILE)
    print(f"Using MCP Playwright profile: {MCP_PROFILE}")
else:
    USER_DATA_DIR = str(FALLBACK_PROFILE)
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    print(f"Using fallback profile: {FALLBACK_PROFILE}")

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("ERROR: Playwright not installed.")
    print("Run: pip install playwright && playwright install chromium")
    exit(1)

with sync_playwright() as p:
    # Launch VISIBLE browser so user can interact with 2FA
    browser = p.chromium.launch_persistent_context(
        USER_DATA_DIR,
        headless=False,
        viewport={"width": 1280, "height": 800},
    )
    page = browser.new_page()

    print("\n" + "=" * 60)
    print("  AMAZON AFFILIATE SESSION SETUP")
    print("=" * 60)

    # Amazon IN
    print("\n--- STEP 1: Amazon India ---")
    print("1. Log in with your email + password")
    print("2. Enter the authenticator OTP")
    print("3. ✅ CHECK 'Don't ask for codes on this device'")
    print("4. Wait until you see the Associates Central dashboard")
    print()

    page.goto("https://affiliate-program.amazon.in/home")
    input("Press ENTER after Amazon IN login is complete... ")

    # Amazon US
    print("\n--- STEP 2: Amazon US ---")
    print("1. Log in with your email + password")
    print("2. Enter the authenticator OTP")
    print("3. ✅ CHECK 'Don't require code on this browser'")
    print("4. Wait until you see the Associates Central dashboard")
    print()

    page.goto("https://affiliate-program.amazon.com/home")
    input("Press ENTER after Amazon US login is complete... ")

    print("\n✓ Sessions saved to:", USER_DATA_DIR)
    print("✓ The daily scraper will use these sessions (no 2FA needed)")
    print()
    browser.close()
