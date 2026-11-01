/**
 * Plan de navigation de l'IHM, organisé par intention (ce que l'on veut faire),
 * et correspondance vue ↔ adresse (`#/accueil`, `#/comprendre`…).
 *
 * Une seule source de vérité : la barre latérale, le routage par adresse, le
 * titre de l'onglet et les liens internes lisent tous ce tableau.
 */
import type { LucideIcon } from 'lucide-react';
import {
  Activity, Archive, Bot, BrainCircuit, BookOpen, FolderGit2, Gauge, House, HousePlug, KeyRound,
  Map as MapIcon, MessageSquare, Mic, Moon, PenLine, Plug, Radio, Rocket, Settings2, Wallet, Wrench,
  BarChart3,
} from 'lucide-react';

export type ViewKey =
  // Découvrir
  | 'accueil'
  | 'comprendre'
  | 'demo'
  | 'docs'
  // Utiliser
  | 'chat'
  | 'taskrunner'
  | 'homeassistant'
  // Régler
  | 'llm'
  | 'accounts'
  | 'agents'
  | 'mcp'
  | 'config'
  // Surveiller
  | 'operations'
  | 'observability'
  | 'sauvegardes'
  | 'dashboard'
  // Avancé
  | 'workflow'
  | 'prompt'
  | 'benchmarks'
  | 'autonomy'
  | 'projects'
  | 'setup';

export type GroupKey = 'decouvrir' | 'utiliser' | 'regler' | 'surveiller' | 'avance';

export interface NavGroup {
  key: GroupKey;
  label: string;
  description: string;
}

export interface NavEntry {
  key: ViewKey;
  /** Segment d'adresse : `#/<slug>`. */
  slug: string;
  label: string;
  /** Une phrase : ce que l'on trouve (infobulle de la barre latérale). */
  description: string;
  icon: LucideIcon;
  group: GroupKey;
}

export const DEFAULT_VIEW: ViewKey = 'accueil';

export const NAV_GROUPS: NavGroup[] = [
  { key: 'decouvrir', label: 'Découvrir', description: 'Comprendre ce que fait le moteur et voir qu’il fonctionne.' },
  { key: 'utiliser', label: 'Utiliser', description: 'Parler au moteur, lui confier du travail, piloter la maison.' },
  { key: 'regler', label: 'Régler', description: 'Choisir les modèles, les agents, les outils et les limites de dépense.' },
  { key: 'surveiller', label: 'Surveiller', description: 'Vérifier la santé du moteur et protéger ses données.' },
  { key: 'avance', label: 'Avancé', description: 'Outils pour aller plus loin : workflows, essais de prompts, projets.' },
];

