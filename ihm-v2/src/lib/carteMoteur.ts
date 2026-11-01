/**
 * Données de la carte « Comprendre le moteur » : étapes, nœuds, et correspondance
 * entre le `routing_type` enregistré par le moteur (journal d'audit vocal) et le
 * chemin réellement pris sur la carte.
 *
 * Les textes décrivent des comportements lus dans le code et la documentation du
 * moteur (docs/guide, Docs.tsx). Les durées citées portent leur date de mesure.
 */
import type { LucideIcon } from 'lucide-react';
import {
  Brain, Cable, House, Layers, MessageSquare, Mic, Plug, Route, ScrollText, Split, Workflow, Wrench,
} from 'lucide-react';
import type { ViewKey } from './navigation';

export type EtapeId = 'entrees' | 'routeur' | 'chemins' | 'cascade' | 'actions' | 'traces';

export type NoeudId =
  | 'in-vocal' | 'in-chat' | 'in-mcp' | 'in-api'
  | 'routeur'
  | 'p-commande' | 'p-question' | 'p-discussion' | 'p-complexe'
  | 'cascade'
  | 'act-ha' | 'act-outils' | 'act-memoire'
  | 'traces';

export type CheminId = 'p-commande' | 'p-question' | 'p-discussion' | 'p-complexe';

export const ETAPES: Array<{ id: EtapeId; numero: number; titre: string; sousTitre: string }> = [
  { id: 'entrees', numero: 1, titre: 'Entrées', sousTitre: 'Par où arrive la demande' },
  { id: 'routeur', numero: 2, titre: 'Routeur', sousTitre: 'Qui décide du chemin' },
  { id: 'chemins', numero: 3, titre: 'Chemins', sousTitre: 'Quatre façons de répondre' },
  { id: 'cascade', numero: 4, titre: 'Modèles', sousTitre: 'Qui répond, dans quel ordre' },
  { id: 'actions', numero: 5, titre: 'Actions', sousTitre: 'Ce que le moteur fait' },
  { id: 'traces', numero: 6, titre: 'Traces', sousTitre: 'Ce qui est gardé' },
];

export interface LienReglage {
  vue: ViewKey;
  texte: string;
}

export interface NoeudDef {
  id: NoeudId;
  etape: EtapeId;
  titre: string;
  /** Une ligne affichée sur la carte. */
  court: string;
  icone: LucideIcon;
  /** « Ce que c'est » : deux phrases. Marqueurs `[[terme|libellé]]` rendus par `Glose`. */
  quoi: string;
  /** « Pourquoi c'est utile ». */
  utile: string;
  reglages: LienReglage[];
}

