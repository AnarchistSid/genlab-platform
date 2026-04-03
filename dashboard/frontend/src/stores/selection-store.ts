import { create } from "zustand";
import { immer } from "zustand/middleware/immer";

interface SelectionState {
  selectedIds: Set<string>;
  lastSelectedIndex: number | null;
  toggle: (id: string, index?: number) => void;
  selectRange: (ids: string[]) => void;
  selectAll: (ids: string[]) => void;
  clearAll: () => void;
  isSelected: (id: string) => boolean;
}

export const useSelectionStore = create<SelectionState>()(
  immer((set, get) => ({
    selectedIds: new Set<string>(),
    lastSelectedIndex: null,

    toggle: (id: string, index?: number) =>
      set((state) => {
        if (state.selectedIds.has(id)) {
          state.selectedIds.delete(id);
        } else {
          state.selectedIds.add(id);
        }
        if (index !== undefined) {
          state.lastSelectedIndex = index;
        }
      }),

    selectRange: (ids: string[]) =>
      set((state) => {
        for (const id of ids) {
          state.selectedIds.add(id);
        }
      }),

    selectAll: (ids: string[]) =>
      set((state) => {
        state.selectedIds = new Set(ids);
      }),

    clearAll: () =>
      set((state) => {
        state.selectedIds = new Set();
        state.lastSelectedIndex = null;
      }),

    isSelected: (id: string) => get().selectedIds.has(id),
  }))
);
