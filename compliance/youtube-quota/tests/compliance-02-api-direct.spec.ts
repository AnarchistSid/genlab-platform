/**
 * Compliance clip #2: direct YouTube Data API v3 videos.insert call,
 * with request + response visible in the browser.
 *
 * Depends on the compliance-only Flask endpoint at
 *   /api/v1/yt-quota-demo/page  (HTML demo page)
 *   /api/v1/yt-quota-demo/trigger  (POST that runs YouTubeClient.publish)
 * See dashboard/server/api/yt_quota_demo.py.
 *
 * Env inputs:
 *   DASHBOARD_URL           — base URL of the dashboard
 *   DASHBOARD_PASSWORD      — basic-auth password
 *   CLIENT_LOCATION         — header string
 *   GENLAB_YT_COMPLIANCE_DEMO=1  — MUST be set on the DASHBOARD host for
 *                                  the endpoint to be reachable
 *   YT_COMPLIANCE_ASSET     — set on the DASHBOARD host: absolute path to
 *                             the test MP4 the demo will upload
 *   YT_COMPLIANCE_NICHE     — set on the DASHBOARD host: which niche's
 *                             credentials to use (default: ai_creators)
 */

import { test } from '@playwright/test';
import { pinHeader, step, stamp } from './_overlay';

test('videos.insert direct call — request + response visible in browser', async ({ page }) => {
  test.setTimeout(4 * 60_000);

  await page.goto('/api/v1/yt-quota-demo/page');
  await page.waitForLoadState('domcontentloaded');

  await pinHeader(page, [
    'GenLab — YouTube Data API v3 videos.insert (segment 2 of 2)',
    `Client: Hetzner ${process.env.CLIENT_LOCATION ?? 'nbg1-dc1'}  ·  ${stamp()}`,
    'This page invokes YouTubeClient.publish() server-side and prints the API exchange',
  ]);

  await step(
    page,
    'About to call: POST https://www.googleapis.com/upload/youtube/v3/videos?part=snippet,status&uploadType=resumable',
    { durationMs: 6000 },
  );

  // Click the button on the demo page that triggers the upload
  await page.locator('#trigger-upload').click();

  await step(page, 'Server is calling videos.insert — this can take up to 90s for a short clip', {
    color: '#f9ab00',
    durationMs: 5000,
  });

  // Wait for the response JSON to render (the demo page updates #response-pre
  // when the POST /trigger completes). Look for something in the response
  // that only appears on success — e.g. "post_id" in the returned payload.
  await page.waitForSelector('#response-pre:has-text("post_id"), #response-pre:has-text("videoId")', {
    timeout: 3 * 60_000,
  });

  await step(page, 'videos.insert returned — request + response visible below', {
    color: '#0f9d58',
    durationMs: 5000,
  });

  // Give the reviewer time to read the request/response JSON
  await page.locator('#request-pre').scrollIntoViewIfNeeded();
  await page.waitForTimeout(6000);
  await page.locator('#response-pre').scrollIntoViewIfNeeded();
  await page.waitForTimeout(8000);

  // Extract the video ID from the response and navigate to youtube.com to verify
  const videoId = ((await page.locator('#video-id').textContent()) ?? '').trim();
  if (videoId && videoId !== '—') {
    await step(page, `Opening the uploaded video: youtube.com/watch?v=${videoId}`, {
      durationMs: 4000,
    });
    await page.goto(`https://www.youtube.com/watch?v=${videoId}`, {
      waitUntil: 'domcontentloaded',
    });
    await page.waitForTimeout(4000);
    await pinHeader(page, [
      'GenLab — YouTube Data API v3 videos.insert (segment 2 of 2, contd.)',
      `Verifying: https://www.youtube.com/watch?v=${videoId}`,
      'Uploaded as UNLISTED (compliance-safe) via the API call shown above',
    ]);
    await step(page, 'Video is live — direct API path verified end-to-end', {
      color: '#0f9d58',
      durationMs: 8000,
    });
    await page.waitForTimeout(3000);
  } else {
    await step(page, 'No video ID returned — check the response payload above for the error', {
      color: '#d93025',
      durationMs: 8000,
    });
  }
});
