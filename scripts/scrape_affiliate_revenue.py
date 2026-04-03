#!/usr/bin/env python3
"""Daily affiliate revenue scraper — uses Playwright to log into each network
dashboard and record clicks/conversions/earnings into the affiliate_revenue table.

Networks scraped:
  - Amazon IN — Associates Central
  - Amazon US — Associates Central
  - Cuelinks — Dashboard
  - EarnKaro — Dashboard
  - Admitad — API (no browser needed, uses REST)

Usage:
  uv run python scripts/scrape_affiliate_revenue.py

Requirements:
  - Playwright browsers installed: npx playwright install chromium
  - Credentials in .env: AMAZON_EMAIL, AMAZON_PASSWORD,
    CUELINKS_EMAIL, CUELINKS_PASSWORD,
    EARNKARO_EMAIL, EARNKARO_PASSWORD
  - Note: Amazon requires manual 2FA — run interactively first time,
    then use "Don't ask for codes on this device" to persist session.

Exit codes:
  0 = all networks scraped
  1 = some networks failed (partial data recorded)
  2 = fatal error
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import date
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("affiliate_scraper")

GENLAB_ROOT = Path(__file__).resolve().parent.parent

# Load .env
env_path = GENLAB_ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            key, _, val = line.partition("=")
            val = val.strip().strip("'").strip('"')
            os.environ.setdefault(key.strip(), val)

DATABASE_URL = os.environ.get("DATABASE_URL", "dbname=genlab")


def _record(network: str, clicks: int, conversions: int, revenue: float,
            currency: str = "INR", extra: dict | None = None) -> None:
    """Write a revenue record to the database."""
    import psycopg
    try:
        conn = psycopg.connect(DATABASE_URL, autocommit=True)
        conn.execute(
            "INSERT INTO affiliate_revenue "
            "(niche_id, network, clicks, conversions, revenue_amount, currency, date, extra) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            ("all", network, clicks, conversions, revenue, currency,
             date.today(), json.dumps(extra or {})),
        )
        conn.close()
        logger.info("[%s] Recorded: %d clicks, %d conv, %s %.2f",
                    network, clicks, conversions, currency, revenue)
    except Exception as e:
        logger.error("[%s] DB write failed: %s", network, e)


def scrape_admitad() -> bool:
    """Scrape Admitad via REST API (no browser needed)."""
    client_id = os.environ.get("ADMITAD_CLIENT_ID", "")
    client_secret = os.environ.get("ADMITAD_CLIENT_SECRET", "")
    website_id = os.environ.get("ADMITAD_WEBSITE_ID", "")
    if not all([client_id, client_secret]):
        logger.warning("[admitad] Missing API credentials, skipping")
        return False

    import urllib.request
    import base64

    try:
        # Get OAuth token (credentials in POST body, not Basic auth)
        token_data_str = (
            f"grant_type=client_credentials"
            f"&client_id={client_id}"
            f"&client_secret={client_secret}"
            f"&scope=public_data statistics websites"
        )
        req = urllib.request.Request(
            "https://api.admitad.com/token/",
            data=token_data_str.encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp = urllib.request.urlopen(req, timeout=15)
        token_data = json.loads(resp.read())
        access_token = token_data.get("access_token", "")
        if not access_token:
            logger.error("[admitad] No access token in response")
            return False

        # Get statistics for last 30 days
        from datetime import timedelta
        since = (date.today() - timedelta(days=30)).strftime("%d.%m.%Y")
        until = date.today().strftime("%d.%m.%Y")
        stats_url = (
            f"https://api.admitad.com/webmaster/statistics/websites/"
            f"?date_start={since}&date_end={until}&website={website_id}"
        )
        req = urllib.request.Request(
            stats_url,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp = urllib.request.urlopen(req, timeout=15)
        stats = json.loads(resp.read())

        results = stats.get("results", [])
        total_clicks = sum(r.get("clicks", 0) for r in results)
        total_leads = sum(r.get("leads", 0) for r in results)
        total_sales = sum(r.get("sales", 0) for r in results)
        total_revenue = sum(float(r.get("payment_sum_open", 0)) + float(r.get("payment_sum_approved", 0)) for r in results)

        _record("admitad", total_clicks, total_leads + total_sales, total_revenue,
                currency="USD", extra={"source": "api", "period": "30d",
                                       "website_id": website_id})
        return True

    except Exception as e:
        logger.error("[admitad] API scrape failed: %s", e)
        return False


def scrape_cuelinks_browser(page) -> bool:
    """Scrape Cuelinks dashboard via Playwright."""
    email = os.environ.get("CUELINKS_EMAIL", "")
    password = os.environ.get("CUELINKS_PASSWORD", "")
    if not email or not password:
        logger.warning("[cuelinks] Missing credentials, skipping")
        return False

    try:
        page.goto("https://www.cuelinks.com/login", timeout=15000)
        page.locator('input[placeholder=" "]').first.fill(email)
        page.locator('input[placeholder=" "]').nth(1).fill(password)
        page.get_by_role("button", name="Login").click()
        page.wait_for_url("**/dashboard**", timeout=15000)

        # Extract stats from dashboard
        clicks_el = page.locator("text=Clicks").locator("..").locator("..").first
        clicks_text = clicks_el.inner_text()
        clicks_match = re.search(r"(\d[\d,]*)", clicks_text)
        clicks = int(clicks_match.group(1).replace(",", "")) if clicks_match else 0

        earnings_el = page.locator("text=Earnings").locator("..").locator("..").first
        earnings_text = earnings_el.inner_text()
        earnings_match = re.search(r"([\d,.]+)", earnings_text)
        earnings = float(earnings_match.group(1).replace(",", "")) if earnings_match else 0.0

        _record("cuelinks", clicks, 0, earnings,
                extra={"source": "browser_scrape", "period": "dashboard_default"})
        return True

    except Exception as e:
        logger.error("[cuelinks] Browser scrape failed: %s", e)
        return False


def scrape_earnkaro_browser(page) -> bool:
    """Scrape EarnKaro dashboard via Playwright."""
    email = os.environ.get("EARNKARO_EMAIL", "")
    password = os.environ.get("EARNKARO_PASSWORD", "")
    if not email or not password:
        logger.warning("[earnkaro] Missing credentials, skipping")
        return False

    try:
        page.goto("https://earnkaro.com/login", timeout=15000)
        page.get_by_test_id("t-uname").fill(email)
        page.get_by_role("button", name="Continue").click()
        page.wait_for_timeout(3000)
        page.get_by_test_id("t-pwd").fill(password)
        page.get_by_role("button", name="Continue").click()
        page.wait_for_timeout(5000)

        # Extract total profit
        content = page.content()
        profit_match = re.search(r"Total Profit.*?₹([\d,.]+)", content, re.DOTALL)
        profit = float(profit_match.group(1).replace(",", "")) if profit_match else 0.0

        _record("earnkaro", 0, 0, profit,
                extra={"source": "browser_scrape", "note": "total_profit_lifetime"})
        return True

    except Exception as e:
        logger.error("[earnkaro] Browser scrape failed: %s", e)
        return False


def scrape_amazon_browser(page, region: str = "in") -> bool:
    """Scrape Amazon Associates dashboard via Playwright.

    NOTE: Amazon requires 2FA. The first run must be interactive so you can
    enter the OTP and check "Don't ask for codes on this device". Subsequent
    runs will use the persisted session.
    """
    domain = "amazon.in" if region == "in" else "amazon.com"
    tag = os.environ.get("AMAZON_IN_AFFILIATE_TAG", "") if region == "in" else os.environ.get("AMAZON_US_AFFILIATE_TAG", "")

    try:
        page.goto(f"https://affiliate-program.{domain}/home", timeout=30000)
        page.wait_for_timeout(5000)

        content = page.content()

        # Check if logged in (look for "Hello" or store ID)
        if tag not in content and "Hello" not in content:
            logger.warning("[amazon_%s] Not logged in (needs 2FA), skipping", region)
            return False

        # Extract clicks
        clicks_match = re.search(r"Clicks.*?(\d[\d,]*)", content, re.DOTALL)
        clicks = int(clicks_match.group(1).replace(",", "")) if clicks_match else 0

        # Extract earnings
        currency_sym = "₹" if region == "in" else r"\$"
        earnings_match = re.search(
            rf"Total (?:Fees|Commissions|Earnings).*?{currency_sym}([\d,.]+)",
            content, re.DOTALL,
        )
        earnings = float(earnings_match.group(1).replace(",", "")) if earnings_match else 0.0

        currency = "INR" if region == "in" else "USD"
        _record(f"amazon_{region}", clicks, 0, earnings, currency=currency,
                extra={"source": "browser_scrape", "store_id": tag, "period": "30d"})
        return True

    except Exception as e:
        logger.error("[amazon_%s] Browser scrape failed: %s", region, e)
        return False


def main() -> int:
    successes = 0
    total = 5

    # 1. Admitad (API — no browser needed)
    if scrape_admitad():
        successes += 1

    # 2-5. Browser-based scraping
    try:
        from playwright.sync_api import sync_playwright

        # Use the MCP Playwright profile — same browser profile that Claude Code
        # uses for interactive browsing. Amazon 2FA sessions are persisted there.
        mcp_profile = Path.home() / "Library" / "Caches" / "ms-playwright" / "mcp-chrome-d18f1a4"
        fallback_profile = GENLAB_ROOT / ".tmp" / "playwright_profile"

        if mcp_profile.exists() and (mcp_profile / "Default" / "Cookies").exists():
            user_data_dir = str(mcp_profile)
            logger.info("[scraper] Using MCP Playwright profile (has Amazon session)")
        else:
            user_data_dir = str(fallback_profile)
            os.makedirs(user_data_dir, exist_ok=True)
            logger.info("[scraper] Using fallback profile (run amazon_2fa_setup.py for Amazon access)")

        with sync_playwright() as p:
            browser = p.chromium.launch_persistent_context(
                user_data_dir,
                headless=True,
                viewport={"width": 1280, "height": 720},
            )
            page = browser.new_page()

            # 2. Cuelinks
            if scrape_cuelinks_browser(page):
                successes += 1

            # 3. EarnKaro
            if scrape_earnkaro_browser(page):
                successes += 1

            # 4. Amazon IN (may need 2FA on first run)
            if scrape_amazon_browser(page, "in"):
                successes += 1

            # 5. Amazon US (may need 2FA on first run)
            if scrape_amazon_browser(page, "us"):
                successes += 1

            browser.close()

    except ImportError:
        logger.error("Playwright not installed: pip install playwright && playwright install chromium")
    except Exception as e:
        logger.error("Browser scraping failed: %s", e)

    logger.info("Scraped %d/%d networks successfully", successes, total)
    return 0 if successes == total else 1


if __name__ == "__main__":
    sys.exit(main())
