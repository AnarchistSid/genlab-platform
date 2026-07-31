/**
 * On-screen overlay helpers for silent Playwright recordings.
 *
 * Rationale: `recordVideo` produces a silent WebM. Everything the
 * reviewer needs to follow the demo MUST be visible in the viewport.
 * These helpers inject in-page banners via `page.evaluate()` so each
 * step is narrated on the video without needing audio.
 *
 * Implementation note: all text is inserted via `textContent` (never
 * innerHTML) — even though callers use hardcoded English strings today,
 * that discipline keeps the helper safe if it's ever reused for
 * dynamic content in another compliance recording.
 */

import type { Page } from '@playwright/test';

/**
 * Persistent header pinned to the top of every frame.
 *
 * Call once per page (or after each navigation — the injected element
 * is per-document, so navigation clears it). Contains client location,
 * timestamp, and one-liner about what the reviewer is watching.
 */
export async function pinHeader(page: Page, lines: string[]): Promise<void> {
  await page.evaluate((lines) => {
    const existing = document.getElementById('compliance-header');
    if (existing) existing.remove();
    const el = document.createElement('div');
    el.id = 'compliance-header';
    // Build each line as a separate div with textContent — never
    // innerHTML — so any special chars in the caller's strings render
    // literally rather than being interpreted as HTML.
    for (const line of lines) {
      const row = document.createElement('div');
      row.textContent = line;
      el.appendChild(row);
    }
    Object.assign(el.style, {
      position: 'fixed',
      top: '0',
      left: '0',
      right: '0',
      background: 'rgba(0,0,0,0.88)',
      color: '#fff',
      padding: '10px 16px',
      fontFamily: 'ui-monospace, SFMono-Regular, monospace',
      fontSize: '13px',
      lineHeight: '1.5',
      zIndex: '2147483647',
      textAlign: 'center',
      pointerEvents: 'none',
    });
    document.body.appendChild(el);
    document.body.style.paddingTop = '80px';
  }, lines);
}

/**
 * Transient step banner. Appears near the top for `durationMs`, then fades.
 *
 * Blocks execution for the duration so the reviewer has time to read it
 * before the next action fires. Use liberally — every user-visible step
 * should have one.
 */
export async function step(
  page: Page,
  text: string,
  opts: { color?: string; durationMs?: number } = {},
): Promise<void> {
  const color = opts.color ?? '#1a73e8';
  const durationMs = opts.durationMs ?? 4000;
  await page.evaluate(
    ({ text, color, durationMs }) => {
      const el = document.createElement('div');
      el.textContent = text;
      Object.assign(el.style, {
        position: 'fixed',
        top: '96px',
        left: '50%',
        transform: 'translateX(-50%)',
        background: color,
        color: '#fff',
        padding: '14px 28px',
        borderRadius: '10px',
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
        fontSize: '17px',
        fontWeight: '600',
        zIndex: '2147483647',
        boxShadow: '0 10px 40px rgba(0,0,0,0.35)',
        maxWidth: '80vw',
        textAlign: 'center',
        pointerEvents: 'none',
        transition: 'opacity 400ms ease',
      });
      document.body.appendChild(el);
      setTimeout(() => {
        el.style.opacity = '0';
        setTimeout(() => el.remove(), 500);
      }, Math.max(500, durationMs - 500));
    },
    { text, color, durationMs },
  );
  await page.waitForTimeout(durationMs);
}

/**
 * ISO timestamp helper used in headers. Rounds to seconds so the reviewer
 * can read it without eye strain.
 */
export function stamp(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
}
