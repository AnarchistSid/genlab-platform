import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  formatCompact,
  relativeTime,
  getTodayISO,
  getThumbnailInfo,
  formatRelativeTime,
  formatDuration,
  formatCost,
} from '../format';

// ---------------------------------------------------------------------------
// formatCompact
// ---------------------------------------------------------------------------
describe('formatCompact', () => {
  it('returns "-" for null input', () => {
    expect(formatCompact(null)).toBe('-');
  });

  it('returns "0" for zero', () => {
    expect(formatCompact(0)).toBe('0');
  });

  it('returns locale string for numbers under 1000', () => {
    expect(formatCompact(42)).toBe('42');
    expect(formatCompact(999)).toBe('999');
  });

  it('formats thousands with K suffix', () => {
    expect(formatCompact(1000)).toBe('1.0K');
    expect(formatCompact(1500)).toBe('1.5K');
    expect(formatCompact(12345)).toBe('12.3K');
    expect(formatCompact(999999)).toBe('1000.0K');
  });

  it('formats millions with M suffix', () => {
    expect(formatCompact(1_000_000)).toBe('1.0M');
    expect(formatCompact(1_500_000)).toBe('1.5M');
    expect(formatCompact(25_300_000)).toBe('25.3M');
  });

  it('handles negative numbers (below thresholds)', () => {
    // Negative numbers are < 1000, so they go through toLocaleString
    expect(formatCompact(-5)).toBe('-5');
  });
});

// ---------------------------------------------------------------------------
// relativeTime
// ---------------------------------------------------------------------------
describe('relativeTime', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-03-23T12:00:00Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('returns "just now" for timestamps within the last 60 seconds', () => {
    expect(relativeTime('2026-03-23T11:59:30Z')).toBe('just now');
    expect(relativeTime('2026-03-23T11:59:55Z')).toBe('just now');
  });

  it('returns minutes ago for timestamps within the last hour', () => {
    expect(relativeTime('2026-03-23T11:55:00Z')).toBe('5m ago');
    expect(relativeTime('2026-03-23T11:30:00Z')).toBe('30m ago');
    expect(relativeTime('2026-03-23T11:01:00Z')).toBe('59m ago');
  });

  it('returns hours ago for timestamps within the last day', () => {
    expect(relativeTime('2026-03-23T10:00:00Z')).toBe('2h ago');
    expect(relativeTime('2026-03-23T00:00:00Z')).toBe('12h ago');
  });

  it('returns days ago for timestamps older than 24 hours', () => {
    expect(relativeTime('2026-03-22T12:00:00Z')).toBe('1d ago');
    expect(relativeTime('2026-03-20T12:00:00Z')).toBe('3d ago');
    expect(relativeTime('2026-03-16T12:00:00Z')).toBe('7d ago');
  });
});

// ---------------------------------------------------------------------------
// formatRelativeTime (the more detailed version)
// ---------------------------------------------------------------------------
describe('formatRelativeTime', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-03-23T12:00:00Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('returns "--" for invalid date strings', () => {
    expect(formatRelativeTime('not-a-date')).toBe('--');
    expect(formatRelativeTime('')).toBe('--');
  });

  it('returns "just now" for very recent timestamps', () => {
    expect(formatRelativeTime('2026-03-23T11:59:30Z')).toBe('just now');
  });

  it('returns "X min ago" for minutes', () => {
    expect(formatRelativeTime('2026-03-23T11:50:00Z')).toBe('10 min ago');
  });

  it('returns "Xh ago" for hours', () => {
    expect(formatRelativeTime('2026-03-23T06:00:00Z')).toBe('6h ago');
  });

  it('returns "yesterday" for exactly 1 day ago', () => {
    expect(formatRelativeTime('2026-03-22T12:00:00Z')).toBe('yesterday');
  });

  it('returns "X days ago" for 2-29 days', () => {
    expect(formatRelativeTime('2026-03-18T12:00:00Z')).toBe('5 days ago');
  });

  it('returns "Xmo ago" for months', () => {
    expect(formatRelativeTime('2026-01-23T12:00:00Z')).toBe('1mo ago');
  });

  it('returns "Xy ago" for years', () => {
    expect(formatRelativeTime('2024-03-23T12:00:00Z')).toBe('2y ago');
  });

  it('handles future dates with "in" prefix', () => {
    expect(formatRelativeTime('2026-03-23T12:30:00Z')).toBe('in 30m');
    expect(formatRelativeTime('2026-03-23T15:00:00Z')).toBe('in 3h');
    expect(formatRelativeTime('2026-03-24T12:00:00Z')).toBe('tomorrow');
    expect(formatRelativeTime('2026-03-26T12:00:00Z')).toBe('in 3 days');
  });
});

