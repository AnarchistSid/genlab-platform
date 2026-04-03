import { describe, it, expect } from 'vitest';
import {
  getNicheInfo,
  NICHE_COLORS,
  NICHE_LABELS,
  NICHE_HEX,
  NICHE_SHORT_LABELS,
  NICHE_CHANNELS,
  NICHE_IDS,
  getNiche,
  getActiveNiches,
  getAllNiches,
  nicheRegistry,
  type NicheId,
} from '../registry';

const ALL_NICHE_IDS: NicheId[] = ['ai_creators', 'gaming', 'sports', 'anime', 'movies'];

// ---------------------------------------------------------------------------
// getNicheInfo — valid niche IDs
// ---------------------------------------------------------------------------
describe('getNicheInfo', () => {
  it('returns correct info for ai_creators', () => {
    const info = getNicheInfo('ai_creators');
    expect(info.id).toBe('ai_creators');
    expect(info.label).toBe('Blackbox Brief');
    expect(info.shortLabel).toBe('AI');
    expect(info.hex).toBe('#00D4FF');
    expect(info.handle).toBe('@blackbox.brief');
    expect(info.color).toContain('var(');
  });

  it('returns correct info for gaming', () => {
    const info = getNicheInfo('gaming');
    expect(info.id).toBe('gaming');
    expect(info.label).toBe('CriticalRush');
    expect(info.hex).toBe('#F97316');
  });

  it('returns correct info for sports', () => {
    const info = getNicheInfo('sports');
    expect(info.id).toBe('sports');
    expect(info.label).toBe('ClutchWire');
    expect(info.hex).toBe('#FF2040');
  });

  it('returns correct info for anime', () => {
    const info = getNicheInfo('anime');
    expect(info.id).toBe('anime');
    expect(info.label).toBe('FrameDrift');
    expect(info.hex).toBe('#7B3FE4');
  });

  it('returns correct info for movies', () => {
    const info = getNicheInfo('movies');
    expect(info.id).toBe('movies');
    expect(info.label).toBe('SpliceReel');
    expect(info.hex).toBe('#C9A84C');
  });

  it('returns fallback for unknown niche ID', () => {
    const info = getNicheInfo('nonexistent_niche');
    // Fallback should use the unknown id as label and shortLabel
    expect(info.id).toBe('nonexistent_niche');
    expect(info.label).toBe('nonexistent_niche');
    expect(info.shortLabel).toBe('nonexistent_niche');
    expect(info.hex).toBe('#71717a');
    expect(info.handle).toBe('');
    expect(info.color).toBe('var(--text-muted)');
  });

  it('fallback for empty string returns safe defaults', () => {
    const info = getNicheInfo('');
    expect(info.hex).toBe('#71717a');
    expect(info.handle).toBe('');
  });
});

// ---------------------------------------------------------------------------
// NICHE_COLORS — all 5 niches have colors
// ---------------------------------------------------------------------------
describe('NICHE_COLORS', () => {
  it('contains entries for all 5 niches', () => {
    for (const id of ALL_NICHE_IDS) {
      expect(NICHE_COLORS[id]).toBeDefined();
      expect(NICHE_COLORS[id]).toContain('var(');
    }
  });

  it('has exactly 5 entries', () => {
    expect(Object.keys(NICHE_COLORS)).toHaveLength(5);
  });
});

// ---------------------------------------------------------------------------
// NICHE_LABELS — all 5 niches have labels
// ---------------------------------------------------------------------------
describe('NICHE_LABELS', () => {
  it('contains entries for all 5 niches', () => {
    for (const id of ALL_NICHE_IDS) {
      expect(NICHE_LABELS[id]).toBeDefined();
      expect(typeof NICHE_LABELS[id]).toBe('string');
      expect(NICHE_LABELS[id].length).toBeGreaterThan(0);
    }
  });

  it('has the correct display names', () => {
    expect(NICHE_LABELS['ai_creators']).toBe('Blackbox Brief');
    expect(NICHE_LABELS['gaming']).toBe('CriticalRush');
    expect(NICHE_LABELS['sports']).toBe('ClutchWire');
    expect(NICHE_LABELS['anime']).toBe('FrameDrift');
    expect(NICHE_LABELS['movies']).toBe('SpliceReel');
  });
});