export const NAV_ENTRIES: NavEntry[] = [
  // Découvrir
  { key: 'accueil', slug: 'accueil', label: 'Accueil', icon: House, group: 'decouvrir',
    description: 'Le verdict du moteur en une phrase, ses chiffres clés et par où commencer.' },
  { key: 'comprendre', slug: 'comprendre', label: 'Comprendre le moteur', icon: MapIcon, group: 'decouvrir',
    description: 'Le trajet d’une demande sur une carte cliquable, rejouable avec de vrais échanges.' },
  { key: 'demo', slug: 'demo', label: 'Démo vocale & maison', icon: Mic, group: 'decouvrir',
    description: 'De « Okay Nabu » à l’action dans la maison : chaque étape, avec les durées mesurées.' },
  { key: 'docs', slug: 'documentation', label: 'Documentation', icon: BookOpen, group: 'decouvrir',
    description: 'Les mécanismes expliqués (cascade, budget, agents…) et les recettes pour réduire les coûts.' },
  // Utiliser
  { key: 'chat', slug: 'chat', label: 'Chat', icon: MessageSquare, group: 'utiliser',
    description: 'Discuter avec le moteur : il choisit lui-même le modèle qui répond.' },
  { key: 'taskrunner', slug: 'taches', label: 'Lancer une tâche', icon: Rocket, group: 'utiliser',
    description: 'Confier un objectif au moteur, tout de suite ou dans la file d’attente.' },
  { key: 'homeassistant', slug: 'domotique', label: 'Domotique & vocal', icon: HousePlug, group: 'utiliser',
    description: 'Tester Home Assistant en direct, voir son périmètre et l’historique des commandes vocales.' },
  // Régler
  { key: 'llm', slug: 'modeles', label: 'Modèles & routage', icon: BrainCircuit, group: 'regler',
    description: 'Quels modèles sont actifs, à quel niveau (léger, moyen, fort) et dans quel ordre.' },
  { key: 'accounts', slug: 'comptes', label: 'Comptes & coûts', icon: Wallet, group: 'regler',
    description: 'Soldes, dépenses et coût par succès, fournisseur par fournisseur.' },
  { key: 'agents', slug: 'agents', label: 'Agents', icon: Bot, group: 'regler',
    description: 'Le rôle, le modèle et la consigne de chaque agent du moteur.' },
  { key: 'mcp', slug: 'mcp', label: 'Outils MCP', icon: Plug, group: 'regler',
    description: 'Les outils que le moteur offre à Claude ou Cursor, et ceux qu’il utilise lui-même.' },
  { key: 'config', slug: 'configuration', label: 'Configuration', icon: Wrench, group: 'regler',
    description: 'Tous les réglages du moteur, chacun avec son effet en une phrase.' },
  // Surveiller
  { key: 'operations', slug: 'operations', label: 'Opérations', icon: Gauge, group: 'surveiller',
    description: 'Version, circuits de protection, quotas et soldes en temps réel.' },
  { key: 'observability', slug: 'observabilite', label: 'Observabilité', icon: Activity, group: 'surveiller',
    description: 'Télémétrie : consommation, coûts et sessions sur la période choisie.' },
  { key: 'sauvegardes', slug: 'sauvegardes', label: 'Sauvegardes', icon: Archive, group: 'surveiller',
    description: 'Ce qui est sauvegardé, la dernière sauvegarde, et comment restaurer.' },
  { key: 'dashboard', slug: 'execution', label: 'Exécution en direct', icon: Radio, group: 'surveiller',
    description: 'Suivre une exécution en cours étape par étape, avec le flux d’événements.' },
  // Avancé
  { key: 'workflow', slug: 'workflows', label: 'Workflows', icon: Settings2, group: 'avance',
    description: 'Éditeur graphique des enchaînements d’agents.' },
  { key: 'prompt', slug: 'prompts', label: 'Prompt Studio', icon: PenLine, group: 'avance',
    description: 'Préparer une consigne, estimer son coût, choisir le modèle, puis l’envoyer.' },
  { key: 'benchmarks', slug: 'comparatif', label: 'Comparatif modèles', icon: BarChart3, group: 'avance',
    description: 'Performance observée des modèles : appels, coût, latence, classement.' },
  { key: 'autonomy', slug: 'autonomes', label: 'Agents autonomes', icon: Moon, group: 'avance',
    description: 'Les agents qui travaillent seuls (surveillance, mémoire, code) et leur rythme.' },
  { key: 'projects', slug: 'projets', label: 'Projets & git', icon: FolderGit2, group: 'avance',
    description: 'Les dépôts que le moteur connaît : branche, politique et zones du board.' },
  { key: 'setup', slug: 'installation', label: 'Installation', icon: KeyRound, group: 'avance',
    description: 'Contrôles de l’installation : accès, bases, configuration, fournisseurs, Home Assistant.' },
];

const PAR_CLE = new Map<ViewKey, NavEntry>(NAV_ENTRIES.map((e) => [e.key, e]));
const PAR_SLUG = new Map<string, NavEntry>(NAV_ENTRIES.map((e) => [e.slug, e]));

export function entreeDe(key: ViewKey): NavEntry {
  const e = PAR_CLE.get(key);
  if (!e) throw new Error(`Vue inconnue : ${key}`);
  return e;
}

/** `#/accueil` → `accueil` ; `#/demo?x=1` et `#demo` sont aussi acceptés ; sinon `null`. */
export function viewFromHash(hash: string): ViewKey | null {
  const brut = hash.replace(/^#\/?/, '').split(/[?/]/)[0]?.trim().toLowerCase() ?? '';
  return PAR_SLUG.get(brut)?.key ?? null;
}

export function hashForView(key: ViewKey): string {
  return `#/${entreeDe(key).slug}`;
}

export const NOM_PRODUIT = 'Moteur IA';