// ---------------------------------------------------------------------------
// formatDuration
// ---------------------------------------------------------------------------
describe('formatDuration', () => {
  it('formats sub-minute durations as seconds', () => {
    expect(formatDuration(0)).toBe('0s');
    expect(formatDuration(45)).toBe('45s');
    expect(formatDuration(59.4)).toBe('59s');
  });

  it('formats exact minutes without seconds', () => {
    expect(formatDuration(60)).toBe('1m');
    expect(formatDuration(120)).toBe('2m');
    expect(formatDuration(300)).toBe('5m');
  });

  it('formats minutes with remaining seconds', () => {
    expect(formatDuration(75)).toBe('1m 15s');
    expect(formatDuration(135)).toBe('2m 15s');
  });
});

// ---------------------------------------------------------------------------
// formatCost
// ---------------------------------------------------------------------------
describe('formatCost', () => {
  it('formats cost as USD with two decimal places', () => {
    expect(formatCost(0)).toBe('$0.00');
    expect(formatCost(1.5)).toBe('$1.50');
    expect(formatCost(99.999)).toBe('$100.00');
    expect(formatCost(0.1)).toBe('$0.10');
  });
});

// ---------------------------------------------------------------------------
// getTodayISO
// ---------------------------------------------------------------------------
describe('getTodayISO', () => {
  it('returns a YYYY-MM-DD format string', () => {
    const result = getTodayISO();
    expect(result).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it('returns the correct date when mocked', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-03-23T15:30:00Z'));
    // Note: getTodayISO uses local date, so the result depends on timezone.
    // We just verify the format is correct.
    const result = getTodayISO();
    expect(result).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    // The year should be 2026
    expect(result.startsWith('2026')).toBe(true);
    vi.useRealTimers();
  });
});

// ---------------------------------------------------------------------------
// getThumbnailInfo
// ---------------------------------------------------------------------------
describe('getThumbnailInfo', () => {
  it('returns null for empty blueprint (no visual_paths, no slide_previews)', () => {
    expect(getThumbnailInfo({})).toBeNull();
    expect(getThumbnailInfo({ visual_paths: null, slide_previews: null })).toBeNull();
    expect(getThumbnailInfo({ visual_paths: null, slide_previews: [] })).toBeNull();
  });

  it('prefers local visual_paths over slide_previews', () => {
    const bp = {
      visual_paths: '/api/media/video.mp4',
      slide_previews: [{ url: 'https://cdn.example.com/thumb.jpg' }],
    };
    const result = getThumbnailInfo(bp);
    expect(result).toEqual({ url: '/api/media/video.mp4', isVideo: true });
  });

  it('detects video URLs by extension', () => {
    expect(getThumbnailInfo({ visual_paths: '/api/media/clip.mp4' })).toEqual({
      url: '/api/media/clip.mp4',
      isVideo: true,
    });
    expect(getThumbnailInfo({ visual_paths: '/api/media/clip.mov' })).toEqual({
      url: '/api/media/clip.mov',
      isVideo: true,
    });
    expect(getThumbnailInfo({ visual_paths: '/api/media/clip.webm' })).toEqual({
      url: '/api/media/clip.webm',
      isVideo: true,
    });
  });

  it('detects image URLs (non-video) correctly', () => {
    const result = getThumbnailInfo({ visual_paths: '/api/media/thumb.jpg' });
    expect(result).toEqual({ url: '/api/media/thumb.jpg', isVideo: false });
  });

  it('ignores non-local visual_paths and falls back to slide_previews', () => {
    const bp = {
      visual_paths: 'https://cdn.example.com/remote.mp4',
      slide_previews: [{ url: '/api/media/local.jpg' }],
    };
    const result = getThumbnailInfo(bp);
    expect(result).toEqual({ url: '/api/media/local.jpg', isVideo: false });
  });

  it('prefers local slide_previews over remote ones', () => {
    const bp = {
      slide_previews: [
        { url: 'https://cdn.example.com/remote.jpg' },
        { url: '/api/media/local.jpg' },
      ],
    };
    const result = getThumbnailInfo(bp);
    expect(result).toEqual({ url: '/api/media/local.jpg', isVideo: false });
  });

  it('falls back to first remote slide_preview when no local URLs exist', () => {
    const bp = {
      slide_previews: [
        { url: 'https://cdn.example.com/thumb1.jpg' },
        { url: 'https://cdn.example.com/thumb2.jpg' },
      ],
    };
    const result = getThumbnailInfo(bp);
    expect(result).toEqual({ url: 'https://cdn.example.com/thumb1.jpg', isVideo: false });
  });
});
