import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright config for the YouTube Data API v3 quota compliance recording.
 *
 * Design notes:
 * - `headless: false` — recordVideo requires a real, GPU-backed viewport.
 * - `fullyParallel: false` + `workers: 1` — one deterministic recording
 *   at a time, no cross-test races.
 * - `video: 'on'` — capture every test (default is 'retain-on-failure',
 *   which would drop successful recordings — the whole point here).
 * - Long `timeout` — the dashboard approve flow can take up to ~3 min
 *   for the pipeline to run `videos.insert` and flip status to PUBLISHED.
 */
export default defineConfig({
  testDir: './tests',
  timeout: 5 * 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: process.env.DASHBOARD_URL ?? 'https://dashboard.your-domain.example',
    headless: false,
    viewport: { width: 1440, height: 900 },
    video: {
      mode: 'on',
      size: { width: 1440, height: 900 },
    },
    trace: 'off',
    screenshot: 'off',
    actionTimeout: 20_000,
    navigationTimeout: 45_000,
    // Dashboard uses basic auth (see dashboard/CLAUDE.md). Skip httpCredentials
    // if you've already logged in via the login form / cookie; leave here if
    // your dashboard is fronted by htaccess-style basic auth.
    httpCredentials: process.env.DASHBOARD_PASSWORD
      ? { username: 'admin', password: process.env.DASHBOARD_PASSWORD }
      : undefined,
    // Pre-emptive Authorization header. The dashboard returns 401 without
    // a WWW-Authenticate challenge header (returns JSON `{"error": "..."}`
    // with just `content-type: application/json`), so Playwright's
    // httpCredentials never triggers — it only sends creds after a
    // challenge. This extraHTTPHeaders line sends Basic Auth on EVERY
    // request pre-emptively, which is what curl -u does. Kept alongside
    // httpCredentials so either path works depending on the auth response.
    extraHTTPHeaders: process.env.DASHBOARD_PASSWORD
      ? { Authorization: 'Basic ' + Buffer.from('admin:' + process.env.DASHBOARD_PASSWORD).toString('base64') }
      : undefined,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  outputDir: './recordings',
});