export const NOEUDS: NoeudDef[] = [
  {
    id: 'in-vocal', etape: 'entrees', titre: 'Écran et voix', court: 'Okay Nabu → Home Assistant', icone: Mic,
    quoi: 'L’écran de la maison (Tab5) écoute le [[mot-de-reveil|mot de réveil]], envoie votre phrase à [[ha|Home Assistant]], qui la transcrit ([[stt|reconnaissance vocale]]) puis la confie au moteur. Le moteur reçoit le texte avec un mode : « ha » pour les commandes, « chat » pour la discussion.',
    utile: 'C’est l’entrée la plus rapide : un ordre simple n’attend aucun [[llm|modèle de langage]]. Tout le trajet est détaillé dans la page Démo vocale & maison.',
    reglages: [{ vue: 'demo', texte: 'Démo vocale & maison' }, { vue: 'homeassistant', texte: 'Domotique & vocal' }],
  },
  {
    id: 'in-chat', etape: 'entrees', titre: 'Chat de l’interface', court: 'Vous écrivez, le moteur répond', icone: MessageSquare,
    quoi: 'Le Chat de cette interface envoie votre message au moteur, qui répond au fil de l’eau. C’est le moteur qui choisit le modèle qui répond.',
    utile: 'Le moyen le plus simple de poser une question ou de lancer un raisonnement sans quitter l’interface.',
    reglages: [{ vue: 'chat', texte: 'Chat' }, { vue: 'taskrunner', texte: 'Lancer une tâche' }],
  },
  {
    id: 'in-mcp', etape: 'entrees', titre: 'Outils MCP', court: 'Claude, Cursor…', icone: Plug,
    quoi: 'Des applications comme Claude ou Cursor peuvent appeler le moteur comme un outil, grâce au protocole [[mcp|MCP]] : lire son état, chercher dans sa mémoire, déléguer un raisonnement ou lancer un agent.',
    utile: 'Le moteur devient une boîte à outils partagée : vous gardez votre éditeur favori, le moteur apporte la mémoire, la maison et l’économie de modèles.',
    reglages: [{ vue: 'mcp', texte: 'Outils MCP' }],
  },
  {
    id: 'in-api', etape: 'entrees', titre: 'API HTTP', court: 'Scripts et applications', icone: Cable,
    quoi: 'Tout programme peut appeler les routes du moteur ([[api|API]]) avec la clé du moteur, y compris Home Assistant. Une interface compatible avec celle d’OpenAI existe aussi, pour les applications qui la connaissent.',
    utile: 'C’est la porte des scripts et des automatisations. Sans clé valide, elle reste fermée.',
    reglages: [{ vue: 'setup', texte: 'Installation' }, { vue: 'config', texte: 'Configuration' }],
  },
  {
    id: 'routeur', etape: 'routeur', titre: 'Routeur', court: 'Raccourcis sans IA, puis classification', icone: Split,
    quoi: 'Le routeur regarde la demande et choisit son chemin. D’abord des règles ([[fast-path]]) attrapent les cas évidents sans appeler de [[llm|modèle de langage]] ; sinon une classification décide s’il s’agit d’une commande, d’une question, d’une discussion ou d’une tâche complexe.',
    utile: 'Plus la demande est simple, moins elle coûte : un ordre domotique ne doit jamais déclencher un modèle payant.',
    reglages: [{ vue: 'config', texte: 'Configuration (politique de routage)' }, { vue: 'llm', texte: 'Modèles & routage' }],
  },
  {
    id: 'p-commande', etape: 'chemins', titre: 'Commande de la maison', court: '« Allume le salon »', icone: House,
    quoi: 'La phrase d’ordre est reconnue dans le catalogue de commandes (correspondance exacte, puis approchée), l’appareil est vérifié, puis l’ordre part vers Home Assistant. Aucun [[llm|modèle de langage]] n’intervient.',
    utile: 'Rapide et prévisible : 0,1 à 0,2 s côté moteur (mesure du 01/11/2026). Attention : l’ordre agit sur la vraie maison.',
    reglages: [{ vue: 'homeassistant', texte: 'Domotique & vocal' }, { vue: 'demo', texte: 'Démo vocale & maison' }],
  },
  {
    id: 'p-question', etape: 'chemins', titre: 'Question sur la maison', court: '« Quelle heure est-il ? »', icone: Route,
    quoi: 'Le moteur lit l’état d’un appareil dans Home Assistant, ou l’heure, la date, la météo, et formule la réponse avec des règles. Le code prévoit qu’une question ne soit jamais prise pour un ordre.',
    utile: 'Réponse en quelques dizaines de millisecondes (4 à 143 ms, mesure du 01/11/2026), sans modèle : donc gratuite.',
    reglages: [{ vue: 'homeassistant', texte: 'Domotique & vocal' }],
  },
  {
    id: 'p-discussion', etape: 'chemins', titre: 'Discussion avec un modèle', court: '« Raconte-moi une blague »', icone: MessageSquare,
    quoi: 'Quand la phrase n’est ni un ordre ni une lecture d’état, un [[llm|modèle de langage]] répond. Le moteur parcourt sa [[cascade]] de modèles et garde les cinq derniers tours de la conversation vocale. L’agenda et le web passent par des spécialistes.',
    utile: 'C’est le seul chemin vocal qui consomme un modèle (environ 3 s en médiane, mesure du 01/11/2026). Le modèle ne peut pas commander la maison.',
    reglages: [{ vue: 'llm', texte: 'Modèles & routage' }, { vue: 'config', texte: 'Configuration' }],
  },
  {
    id: 'p-complexe', etape: 'chemins', titre: 'Tâche complexe', court: 'Planner → plan → Reviewer', icone: Workflow,
    quoi: 'Pour un travail en plusieurs étapes, le [[planner|Planner]] découpe la demande en [[dag|plan]], des [[agent|agents]] exécutent les étapes, le [[reviewer|Reviewer]] relit le résultat, et une [[hitl|validation humaine]] peut être demandée avant une action sensible. La voix n’atteint jamais ce chemin.',
    utile: 'Donne des résultats plus fiables sur les travaux longs, grâce à la relecture automatique et à l’accord humain avant d’agir.',
    reglages: [{ vue: 'taskrunner', texte: 'Lancer une tâche' }, { vue: 'agents', texte: 'Agents' }, { vue: 'workflow', texte: 'Workflows' }],
  },
  {
    id: 'cascade', etape: 'cascade', titre: 'Cascade de modèles', court: 'Le premier qui répond gagne', icone: Layers,
    quoi: 'Une liste ordonnée de modèles : le moteur essaie le premier, puis passe au suivant en cas de panne, de [[quota]] épuisé ou de [[circuit-breaker|circuit ouvert]]. L’ordre va du moins cher au plus cher : local, gratuit, forfait déjà payé, puis API facturée.',
    utile: 'Une panne de fournisseur ne bloque pas le moteur, et l’ordre de la liste est votre premier levier d’économie.',
    reglages: [{ vue: 'config', texte: 'Configuration (composition des niveaux)' }, { vue: 'llm', texte: 'Modèles & routage' }, { vue: 'accounts', texte: 'Comptes & coûts' }],
  },
  {
    id: 'act-ha', etape: 'actions', titre: 'Home Assistant', court: 'Allumer, lire, régler', icone: House,
    quoi: 'Le moteur parle à Home Assistant avec son propre accès : il commande des appareils et lit des états. Ce qu’il peut commander est fixé par son catalogue de commandes, pas par la liste « Exposer » de Home Assistant.',
    utile: 'Un seul point d’action sur la maison, avec des garde-fous. Rappel : il s’agit de la vraie maison, même pendant un essai.',
    reglages: [{ vue: 'homeassistant', texte: 'Domotique & vocal' }, { vue: 'demo', texte: 'Démo vocale & maison' }],
  },
  {
    id: 'act-outils', etape: 'actions', titre: 'Outils', court: 'Lire un état, chercher, MCP', icone: Wrench,
    quoi: 'Selon la demande, le moteur met des outils à disposition de l’agent : lire l’état de la maison, chercher dans la mémoire, ou utiliser des outils [[mcp|MCP]] externes. Des règles d’activation décident quels outils sont proposés.',
    utile: 'Les outils donnent aux modèles des faits vérifiables plutôt que des suppositions ; n’en activer que quelques-uns évite d’encombrer les modèles.',
    reglages: [{ vue: 'mcp', texte: 'Outils MCP' }],
  },
  {
    id: 'act-memoire', etape: 'actions', titre: 'Mémoire', court: 'Souvenirs et recherche', icone: Brain,
    quoi: 'Le moteur garde des souvenirs (faits, historique des conversations) et sait retrouver ceux qui comptent grâce à une recherche par le sens ([[rag|RAG]]).',
    utile: 'Il évite de tout réexpliquer à chaque demande et permet de réutiliser ce qui a déjà été appris. La mémoire fait partie de ce qui est sauvegardé.',
    reglages: [{ vue: 'sauvegardes', texte: 'Sauvegardes' }, { vue: 'config', texte: 'Configuration' }],
  },
  {
    id: 'traces', etape: 'traces', titre: 'Traces et coûts', court: 'Qui a répondu, combien ça a coûté', icone: ScrollText,
    quoi: 'Chaque demande laisse une trace : chemin choisi, modèle utilisé, durée, [[token|tokens]] et coût. Pour la voix, un journal d’audit garde le texte reconnu, le chemin pris et la durée côté moteur.',
    utile: 'C’est ce qui permet de vérifier après coup ce qui s’est passé, et de repérer le poste de dépense.',
    reglages: [{ vue: 'observability', texte: 'Observabilité' }, { vue: 'accounts', texte: 'Comptes & coûts' }, { vue: 'operations', texte: 'Opérations' }],
  },
];

