/**
 * Glossaire partagé : une définition courte par terme technique.
 *
 * Règle d'écriture : une ou deux phrases, sans autre jargon que des termes eux-mêmes
 * définis ici. Le composant `Terme` (components/guide/Terme.tsx) affiche la
 * définition en infobulle partout où un terme apparaît.
 */

export interface TermeDef {
  /** Libellé affiché dans l'infobulle. */
  terme: string;
  definition: string;
  /** Exemple concret (facultatif). */
  exemple?: string;
}

export const GLOSSAIRE = {
  'fast-path': {
    terme: 'Fast-path (raccourci)',
    definition:
      'Chemin court du moteur : des règles reconnaissent les demandes évidentes et y répondent tout de suite, sans planification et sans appeler de modèle de langage (ou avec un seul appel).',
    exemple: '« Allume la lumière du salon » est exécuté sans passer par un modèle.',
  },
  'zero-llm': {
    terme: 'Zéro modèle',
    definition:
      'Réponse produite par des règles et des lectures d’état, sans qu’aucun modèle de langage soit appelé : rapide et gratuite.',
  },
  routeur: {
    terme: 'Routeur',
    definition:
      'Le composant qui regarde une demande et décide quel chemin elle prend : un raccourci sans IA, une discussion avec un modèle ou une tâche complexe.',
  },
  tier: {
    terme: 'Tier (niveau)',
    definition:
      'Niveau de puissance et de coût d’un groupe de modèles : léger, moyen, fort ou automatique. Le moteur demande un niveau, pas un modèle précis.',
    exemple: 'Une tâche simple part au niveau « léger », une analyse difficile au niveau « fort ».',
  },
  cascade: {
    terme: 'Cascade',
    definition:
      'Liste ordonnée de modèles : le moteur essaie le premier ; s’il est indisponible ou en échec, il passe au suivant. L’ordre de la liste est le principal levier d’économie.',
  },
  'circuit-breaker': {
    terme: 'Circuit breaker (disjoncteur)',
    definition:
      'Protection par fournisseur : après plusieurs échecs, le moteur cesse de l’appeler un moment et passe au suivant sans attendre. « Fermé » = normal, « ouvert » = suspendu, « semi-ouvert » = un appel d’essai décide.',
  },
  hitl: {
    terme: 'Validation humaine (HITL)',
    definition:
      'Human in the loop : avant une action sensible, le moteur s’arrête et attend votre accord. Rien de risqué ne part sans votre clic.',
  },
  dag: {
    terme: 'DAG (graphe de tâches)',
    definition:
      'Plan en étapes reliées par leurs dépendances : une étape ne démarre que quand celles dont elle dépend sont finies, et les étapes indépendantes peuvent tourner en même temps.',
  },
  planner: {
    terme: 'Planner',
    definition: 'L’agent qui découpe une demande complexe en plan (un graphe de tâches) avant toute exécution.',
  },
  executor: {
    terme: 'Executor',
    definition:
      'L’agent qui exécute chaque étape du plan. C’est le plus sollicité, donc celui qui pèse le plus sur la facture.',
  },
  reviewer: {
    terme: 'Reviewer',
    definition:
      'L’agent qui relit et note le résultat d’une tâche. Sous le seuil de qualité, la tâche peut être rejouée sur un niveau supérieur.',
  },
  agent: {
    terme: 'Agent',
    definition:
      'Un rôle du moteur avec sa consigne et son modèle : par exemple Planner, Executor, Reviewer ou l’agent Home Assistant. Chaque agent fait une chose et la fait avec le niveau de modèle adapté.',
  },
  'boucle-de-fond': {
    terme: 'Boucle de fond',
    definition:
      'Tâche que le moteur exécute seul, à intervalle régulier, sans que vous le demandiez (surveillance, consolidation de la mémoire…). Chacune a un réglage qui l’active ou la coupe.',
  },
  mcp: {
    terme: 'MCP (Model Context Protocol)',
    definition:
      'Standard ouvert qui permet à une application d’IA (Claude, Cursor) d’utiliser des outils fournis par un autre programme. Le moteur peut offrir ses outils à ces applications, et en utiliser d’autres.',
  },
  rag: {
    terme: 'RAG (recherche augmentée)',
    definition:
      'Avant de répondre, on cherche dans une base de documents ou de souvenirs les passages utiles et on les donne au modèle : il répond avec des faits du moteur plutôt que de mémoire.',
  },
  'sonde-de-sante': {
    terme: 'Sonde de santé',
    definition:
      'Petit appel de test, envoyé régulièrement à un modèle pour savoir s’il répond. Ce n’est pas une vraie demande : elle est comptée à part.',
  },
  'appel-reel': {
    terme: 'Appel réel',
    definition:
      'Une demande envoyée à un modèle parce qu’un utilisateur ou un agent en avait besoin, par opposition aux sondes de santé.',
  },
  fournisseur: {
    terme: 'Fournisseur',
    definition:
      'Un service qui héberge des modèles de langage (par exemple Gemini ou DeepSeek, ou un modèle local) et que le moteur joint avec une clé d’accès.',
  },
  modele: {
    terme: 'Modèle',
    definition: 'Un modèle de langage précis proposé par un fournisseur. Un fournisseur en propose souvent plusieurs.',
  },
  llm: {
    terme: 'Modèle de langage (LLM)',
    definition: 'Programme d’IA qui comprend et produit du texte (Gemini, DeepSeek, Claude…).',
  },
  token: {
    terme: 'Token',
    definition:
      'Morceau de texte de quelques caractères : les fournisseurs facturent et limitent leur travail en tokens.',
  },
  quota: {
    terme: 'Quota',
    definition: 'Plafond d’usage fixé par un fournisseur (requêtes par jour, tokens par heure) au-delà duquel il refuse les demandes.',
  },
  'budget-guard': {
    terme: 'BudgetGuard',
    definition:
      'Garde-fou de dépense : une fois le plafond journalier atteint, le moteur n’envoie plus rien vers un fournisseur payant et se rabat sur le gratuit.',
  },
  'cache-semantique': {
    terme: 'Cache sémantique',
    definition:
      'Mémoire des réponses déjà données : une demande très proche d’une précédente reçoit la réponse déjà produite, sans appel de modèle.',
  },
  mediane: {
    terme: 'Latence médiane',
    definition:
      'Durée au-dessous de laquelle se trouve la moitié des échanges. Plus parlante qu’une moyenne, qu’un seul cas très lent suffit à fausser.',
  },
  stt: {
    terme: 'STT (reconnaissance vocale)',
    definition: 'Speech to text : transforme la voix en texte. C’est le maillon le plus fragile du trajet vocal.',
  },
  tts: {
    terme: 'TTS (synthèse vocale)',
    definition: 'Text to speech : transforme un texte en voix.',
  },
  'pipeline-assist': {
    terme: 'Pipeline Assist',
    definition:
      'Chaîne de Home Assistant : reconnaissance vocale (STT), agent de conversation, puis synthèse vocale (TTS). On en choisit un par écran.',
  },
  'agent-de-conversation': {
    terme: 'Agent de conversation',
    definition:
      'Le composant de Home Assistant qui répond au texte reconnu : un agent natif, Gemini, ou ici « moteur_ia_conversation », qui confie la phrase au moteur.',
  },
  'satellite-vocal': {
    terme: 'Satellite vocal',
    definition: 'Appareil (ici l’écran Tab5) qui écoute, envoie l’audio à Home Assistant et joue la réponse.',
  },
  'mot-de-reveil': {
    terme: 'Mot de réveil',
    definition:
      'Phrase qui déclenche l’écoute (« Okay Nabu »). Elle est reconnue sur l’appareil lui-même : aucun son ne part vers Internet avant.',
  },
  ha: {
    terme: 'Home Assistant',
    definition: 'Le logiciel de domotique de la maison. Le moteur lui parle par son interface de programmation (API).',
  },
  api: {
    terme: 'API',
    definition: 'Interface de programmation : les adresses que les programmes (ou cette interface) appellent pour demander une information ou une action au moteur.',
  },
  integrite: {
    terme: 'Intégrité',
    definition: 'Contrôle qu’un fichier sauvegardé est lisible et complet, donc qu’on pourra s’en servir en cas de besoin.',
  },
  retention: {
    terme: 'Rétention',
    definition: 'Règle qui fixe combien de temps ou combien de sauvegardes sont conservées.',
  },
} as const satisfies Record<string, TermeDef>;

export type TermeId = keyof typeof GLOSSAIRE;

export function definitionDe(id: TermeId): TermeDef {
  return GLOSSAIRE[id];
}
