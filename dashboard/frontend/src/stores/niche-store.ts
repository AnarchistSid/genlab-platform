import { create } from "zustand";
import { persist } from "zustand/middleware";
import {
  type NicheId,
  type NicheDefinition,
  getNiche,
  getActiveNiches,
} from "@/niches/registry";

interface NicheState {
  selectedNicheId: NicheId | "all";
  selectedNiche: NicheDefinition | null;
  setNiche: (id: NicheId | "all") => void;
}

function applyNicheTheme(id: NicheId | "all"): void {
  if (id === "all") {
    const first = getActiveNiches()[0];
    if (first) {
      document.documentElement.style.setProperty(
        "--niche-current",
        first.accentHex,
      );
    }
    return;
  }
  const niche = getNiche(id);
  document.documentElement.style.setProperty(
    "--niche-current",
    niche.accentHex,
  );
}

export const useNicheStore = create<NicheState>()(
  persist(
    (set) => ({
      selectedNicheId: "all" as NicheId | "all",
      selectedNiche: null,

      setNiche: (id: NicheId | "all") => {
        applyNicheTheme(id);
        set({
          selectedNicheId: id,
          selectedNiche: id === "all" ? null : getNiche(id),
        });
      },
    }),
    {
      name: "genlab-selected-niche",
    },
  ),
);

// Apply theme on initial load from persisted state
const initialId = useNicheStore.getState().selectedNicheId;
applyNicheTheme(initialId);
