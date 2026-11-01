/**
 * Coquille de l'application : navigation par intention + bandeau d'état moteur.
 *
 * La navigation est organisée par ce que l'on veut faire (Découvrir, Utiliser,
 * Régler, Surveiller, Avancé) et chaque entrée porte une courte description en
 * infobulle. Chaque entrée est un vrai lien (`#/accueil`…) : clic droit, nouvel
 * onglet et partage fonctionnent. Sur grand écran, la barre se replie en icônes ;
 * sur petit écran, elle devient un tiroir ouvert par le bouton de la barre du haut.
 *
 * Le bandeau d'état affiche la santé RÉELLE de la connexion au moteur (état du
 * flux SSE), pas un voyant décoratif.
 */
import { useEffect, useRef, useSyncExternalStore } from 'react';
import { Bot, Menu, PanelLeftClose, PanelLeftOpen, X } from 'lucide-react';
import { useUIStore } from '../../state/uiStore';
import { useEngineStore } from '../../state/engineStore';
import { NAV_ENTRIES, NAV_GROUPS, NOM_PRODUIT, entreeDe, hashForView } from '../../lib/navigation';

const REQUETE_GRAND_ECRAN = '(min-width: 1024px)';

/** `true` quand la fenêtre est assez large pour la barre latérale fixe. */
function useGrandEcran(): boolean {
  return useSyncExternalStore(
    (notifier) => {
      const mq = window.matchMedia(REQUETE_GRAND_ECRAN);
      mq.addEventListener('change', notifier);
      return () => mq.removeEventListener('change', notifier);
    },
    () => window.matchMedia(REQUETE_GRAND_ECRAN).matches,
    () => true,
  );
}

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { currentView, isSidebarOpen, toggleSidebar, isMobileNavOpen, setMobileNavOpen } = useUIStore();
  const connection = useEngineStore((s) => s.connection);
  const status = useEngineStore((s) => s.status);
  const grandEcran = useGrandEcran();
  const zoneDefilement = useRef<HTMLDivElement>(null);
  const contenu = useRef<HTMLElement>(null);
  const boutonFermer = useRef<HTMLButtonElement>(null);

  const entree = entreeDe(currentView);
  // Étiquettes visibles : barre dépliée sur grand écran, toujours sur le tiroir mobile.
  const etiquettes = isSidebarOpen || !grandEcran;

  // Titre de l'onglet et retour en haut de page à chaque changement de vue.
  useEffect(() => {
    document.title = `${entree.label} · ${NOM_PRODUIT}`;
    zoneDefilement.current?.scrollTo({ top: 0 });
  }, [entree.label]);

  // Tiroir mobile : Échap le ferme, et le focus y entre à l'ouverture.
  useEffect(() => {
    if (!isMobileNavOpen) return;
    const surTouche = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMobileNavOpen(false);
    };
    window.addEventListener('keydown', surTouche);
    boutonFermer.current?.focus();
    return () => window.removeEventListener('keydown', surTouche);
  }, [isMobileNavOpen, setMobileNavOpen]);

  // Si la fenêtre s'élargit, le tiroir n'a plus lieu d'être.
  useEffect(() => {
    if (grandEcran && isMobileNavOpen) setMobileNavOpen(false);
  }, [grandEcran, isMobileNavOpen, setMobileNavOpen]);

  const connectionLabel =
    connection === 'open' ? 'Moteur connecté'
    : connection === 'connecting' ? 'Connexion…'
    : 'Moteur injoignable';
  const connectionColor =
    connection === 'open' ? 'bg-emerald-500'
    : connection === 'connecting' ? 'bg-amber-500'
    : 'bg-red-500';

  const tiroirFerme = !grandEcran && !isMobileNavOpen;

  return (
    <div className="flex h-dvh overflow-hidden bg-slate-950 font-sans text-slate-200">
      <button
        type="button"
        onClick={() => contenu.current?.focus()}
        className="sr-only z-50 rounded-md bg-sky-600 px-3 py-2 text-sm font-medium text-white focus:not-sr-only focus:fixed focus:left-3 focus:top-3"
      >
        Aller au contenu
      </button>

      {isMobileNavOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/60 lg:hidden"
          onClick={() => setMobileNavOpen(false)}
          aria-hidden
        />
      )}

      <aside
        id="navigation-principale"
        aria-label="Navigation principale"
        inert={tiroirFerme}
        className={`fixed inset-y-0 left-0 z-40 flex w-72 max-w-[85vw] flex-col border-r border-slate-800 bg-slate-900 transition-transform duration-200 ease-in-out lg:static lg:z-20 lg:max-w-none lg:translate-x-0 lg:transition-[width] ${
          isMobileNavOpen ? 'translate-x-0' : '-translate-x-full'
        } ${isSidebarOpen ? 'lg:w-64' : 'lg:w-[72px]'}`}
      >
        <div className="flex h-16 shrink-0 items-center justify-between border-b border-slate-800 px-4">
          {etiquettes && (
            <span className="flex items-center gap-2 text-lg font-bold tracking-tight text-white">
              <Bot className="h-6 w-6 text-sky-400" aria-hidden />
              {NOM_PRODUIT}
            </span>
          )}
          {/* Grand écran : replier / déplier. */}
          <button
            type="button"
            onClick={toggleSidebar}
            aria-label={isSidebarOpen ? 'Réduire la barre latérale' : 'Déplier la barre latérale'}
            title={isSidebarOpen ? 'Réduire' : 'Déplier'}
            className={`hidden rounded-lg p-2 text-slate-400 transition-colors hover:bg-slate-800 hover:text-slate-200 lg:block ${
              isSidebarOpen ? '' : 'mx-auto'
            }`}
          >
            {isSidebarOpen ? <PanelLeftClose className="h-5 w-5" /> : <PanelLeftOpen className="h-5 w-5" />}
          </button>
          {/* Petit écran : fermer le tiroir. */}
          <button
            ref={boutonFermer}
            type="button"
            onClick={() => setMobileNavOpen(false)}
            aria-label="Fermer le menu"
            className="rounded-lg p-2 text-slate-400 transition-colors hover:bg-slate-800 hover:text-slate-200 lg:hidden"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <nav className="flex-1 space-y-4 overflow-y-auto px-3 py-4" aria-label="Vues du moteur">
          {NAV_GROUPS.map((group) => {
            const entrees = NAV_ENTRIES.filter((e) => e.group === group.key);
            if (entrees.length === 0) return null;
            return (
              <div key={group.key} role="group" aria-label={group.label}>
                {etiquettes ? (
                  <p
                    className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500"
                    title={group.description}
                  >
                    {group.label}
                  </p>
                ) : (
                  <div className="mx-3 mb-2 border-t border-slate-800" aria-hidden />
                )}
                <ul className="space-y-0.5">
                  {entrees.map((item) => {
                    const Icon = item.icon;
                    const actif = currentView === item.key;
                    return (
                      <li key={item.key}>
                        <a
                          href={hashForView(item.key)}
                          onClick={() => setMobileNavOpen(false)}
                          aria-current={actif ? 'page' : undefined}
                          aria-label={etiquettes ? undefined : item.label}
                          title={etiquettes ? item.description : `${item.label} — ${item.description}`}
                          className={`flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${
                            actif
                              ? 'bg-sky-600 font-medium text-white'
                              : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
                          } ${etiquettes ? '' : 'justify-center'}`}
                        >
                          <Icon className="h-4 w-4 shrink-0" aria-hidden />
                          {etiquettes && <span className="truncate">{item.label}</span>}
                        </a>
                      </li>
                    );
                  })}
                </ul>
              </div>
            );
          })}
        </nav>

        {/* État réel de la liaison au moteur (flux SSE), pas un voyant décoratif. */}
        <div className="shrink-0 border-t border-slate-800 bg-slate-900/50 p-4" role="status">
          <div className="flex items-center gap-3">
            <span
              className={`h-2 w-2 shrink-0 rounded-full ${connectionColor} ${
                connection === 'open' ? 'animate-pulse' : ''
              }`}
              aria-hidden
            />
            {etiquettes ? (
              <div className="min-w-0">
                <p className="truncate text-xs font-medium text-slate-400">{connectionLabel}</p>
                {connection === 'open' && (
                  <p className="truncate text-[11px] text-slate-500">exécution : {status}</p>
                )}
              </div>
            ) : (
              <span className="sr-only">{connectionLabel}</span>
            )}
          </div>
        </div>
      </aside>

      <div className="relative flex min-w-0 flex-1 flex-col overflow-hidden">
        {/* Barre du haut : petit écran uniquement. */}
        <header className="z-10 flex h-14 shrink-0 items-center gap-3 border-b border-slate-800 bg-slate-900 px-4 lg:hidden">
          <button
            type="button"
            onClick={() => setMobileNavOpen(true)}
            aria-label="Ouvrir le menu"
            aria-expanded={isMobileNavOpen}
            aria-controls="navigation-principale"
            className="-ml-2 rounded-lg p-2 text-slate-300 transition-colors hover:bg-slate-800"
          >
            <Menu className="h-5 w-5" />
          </button>
          <span className="min-w-0 flex-1 truncate text-sm font-semibold text-slate-100">{entree.label}</span>
          <span
            className={`h-2 w-2 shrink-0 rounded-full ${connectionColor}`}
            role="img"
            aria-label={connectionLabel}
            title={connectionLabel}
          />
        </header>

        <main
          id="contenu-principal"
          ref={contenu}
          tabIndex={-1}
          className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-slate-950 outline-none"
        >
          <div
            className="pointer-events-none absolute left-1/2 top-0 h-[400px] w-[800px] max-w-full -translate-x-1/2 rounded-full bg-sky-900/15 blur-[120px]"
            aria-hidden
          />
          <div ref={zoneDefilement} className="z-10 flex-1 overflow-y-auto">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