export const NOEUD_PAR_ID: Record<NoeudId, NoeudDef> = Object.fromEntries(
  NOEUDS.map((n) => [n.id, n]),
) as Record<NoeudId, NoeudDef>;

/* ── Chemin réellement pris, d'après le routing_type du journal d'audit ───── */

export interface CheminPris {
  /** Chemin de la carte, ou `null` si le type de routage n'est pas connu de la carte. */
  chemin: CheminId | null;
  /** Nœuds à mettre en évidence, dans l'ordre du trajet. */
  noeuds: NoeudId[];
  /** Le chemin a échoué (réponse d'excuse, délai dépassé…). */
  echec: boolean;
  libelle: string;
  explication: string;
}

const TRAJET_COMPLEXE = ['routeur', 'p-complexe', 'cascade', 'act-outils', 'act-memoire'] as const;
const TYPES_COMPLEXES = new Set([
  'default', 'standard', 'planner_pour_approbation', 'executor_direct', 'sysadmin_direct',
]);

/**
 * Traduit un `routing_type` en chemin de la carte. L'ordre des tests compte :
 * `executor_direct` contient « direct » mais n'est pas une commande domotique.
 */
export function cheminDepuisRouting(routingType: string | null | undefined, modeSource?: string | null): CheminPris {
  const rt = (routingType ?? '').trim().toLowerCase();
  const base: NoeudId[] = ['in-vocal', 'routeur'];

  if (rt === '') {
    return {
      chemin: null, noeuds: base, echec: false, libelle: 'Chemin non enregistré',
      explication: 'Le journal n’a pas gardé le type de routage de cet échange : la carte ne peut pas dire quel chemin il a pris.',
    };
  }

  if (rt.includes('failure') || rt.includes('blocked')) {
    const discussion = rt.startsWith('discussion_chat') || modeSource === 'chat';
    const chemin: CheminId = discussion ? 'p-discussion' : 'p-commande';
    return {
      chemin, echec: true,
      noeuds: [...base, chemin, ...(discussion ? (['cascade'] as NoeudId[]) : []), 'traces'],
      libelle: discussion ? 'Discussion en échec' : 'Commande non comprise',
      explication: discussion
        ? 'Tous les modèles ont échoué ou le délai de 20 s a été atteint : le moteur a répondu une phrase d’excuse. Voir la cascade et les quotas des fournisseurs.'
        : 'Le mode Domotique n’a reconnu ni commande, ni question, ni verbe d’action : le moteur a répondu qu’il n’avait pas compris. Aucune action n’a été envoyée à la maison.',
    };
  }

  if (TYPES_COMPLEXES.has(rt)) {
    return {
      chemin: 'p-complexe', noeuds: [...TRAJET_COMPLEXE], echec: false, libelle: 'Tâche complexe',
      explication: 'Le routeur a confié la demande à l’orchestration complète : plan, agents, relecture.',
    };
  }

  if (rt.includes('weather') || rt.includes('datetime') || rt.includes('state')) {
    return {
      chemin: 'p-question', noeuds: [...base, 'p-question', 'act-ha', 'traces'], echec: false, libelle: 'Question sur la maison',
      explication: 'Réponse produite par des règles et des lectures d’état (appareil, heure, météo), sans modèle de langage.',
    };
  }

  if (rt.includes('llm_fallback')) {
    return {
      chemin: 'p-commande', noeuds: [...base, 'p-commande', 'cascade', 'act-ha', 'traces'], echec: false,
      libelle: 'Commande interprétée par un modèle léger',
      explication: 'Aucune règle n’a reconnu la phrase ; le mode Domotique a demandé à un modèle léger de la traduire en ordre, puis l’a envoyé à Home Assistant.',
    };
  }

  if (rt.includes('command') || rt.includes('climate') || rt.includes('deterministic') || rt.includes('direct') || rt.includes('fuzzy') || rt.includes('zero_llm') || rt.includes('vocal_fallback')) {
    return {
      chemin: 'p-commande', noeuds: [...base, 'p-commande', 'act-ha', 'traces'], echec: false, libelle: 'Commande de la maison',
      explication: 'La phrase a été reconnue par les règles et le catalogue de commandes, puis envoyée à Home Assistant. Aucun modèle de langage n’a été appelé.',
    };
  }

  if (rt.startsWith('vocal_host_')) {
    return {
      chemin: 'p-discussion', noeuds: [...base, 'p-discussion', 'traces'], echec: false, libelle: 'Spécialiste (agenda ou web)',
      explication: 'Les demandes d’agenda et de recherche web passent par un spécialiste dédié du mode Discussion, avant la cascade de modèles.',
    };
  }

  if (rt.startsWith('discussion_chat') || rt === 'casual_chat' || rt === 'ha_conversational') {
    return {
      chemin: 'p-discussion', noeuds: [...base, 'p-discussion', 'cascade', 'act-memoire', 'traces'], echec: false,
      libelle: 'Discussion avec un modèle',
      explication: 'Ni ordre ni lecture d’état : la cascade de modèles a répondu, avec les derniers tours de la conversation en mémoire.',
    };
  }

  return {
    chemin: null, noeuds: base, echec: false, libelle: `Type de routage « ${routingType} »`,
    explication: 'Ce type de routage n’est pas encore rattaché à un chemin de la carte. L’entrée et le routeur sont mis en évidence, rien de plus.',
  };
}

/** Libellé court d'un chemin, pour les pastilles. */
export const LIBELLE_CHEMIN: Record<CheminId, string> = {
  'p-commande': 'Commande de la maison',
  'p-question': 'Question sur la maison',
  'p-discussion': 'Discussion avec un modèle',
  'p-complexe': 'Tâche complexe',
};
