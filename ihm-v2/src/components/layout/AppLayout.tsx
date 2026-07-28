/**
 * Coquille de l'application : navigation groupée + bandeau d'état moteur.
 *
 * La navigation était une liste plate de 13 entrées sans hiérarchie, où
 * « Config moteur » et « Réglages » éditaient tous deux config.json sans qu'on
 * sache lequel choisir. Elle est désormais organisée par intention : piloter,
 * domotique, construire, autonomie, optimiser, comprendre.
 *
 * Le bandeau d'état affiche la santé RÉELLE de la connexion au moteur (état du
 * flux SSE), là où l'ancien pied de sidebar affichait « Moteur en ligne » en
 * dur, quelle que soit la situation.
 */
import { useUIStore } from '../../state/uiStore';
import type { View } from '../../state/uiStore';
import { useEngineStore } from '../../state/engineStore';
import {
  Bot, BrainCircuit, Settings2, BarChart3, KeyRound, Rocket, Menu,
  LayoutDashboard, MessageSquare, PenLine, Activity, Gauge, Wrench,
  HousePlug, Moon, BookOpen, Wallet,
} from 'lucide-react';

interface NavItem {
  id: View;
  label: string;
  icon: React.ElementType;
}

interface NavGroup {
  label: string;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    label: 'Pilotage',
    items: [
      { id: 'dashboard', label: "Vue d'ensemble", icon: LayoutDashboard },
      { id: 'chat', label: 'Chat', icon: MessageSquare },
      { id: 'taskrunner', label: 'Lancer une tâche', icon: Rocket },
    ],
  },
  {
    label: 'Domotique',
    items: [
      { id: 'homeassistant', label: 'Domotique & Vocal', icon: HousePlug },
    ],
  },
  {
    label: 'Moteur',
    items: [
      { id: 'agents', label: 'Agents', icon: Bot },
      { id: 'workflow', label: 'Workflows', icon: Settings2 },
      { id: 'llm', label: 'LLMs & routage', icon: BrainCircuit },
      { id: 'prompt', label: 'Prompt Studio', icon: PenLine },
      { id: 'benchmarks', label: 'Comparatif modèles', icon: BarChart3 },
    ],
  },
  {
    label: 'Autonomie',
    items: [
      { id: 'autonomy', label: 'Agents autonomes', icon: Moon },
    ],
  },
  {
    label: 'Optimisation',
    items: [
      { id: 'operations', label: 'Opérations', icon: Gauge },
      { id: 'observability', label: 'Observabilité', icon: Activity },
      { id: 'accounts', label: 'Comptes API', icon: Wallet },
      { id: 'config', label: 'Configuration', icon: Wrench },
    ],
  },
  {
    label: 'Comprendre',
    items: [
      { id: 'setup', label: 'Installation', icon: KeyRound },
      { id: 'docs', label: 'Documentation', icon: BookOpen },
    ],
  },
];

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { currentView, setCurrentView, isSidebarOpen, toggleSidebar } = useUIStore();
  const connection = useEngineStore((s) => s.connection);
  const status = useEngineStore((s) => s.status);

  const connectionLabel =
    connection === 'open' ? 'Moteur connecté'
    : connection === 'connecting' ? 'Connexion…'
    : 'Moteur injoignable';
  const connectionColor =
    connection === 'open' ? 'bg-emerald-500'
    : connection === 'connecting' ? 'bg-amber-500'
    : 'bg-red-500';

  return (
    <div className="flex h-screen overflow-hidden bg-slate-950 font-sans text-slate-200">
      <aside
        className={`${isSidebarOpen ? 'w-64' : 'w-20'} z-20 flex flex-col border-r border-slate-800 bg-slate-900 transition-all duration-200 ease-in-out`}
      >
        <div className="flex h-16 items-center justify-between border-b border-slate-800 px-4">
          {isSidebarOpen && (
            <span className="flex items-center gap-2 text-lg font-bold tracking-tight text-white">
              <Bot className="h-6 w-6 text-sky-400" />
              Tab5 Engine
            </span>
          )}
          <button
            onClick={toggleSidebar}
            className="mx-auto rounded-lg p-2 text-slate-400 transition-colors hover:bg-slate-800"
            title={isSidebarOpen ? 'Réduire' : 'Déplier'}
          >
            <Menu className="h-5 w-5" />
          </button>
        </div>

        <nav className="flex-1 space-y-4 overflow-y-auto px-3 py-4">
          {NAV_GROUPS.map((group) => (
            <div key={group.label}>
              {isSidebarOpen && (
                <p className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-600">
                  {group.label}
                </p>
              )}
              <div className="space-y-0.5">
                {group.items.map((item) => {
                  const Icon = item.icon;
                  const isActive = currentView === item.id;
                  return (
                    <button
                      key={item.id}
                      onClick={() => setCurrentView(item.id)}
                      className={`flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${
                        isActive
                          ? 'bg-sky-600 font-medium text-white'
                          : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
                      } ${!isSidebarOpen && 'justify-center'}`}
                      title={!isSidebarOpen ? item.label : undefined}
                    >
                      <Icon className="h-4 w-4 shrink-0" />
                      {isSidebarOpen && <span className="truncate">{item.label}</span>}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        {/* État réel de la liaison au moteur (flux SSE), pas un voyant décoratif. */}
        <div className="border-t border-slate-800 bg-slate-900/50 p-4">
          <div className="flex items-center gap-3">
            <span
              className={`h-2 w-2 shrink-0 rounded-full ${connectionColor} ${
                connection === 'open' ? 'animate-pulse' : ''
              }`}
            />
            {isSidebarOpen && (
              <div className="min-w-0">
                <p className="truncate text-xs font-medium text-slate-400">{connectionLabel}</p>
                {connection === 'open' && (
                  <p className="truncate text-[10px] text-slate-600">exécution : {status}</p>
                )}
              </div>
            )}
          </div>
        </div>
      </aside>

      <main className="relative flex min-w-0 flex-1 flex-col overflow-hidden bg-slate-950">
        <div
          className="pointer-events-none absolute left-1/2 top-0 h-[400px] w-[800px] -translate-x-1/2 rounded-full bg-sky-900/15 blur-[120px]"
          aria-hidden
        />
        <div className="z-10 flex-1 overflow-y-auto">{children}</div>
      </main>
    </div>
  );
}
