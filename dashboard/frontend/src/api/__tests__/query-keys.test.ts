import { describe, it, expect } from 'vitest';
import { queryKeys } from '../query-keys';

// ---------------------------------------------------------------------------
// All key factories return arrays
// ---------------------------------------------------------------------------
describe('queryKeys — all factories return arrays', () => {
  it('blueprints.all() returns an array', () => {
    expect(Array.isArray(queryKeys.blueprints.all())).toBe(true);
    expect(queryKeys.blueprints.all()).toEqual(['blueprints']);
  });

  it('stories.all() returns an array', () => {
    expect(Array.isArray(queryKeys.stories.all())).toBe(true);
    expect(queryKeys.stories.all()).toEqual(['stories']);
  });

  it('pipeline.all() returns an array', () => {
    expect(Array.isArray(queryKeys.pipeline.all())).toBe(true);
    expect(queryKeys.pipeline.all()).toEqual(['pipeline']);
  });

  it('schedule.all() returns an array', () => {
    expect(Array.isArray(queryKeys.schedule.all())).toBe(true);
    expect(queryKeys.schedule.all()).toEqual(['schedule']);
  });

  it('analytics.all() returns an array', () => {
    expect(Array.isArray(queryKeys.analytics.all())).toBe(true);
    expect(queryKeys.analytics.all()).toEqual(['analytics']);
  });

  it('crossNiche.all() returns an array', () => {
    expect(Array.isArray(queryKeys.crossNiche.all())).toBe(true);
    expect(queryKeys.crossNiche.all()).toEqual(['cross-niche']);
  });

  it('queue.all() returns an array', () => {
    expect(Array.isArray(queryKeys.queue.all())).toBe(true);
    expect(queryKeys.queue.all()).toEqual(['queue']);
  });

  it('health() returns an array', () => {
    expect(Array.isArray(queryKeys.health())).toBe(true);
    expect(queryKeys.health()).toEqual(['health']);
  });

  it('learning/engagement/trends factories return arrays', () => {
    expect(Array.isArray(queryKeys.learning.status())).toBe(true);
    expect(Array.isArray(queryKeys.engagement.recent())).toBe(true);
    expect(Array.isArray(queryKeys.engagement.status())).toBe(true);
    expect(Array.isArray(queryKeys.trends.current())).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Parameterized keys include params
// ---------------------------------------------------------------------------
describe('queryKeys — parameterized keys include params', () => {
  it('blueprints.list() includes params', () => {
    const params = { niche: 'gaming', status: 'PUBLISHED' };
    const key = queryKeys.blueprints.list(params);
    expect(key).toEqual(['blueprints', params]);
    expect(key[1]).toBe(params);
  });

  it('blueprints.detail() includes the ID', () => {
    const key = queryKeys.blueprints.detail('bp-123');
    expect(key).toEqual(['blueprints', 'bp-123']);
  });

  it('stories.detail() includes the ID', () => {
    const key = queryKeys.stories.detail('story-456');
    expect(key).toEqual(['stories', 'detail', 'story-456']);
  });

  it('pipeline.status() includes nicheId', () => {
    const key = queryKeys.pipeline.status('gaming');
    expect(key).toEqual(['pipeline', 'gaming', 'status']);
  });

  it('pipeline.runs() includes nicheId and limit', () => {
    const key = queryKeys.pipeline.runs('anime', 10);
    expect(key).toEqual(['pipeline', 'anime', 'runs', 10]);
  });

  it('pipeline.runDetail() includes run ID', () => {
    const key = queryKeys.pipeline.runDetail('run-789');
    expect(key).toEqual(['pipeline', 'runs', 'run-789']);
  });

  it('schedule.range() includes from and to dates', () => {
    const key = queryKeys.schedule.range('2026-03-01', '2026-03-31');
    expect(key).toEqual(['schedule', '2026-03-01', '2026-03-31']);
  });

  it('schedule.coverage() includes from and to dates', () => {
    const key = queryKeys.schedule.coverage('2026-03-01', '2026-03-31');
    expect(key).toEqual(['schedule', 'coverage', '2026-03-01', '2026-03-31']);
  });

  it('analytics.section() includes nicheId, section, and params', () => {
    const params = { window: '7d' };
    const key = queryKeys.analytics.section('gaming', 'engagement', params);
    expect(key).toEqual(['analytics', 'gaming', 'engagement', params]);
  });

  it('analytics.overview() includes nicheId and window', () => {
    const key = queryKeys.analytics.overview('sports', '30d');
    expect(key).toEqual(['analytics', 'overview', 'sports', '30d']);
  });

  it('review.queue() includes nicheId', () => {
    const key = queryKeys.review.queue('movies');
    expect(key).toEqual(['review-queue', 'movies']);
  });

  it('crossNiche.analytics() includes window', () => {
    const key = queryKeys.crossNiche.analytics('7d');
    expect(key).toEqual(['cross-niche', 'analytics', '7d']);
  });

  it('queue.list() includes nicheId and optional status', () => {
    expect(queryKeys.queue.list('gaming', 'pending')).toEqual(['queue', 'gaming', 'pending']);
    expect(queryKeys.queue.list('gaming')).toEqual(['queue', 'gaming', undefined]);
  });

  it('queue.stats() includes nicheId', () => {
    expect(queryKeys.queue.stats('anime')).toEqual(['queue', 'stats', 'anime']);
  });

  it('config.sources() includes optional nicheId', () => {
    expect(queryKeys.config.sources('gaming')).toEqual(['config', 'sources', 'gaming']);
    expect(queryKeys.config.sources()).toEqual(['config', 'sources', undefined]);
  });
});

// ---------------------------------------------------------------------------
// Key hierarchy — child keys start with parent prefix
// ---------------------------------------------------------------------------
describe('queryKeys — hierarchical key design', () => {
  it('all blueprint keys start with "blueprints"', () => {
    expect(queryKeys.blueprints.all()[0]).toBe('blueprints');
    expect(queryKeys.blueprints.list()[0]).toBe('blueprints');
    expect(queryKeys.blueprints.detail('x')[0]).toBe('blueprints');
  });

  it('all pipeline keys start with "pipeline"', () => {
    expect(queryKeys.pipeline.all()[0]).toBe('pipeline');
    expect(queryKeys.pipeline.globalStatus()[0]).toBe('pipeline');
    expect(queryKeys.pipeline.status('x')[0]).toBe('pipeline');
    expect(queryKeys.pipeline.runs('x')[0]).toBe('pipeline');
    expect(queryKeys.pipeline.runDetail('x')[0]).toBe('pipeline');
  });

  it('all analytics keys start with "analytics"', () => {
    expect(queryKeys.analytics.all()[0]).toBe('analytics');
    expect(queryKeys.analytics.publishing()[0]).toBe('analytics');
    expect(queryKeys.analytics.funnel()[0]).toBe('analytics');
    expect(queryKeys.analytics.overview('x', '7d')[0]).toBe('analytics');
  });

  it('globalStatus is a child of pipeline.all', () => {
    const parent = queryKeys.pipeline.all();
    const child = queryKeys.pipeline.globalStatus();
    // child starts with parent elements
    expect(child[0]).toBe(parent[0]);
    expect(child.length).toBeGreaterThan(parent.length);
  });
});
