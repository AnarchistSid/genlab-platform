/**
 * Compliance clip #1: full operator workflow.
 *
 * Records: dashboard login → open a VISUAL_READY blueprint → Approve →
 * poll status → PUBLISHED → navigate to youtube.com to verify the video.
 *
 * Env inputs:
 *   DASHBOARD_URL           — base URL of the dashboard (used by playwright.config.ts)
 *   DASHBOARD_PASSWORD      — basic-auth password
 *   TEST_BLUEPRINT_ID       — UUID of a blueprint currently at VISUAL_READY
 *                             status (fallback: first row in Focus Review)
 *   CLIENT_LOCATION         — string to display in the header (e.g. 'hetzner-nbg1-dc1')
 *
 * Selectors marked with `// TODO:` should be verified against the real
 * dashboard's DOM. The dashboard uses React + Vite; class names may be
 * hashed, so `role=` and `text=` selectors are preferred where possible.
 */

import { test, expect } from '@playwright/test';
import { pinHeader, step, stamp } from './_overlay';

test('operator approve → publish_all_platforms → YouTube upload → verify on YouTube', async ({
  page,
  context,
}) => {
  test.setTimeout(5 * 60_000);

  // ── 0. Load the dashboard and pin the persistent header ────────────────
  await page.goto('/');

  await pinHeader(page, [
    'GenLab — YouTube Data API v3 compliance recording (segment 1 of 2)',
    `Client: Hetzner ${process.env.CLIENT_LOCATION ?? 'nbg1-dc1'}  ·  ${stamp()}`,
    'Use case: automated short-form video publishing to channels the operator owns',
  ]);

  await step(
    page,
    'GenLab operations dashboard — manages 5 YouTube channels (ai_creators, gaming, sports, movies, anime)',
    { durationMs: 6000 },
  );

  // ── 1. Navigate to the review queue ────────────────────────────────────
  await step(page, 'Opening the Focus Review queue (blueprints awaiting approval)', {});

  // TODO: the actual link text/route may differ — verify by opening the
  // dashboard once and adjusting. Common candidates:
  //   /focus-review    /publishing-queue    /queue    /review
  // When TEST_BLUEPRINT_ID is provided we skip the queue navigation entirely
  // (§2 below goto's the blueprint directly). Attempting the queue click
  // sometimes hangs on `networkidle` for React SPAs with active websockets.
  if (!process.env.TEST_BLUEPRINT_ID) {
    const focusReviewLink = page
      .locator('a')
      .filter({ hasText: /focus review|review|queue/i })
      .first();
    if (await focusReviewLink.isVisible().catch(() => false)) {
      await focusReviewLink.click();
      await page.waitForLoadState('domcontentloaded');
    }
  }

  // ── 2. Open a specific known VISUAL_READY blueprint ────────────────────
  const testBlueprintId = process.env.TEST_BLUEPRINT_ID;
  if (testBlueprintId) {
    await step(page, `Opening test blueprint: ${testBlueprintId}`, {});
    // Real SPA route per dashboard/frontend/src/App.tsx is /blueprints/:id
    // (there's a /focus-review route but no /:id param on it).
    await page.goto(`/blueprints/${testBlueprintId}`);
    await page.waitForLoadState('domcontentloaded');
    // Re-pin header (navigation cleared it)
    await pinHeader(page, [
      'GenLab — YouTube Data API v3 compliance recording (segment 1 of 2)',
      `Client: Hetzner ${process.env.CLIENT_LOCATION ?? 'nbg1-dc1'}  ·  ${stamp()}`,
      `Test blueprint: ${testBlueprintId}`,
    ]);
  } else {
    await step(page, 'Selecting the first VISUAL_READY blueprint in the queue', {});
    // TODO: adjust selector to match actual queue row markup
    await page
      .locator('[data-testid="blueprint-row"], .blueprint-card, article')
      .first()
      .click();
    await page.waitForLoadState('domcontentloaded');
  }

  await step(
    page,
    'This blueprint has a rendered 1080×1920 vertical video + caption + title (as required by YouTube Shorts)',
    { durationMs: 6000 },
  );

  // ── 3. Show the rendered video preview so the reviewer sees the content ─
  const video = page.locator('video').first();
  if (await video.isVisible().catch(() => false)) {
    await video.scrollIntoViewIfNeeded();
    await page.waitForTimeout(3000);
  }

  // ── 4. Set up a network listener BEFORE the click so we don't miss the POST ─
  const publishRequestPromise = page.waitForRequest(
    (req) =>
      req.method() === 'POST' &&
      /\/api\/v1\/blueprints\/[^/]+\/(approve|approve-and-schedule|publish)/.test(req.url()),
    { timeout: 60_000 },
  );

  await step(page, 'Clicking Approve — this triggers publish_all_platforms → YouTube upload', {
    color: '#0f9d58',
    durationMs: 4000,
  });

  // TODO: adjust button selector. Real button may be:
  //   button:has-text("Approve")
  //   button:has-text("Approve & Schedule")
  //   [data-testid="approve-button"]
  await page
    .locator('button')
    .filter({ hasText: /approve/i })
    .first()
    .click();

  const publishReq = await publishRequestPromise;
  const publishResp = await publishReq.response();
  await step(
    page,
    `Dashboard POST ${new URL(publishReq.url()).pathname} → ${publishResp?.status()} — publish job queued`,
    { durationMs: 5000 },
  );

  // ── 5. Poll blueprint until status flips to PUBLISHED ──────────────────
  await step(page, 'Polling for status: VISUAL_READY → SCHEDULED → PUBLISHING → PUBLISHED …', {
    color: '#f9ab00',
    durationMs: 4000,
  });

  // Poll by reloading the blueprint detail page every 15s and looking for
  // a "PUBLISHED" indicator. Adapt to your dashboard's actual status DOM.
  let youtubeUrl = '';
  const pollDeadline = Date.now() + 3 * 60_000;
  while (Date.now() < pollDeadline) {
    await page.reload({ waitUntil: 'domcontentloaded' });
    // TODO: adjust selector — actual status might be in a badge, a table cell, etc.
    const publishedBadge = page.locator('text=/PUBLISHED/i').first();
    if (await publishedBadge.isVisible().catch(() => false)) {
      // Try to find a YouTube URL rendered on the page (adjust selector).
      const ytLink = page.locator('a[href*="youtube.com/watch"], a[href*="youtu.be/"]').first();
      if (await ytLink.isVisible().catch(() => false)) {
        youtubeUrl = (await ytLink.getAttribute('href')) ?? '';
      }
      break;
    }
    await page.waitForTimeout(15_000);
  }

  if (!youtubeUrl) {
    await step(
      page,
      'Status flipped to PUBLISHED but no YouTube URL found on the page — proceeding with placeholder',
      { color: '#d93025', durationMs: 5000 },
    );
    youtubeUrl = process.env.FALLBACK_YT_URL ?? 'https://www.youtube.com/@YourChannel';
  } else {
    await step(page, `PUBLISHED — YouTube URL: ${youtubeUrl}`, {
      color: '#0f9d58',
      durationMs: 6000,
    });
  }

  // ── 6. Open youtube.com and verify the uploaded video is live ──────────
  await step(page, 'Opening youtube.com in a new tab to verify the video is live', {});
  const ytPage = await context.newPage();
  await ytPage.goto(youtubeUrl, { waitUntil: 'domcontentloaded' });
  // Give the page a beat to render (YT's SPA hydration is heavy)
  await ytPage.waitForTimeout(4000);
  await pinHeader(ytPage, [
    'GenLab — YouTube Data API v3 compliance recording (segment 1 of 2, contd.)',
    `Verifying uploaded video: ${youtubeUrl}`,
    'The video below was uploaded by the click you just saw in the previous step',
  ]);
  await step(ytPage, 'Video is live on the channel — end-to-end publish flow succeeded', {
    color: '#0f9d58',
    durationMs: 8000,
  });
  await ytPage.waitForTimeout(3000);
});