// ---------------------------------------------------------------------------
// NICHE_HEX
// ---------------------------------------------------------------------------
describe('NICHE_HEX', () => {
  it('all values are valid hex color codes', () => {
    for (const id of ALL_NICHE_IDS) {
      expect(NICHE_HEX[id]).toMatch(/^#[0-9A-Fa-f]{6}$/);
    }
  });
});

// ---------------------------------------------------------------------------
// NICHE_CHANNELS
// ---------------------------------------------------------------------------
describe('NICHE_CHANNELS', () => {
  it('all handles start with @', () => {
    for (const id of ALL_NICHE_IDS) {
      expect(NICHE_CHANNELS[id]).toMatch(/^@/);
    }
  });
});

// ---------------------------------------------------------------------------
// NICHE_SHORT_LABELS
// ---------------------------------------------------------------------------
describe('NICHE_SHORT_LABELS', () => {
  it('short labels are concise (under 10 chars)', () => {
    for (const id of ALL_NICHE_IDS) {
      expect(NICHE_SHORT_LABELS[id].length).toBeLessThanOrEqual(10);
    }
  });
});

// ---------------------------------------------------------------------------
// NICHE_IDS
// ---------------------------------------------------------------------------
describe('NICHE_IDS', () => {
  it('contains all 5 niche IDs', () => {
    expect(NICHE_IDS).toHaveLength(5);
    for (const id of ALL_NICHE_IDS) {
      expect(NICHE_IDS).toContain(id);
    }
  });
});

// ---------------------------------------------------------------------------
// getNiche (full definition)
// ---------------------------------------------------------------------------
describe('getNiche', () => {
  it('returns full definition for valid niche IDs', () => {
    for (const id of ALL_NICHE_IDS) {
      const niche = getNiche(id);
      expect(niche.id).toBe(id);
      expect(niche.displayName).toBeTruthy();
      expect(niche.accentHex).toMatch(/^#/);
      expect(niche.pipelineStages).toBeDefined();
      expect(niche.pipelineStages.length).toBeGreaterThan(0);
    }
  });

  it('throws for unknown niche ID', () => {
    expect(() => getNiche('unknown' as NicheId)).toThrow('Unknown niche: unknown');
  });
});

// ---------------------------------------------------------------------------
// getActiveNiches
// ---------------------------------------------------------------------------
describe('getActiveNiches', () => {
  it('returns all 5 active niches', () => {
    const active = getActiveNiches();
    expect(active.length).toBe(5);
    active.forEach((n) => expect(n.isActive).toBe(true));
  });
});

// ---------------------------------------------------------------------------
// getAllNiches
// ---------------------------------------------------------------------------
describe('getAllNiches', () => {
  it('returns a copy (not the same reference)', () => {
    const a = getAllNiches();
    const b = getAllNiches();
    expect(a).not.toBe(b);
    expect(a).toEqual(b);
  });
});

// ---------------------------------------------------------------------------
// nicheRegistry Map
// ---------------------------------------------------------------------------
describe('nicheRegistry', () => {
  it('has 5 entries', () => {
    expect(nicheRegistry.size).toBe(5);
  });

  it('every entry has sharePointListIds', () => {
    for (const [, def] of nicheRegistry) {
      expect(def.sharePointListIds).toBeDefined();
      expect(def.sharePointListIds!.blueprints).toBeTruthy();
      expect(def.sharePointListIds!.stories).toBeTruthy();
    }
  });

  it('every entry has pipeline stages from SHARED_PIPELINE_STAGES', () => {
    const entries = [...nicheRegistry.values()];
    // All niches share the same pipeline stages reference
    const firstStages = entries[0].pipelineStages;
    for (const def of entries) {
      expect(def.pipelineStages).toBe(firstStages);
    }
  });
});
