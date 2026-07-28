/** Navigation entre vues + état sidebar (sans dépendance routeur). */
import { create } from "zustand";

export type ViewKey =
  // Pilotage
  | "dashboard"
  | "chat"
  | "taskrunner"
  // Domotique & vocal
  | "homeassistant"
  // Moteur & codage
  | "agents"
  | "workflow"
  | "llm"
  | "prompt"
  | "benchmarks"
  // Autonomie
  | "autonomy"
  // Optimisation
  | "operations"
  | "observability"
  | "accounts"
  | "config"
  // Installation & docs
  | "setup"
  | "docs";

// Alias pour compatibilité avec AppLayout généré
export type View = ViewKey;

const STORAGE_KEY = "moteur_ui_view";

/** Restaure le dernier onglet ouvert : un rechargement ne doit pas perdre le contexte. */
function initialView(): ViewKey {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    return (saved as ViewKey) ?? "dashboard";
  } catch {
    return "dashboard";
  }
}

interface UiStore {
  view: ViewKey;
  currentView: ViewKey; // alias lecture seule
  isSidebarOpen: boolean;
  setView: (v: ViewKey) => void;
  setCurrentView: (v: ViewKey) => void;
  toggleSidebar: () => void;
}

function persist(view: ViewKey) {
  try {
    localStorage.setItem(STORAGE_KEY, view);
  } catch {
    /* localStorage indisponible : la navigation reste fonctionnelle en mémoire */
  }
}

export const useUiStore = create<UiStore>((set) => ({
  view: initialView(),
  currentView: initialView(),
  isSidebarOpen: true,
  setView: (view) => {
    persist(view);
    set({ view, currentView: view });
  },
  setCurrentView: (view) => {
    persist(view);
    set({ view, currentView: view });
  },
  toggleSidebar: () => set((s) => ({ isSidebarOpen: !s.isSidebarOpen })),
}));

// Alias pour AppLayout qui importe useUIStore (différente casse)
export const useUIStore = useUiStore;
