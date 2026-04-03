# Playwright Creator Sessions

This project uses logged-in Playwright sessions for TikTok, Threads, and Instagram creator profile scraping.

## Required Environment

Set this in `.env`:

```bash
PLAYWRIGHT_PROFILE_DIR=/absolute/path/to/playwright-profile
```

Optional:

```bash
PLAYWRIGHT_HEADLESS=0
```

Use `PLAYWRIGHT_HEADLESS=0` during first-time login.

## Bootstrap The Persistent Profile

1. Install Playwright browser runtime:

```bash
/Users/anarchistsid/GenLab/Content\ Scraper/venv/bin/python3 -m playwright install chromium
```

2. Create/open the persistent profile in a one-time login run:

```bash
PLAYWRIGHT_PROFILE_DIR="/absolute/path/to/playwright-profile" \
PLAYWRIGHT_HEADLESS=0 \
/Users/anarchistsid/GenLab/Content\ Scraper/venv/bin/python3 - <<'PY'
from playwright.sync_api import sync_playwright
import os

profile = os.environ['PLAYWRIGHT_PROFILE_DIR']
with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir=profile,
        headless=False,
        viewport={"width": 1280, "height": 1600},
    )
    page = context.new_page()
    page.goto("https://www.instagram.com/")
    print("Log in manually, then close the browser window.")
    page.wait_for_timeout(180000)
    context.close()
PY
```

3. Repeat for:
- `https://www.threads.net/`
- `https://www.tiktok.com/`

Use the same `PLAYWRIGHT_PROFILE_DIR` so all sessions are persisted in one profile.

## Enable Stage-B Sources

In `/Users/anarchistsid/GenLab/Content Scraper/config/sources.yaml`, set these `enabled: true` entries:
- TikTok tag/profile sources
- Threads creator profile sources
- Instagram creator reels sources

## Failure Mode

If `PLAYWRIGHT_PROFILE_DIR` is missing, connector fetch returns non-fatal source errors and pipeline continues.
