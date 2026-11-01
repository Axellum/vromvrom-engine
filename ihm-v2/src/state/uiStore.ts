/**
 * Navigation entre vues + état de la barre latérale.
 *
 * La vue courante est synchronisée avec l'adresse de la page (`#/accueil`,
 * `#/comprendre`…) : on peut partager un lien, recharger la page sans perdre sa
 * place et utiliser les boutons précédent / suivant du navigateur. Pas de
 * dépendance à un routeur : l'adresse est lue et écrite ici, à un seul endroit.
 */
import { create } from "zustand";
import { DEFAULT_VIEW, hashForView, viewFromHash } from "../lib/navigation";
import type { ViewKey } from "../lib/navigation";

export type { ViewKey } from "../lib/navigation";
// Alias pour compatibilité avec AppLayout généré
export type View = ViewKey;

const SIDEBAR_KEY = "moteur_ui_sidebar";

function sidebarInitiale(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_KEY) !== "closed";
  } catch {
    return true;
  }
}

function persistSidebar(open: boolean) {
  try {
    localStorage.setItem(SIDEBAR_KEY, open ? "open" : "closed");
  } catch {
    /* localStorage indisponible : la barre reste fonctionnelle en mémoire */
  }
}

/** Vue demandée par l'adresse courante, ou la vue par défaut (adresse vide ou inconnue). */
function vueDeLAdresse(): ViewKey {
  if (typeof window === "undefined") return DEFAULT_VIEW;
  return viewFromHash(window.location.hash) ?? DEFAULT_VIEW;
}

interface UiStore {
  view: ViewKey;
  currentView: ViewKey; // alias lecture seule
  /** Grand écran : barre latérale dépliée (true) ou réduite aux icônes (false). */
  isSidebarOpen: boolean;
  /** Petit écran : tiroir de navigation ouvert. */
  isMobileNavOpen: boolean;
  setView: (v: ViewKey) => void;
  setCurrentView: (v: ViewKey) => void;
  toggleSidebar: () => void;
  setMobileNavOpen: (open: boolean) => void;
}

export const useUiStore = create<UiStore>((set) => {
  const aller = (view: ViewKey) => {
    // Écrire l'adresse crée une entrée d'historique ; l'événement `hashchange`
    // met ensuite le store à jour (même chemin que précédent / suivant).
    if (typeof window !== "undefined" && viewFromHash(window.location.hash) !== view) {
      window.location.hash = hashForView(view);
    }
    set({ view, currentView: view, isMobileNavOpen: false });
  };
  const initiale = vueDeLAdresse();
  return {
    view: initiale,
    currentView: initiale,
    isSidebarOpen: sidebarInitiale(),
    isMobileNavOpen: false,
    setView: aller,
    setCurrentView: aller,
    toggleSidebar: () =>
      set((s) => {
        persistSidebar(!s.isSidebarOpen);
        return { isSidebarOpen: !s.isSidebarOpen };
      }),
    setMobileNavOpen: (open) => set({ isMobileNavOpen: open }),
  };
});

// Alias pour AppLayout qui importe useUIStore (différente casse)
export const useUIStore = useUiStore;

if (typeof window !== "undefined") {
  // Adresse vide ou inconnue : on écrit l'adresse canonique SANS créer d'entrée
  // d'historique, pour que « précédent » ne retombe pas sur une page sans adresse.
  if (viewFromHash(window.location.hash) === null) {
    window.history.replaceState(null, "", hashForView(DEFAULT_VIEW));
  }
  window.addEventListener("hashchange", () => {
    const view = vueDeLAdresse();
    if (useUiStore.getState().view !== view) {
      useUiStore.setState({ view, currentView: view, isMobileNavOpen: false });
    }
    if (viewFromHash(window.location.hash) === null) {
      window.history.replaceState(null, "", hashForView(view));
    }
  });
}
