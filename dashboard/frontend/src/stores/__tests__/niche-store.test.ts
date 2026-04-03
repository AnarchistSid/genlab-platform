import { describe, it, expect, beforeEach, vi } from 'vitest';
import type { NicheId } from '@/niches/registry';

// Zustand persist v5 uses `createJSONStorage()` which defaults to localStorage.
// jsdom doesn't always wire localStorage.setItem correctly, so we provide a
// mock storage *before* the store module is imported.
const storage = new Map<string, string>();
const mockLocalStorage = {
  getItem: vi.fn((key: string) => storage.get(key) ?? null),
  setItem: vi.fn((key: string, value: string) => { storage.set(key, value); }),
  removeItem: vi.fn((key: string) => { storage.delete(key); }),
  get length() { return storage.size; },
  clear: vi.fn(() => storage.clear()),
  key: vi.fn((_i: number) => null),
};

Object.defineProperty(globalThis, 'localStorage', { value: mockLocalStorage, writable: true });

// Now import the store (it will use our mocked localStorage)
const { useNicheStore } = await import('../niche-store');

// Mock document.documentElement.style.setProperty
const setPropertySpy = vi.spyOn(document.documentElement.style, 'setProperty');

describe('useNicheStore', () => {
  beforeEach(() => {
    storage.clear();
    mockLocalStorage.setItem.mockClear();
    mockLocalStorage.getItem.mockClear();
    setPropertySpy.mockClear();

    // Reset store to default via setNiche (which triggers the full middleware chain)
    useNicheStore.getState().setNiche('ai_creators');
    setPropertySpy.mockClear(); // Clear the spy after the reset call
  });

  // ---------------------------------------------------------------------------
  // Initial state
  // ---------------------------------------------------------------------------
  it('has ai_creators as the default selectedNicheId', () => {
    const state = useNicheStore.getState();
    expect(state.selectedNicheId).toBe('ai_creators');
  });

  it('has a setNiche function', () => {
    const state = useNicheStore.getState();
    expect(typeof state.setNiche).toBe('function');
  });

  it('selectedNiche is populated for default niche', () => {
    const state = useNicheStore.getState();
    expect(state.selectedNiche).not.toBeNull();
    expect(state.selectedNiche!.id).toBe('ai_creators');
    expect(state.selectedNiche!.displayName).toBe('Blackbox Brief');
  });

  // ---------------------------------------------------------------------------
  // setNiche updates selectedNicheId
  // ---------------------------------------------------------------------------
  it('updates selectedNicheId when setNiche is called with gaming', () => {
    useNicheStore.getState().setNiche('gaming');
    expect(useNicheStore.getState().selectedNicheId).toBe('gaming');
  });

  it('updates selectedNiche to the correct NicheDefinition', () => {
    useNicheStore.getState().setNiche('gaming');
    const state = useNicheStore.getState();
    expect(state.selectedNiche).not.toBeNull();
    expect(state.selectedNiche!.id).toBe('gaming');
    expect(state.selectedNiche!.displayName).toBe('CriticalRush');
  });

  it('sets selectedNiche to null when "all" is selected', () => {
    useNicheStore.getState().setNiche('all');
    const state = useNicheStore.getState();
    expect(state.selectedNicheId).toBe('all');
    expect(state.selectedNiche).toBeNull();
  });

  it('applies CSS theme variable on niche change', () => {
    useNicheStore.getState().setNiche('sports');
    expect(setPropertySpy).toHaveBeenCalledWith('--niche-current', '#FF2040');
  });

  it('applies first active niche color when "all" is selected', () => {
    useNicheStore.getState().setNiche('all');
    // "all" should apply the first active niche's hex (#00D4FF for ai_creators)
    expect(setPropertySpy).toHaveBeenCalledWith('--niche-current', '#00D4FF');
  });

  it('can cycle through all niche IDs', () => {
    const niches: (NicheId | 'all')[] = ['ai_creators', 'gaming', 'sports', 'anime', 'movies', 'all'];
    for (const id of niches) {
      useNicheStore.getState().setNiche(id);
      expect(useNicheStore.getState().selectedNicheId).toBe(id);
    }
  });

  it('sets correct accent colors for each niche', () => {
    const expected: Record<string, string> = {
      ai_creators: '#00D4FF',
      gaming: '#F97316',
      sports: '#FF2040',
      anime: '#7B3FE4',
      movies: '#C9A84C',
    };

    for (const [id, hex] of Object.entries(expected)) {
      setPropertySpy.mockClear();
      useNicheStore.getState().setNiche(id as NicheId);
      expect(setPropertySpy).toHaveBeenCalledWith('--niche-current', hex);
    }
  });

  // ---------------------------------------------------------------------------
  // Persistence
  // ---------------------------------------------------------------------------
  it('persists state to localStorage under "genlab-selected-niche"', () => {
    mockLocalStorage.setItem.mockClear();
    useNicheStore.getState().setNiche('anime');
    // zustand persist should have called setItem with the persist key
    expect(mockLocalStorage.setItem).toHaveBeenCalled();
    const calls = mockLocalStorage.setItem.mock.calls;
    const persistCall = calls.find((c: string[]) => c[0] === 'genlab-selected-niche');
    expect(persistCall).toBeDefined();
    const parsed = JSON.parse(persistCall![1]);
    expect(parsed.state.selectedNicheId).toBe('anime');
  });

  it('persisted state includes selectedNicheId', () => {
    useNicheStore.getState().setNiche('movies');
    const stored = storage.get('genlab-selected-niche');
    expect(stored).toBeDefined();
    const parsed = JSON.parse(stored!);
    expect(parsed.state.selectedNicheId).toBe('movies');
  });
});
