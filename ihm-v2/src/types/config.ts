/**
 * Registre déclaratif des paramètres éditables de config.json.
 *
 * C'est le cœur « paramétrable » de l'IHM : ajouter un réglage = ajouter une
 * entrée ici (chemin pointé + type de contrôle + aide + effet). Chaque champ a
 * été vérifié contre le code qui le lit réellement — le `impact` décrit ce que
 * le moteur fait de la valeur, pas ce qu'on aimerait qu'il en fasse.
 *
 * Couverture : l'ancien registre exposait 17 champs sur ~45 réellement présents
 * dans config.json. Les 11 réglages `auditor_*`, les listes de tiers, la
 * politique de routage et 4 des 5 plafonds `budget_guard` étaient inaccessibles
 * depuis l'IHM — il fallait éditer config.json à la main.
 *
 * NB : seuls les paramètres de config.json sont éditables (POST /api/config en
 * merge). Les secrets .env (clés API, tokens) sont en lecture seule (présence
 * via GET /api/keys) — aucun endpoint backend ne les écrit.
 */

export type FieldType = "text" | "number" | "toggle" | "time" | "select" | "list";

export interface ConfigFieldOption {
  value: string | number;
  label: string;
}

export interface ConfigField {
  /** Chemin pointé dans config.json (ex: "persistent_agents.daemon_enabled"). */
  path: string;
  label: string;
  type: FieldType;
  /** À quoi sert le réglage. */
  help: string;
  /** Ce qui change concrètement dans le comportement du moteur. */
  impact?: string;
  /** Valeur par défaut côté moteur si la clé est absente de config.json. */
  fallback?: string | number | boolean | string[];
  min?: number;
  max?: number;
  step?: number;
  placeholder?: string;
  /** Pour type "select". */
  options?: ConfigFieldOption[];
  /** Pour type "list" : l'ordre des entrées est signifiant. */
  ordered?: boolean;
}

export interface ConfigSection {
  id: string;
  title: string;
  description?: string;
  /** Regroupement de haut niveau, pour la navigation latérale de la vue. */
  group: "routage" | "budgets" | "autonomie" | "performance";
  fields: ConfigField[];
}

const TIER_OPTIONS: ConfigFieldOption[] = [
  { value: "leger", label: "leger — rapide et gratuit" },
  { value: "moyen", label: "moyen — équilibré" },
  { value: "fort", label: "fort — raisonnement complexe" },
  { value: "automatique", label: "automatique — cascade complète" },
];

export const CONFIG_SECTIONS: ConfigSection[] = [
  /* ── Routage ───────────────────────────────────────────────────────────── */
  {
    id: "models",
    title: "Modèles par agent",
    group: "routage",
    description:
      "Chaque agent peut recevoir un modèle précis (ex: deepseek-reasoner) ou un tier (leger/moyen/fort). Un tier laisse la cascade choisir ; un modèle précis la court-circuite.",
    fields: [
      {
        path: "planner_model",
        label: "Modèle du Planner",
        type: "text",
        help: "Modèle ou tier utilisé par le PlannerAgent pour décomposer une tâche en DAG.",
        impact: "Un planner faible produit des plans incohérents, ce qui coûte plus cher en aval qu'un planner solide.",
        placeholder: "ex: deepseek-reasoner ou fort",
      },
      {
        path: "executor_model",
        label: "Modèle de l'Executor",
        type: "text",
        help: "Modèle ou tier utilisé par l'ExecutorAgent pour exécuter chaque étape.",
        impact: "C'est l'agent le plus sollicité : son modèle domine la facture et la latence globale.",
        placeholder: "ex: gemini-3.5-flash-free ou leger",
      },
      {
        path: "ha_model",
        label: "Modèle Home Assistant",
        type: "text",
        help: "Modèle ou tier de l'agent qui traduit une demande en appel de service HA.",
        impact: "Utilisé par l'assistant vocal : un tier leger réduit nettement le délai de réponse perçu.",
        placeholder: "ex: moyen",
      },
      {
        path: "antigravity_model",
        label: "Modèle Antigravity",
        type: "text",
        help: "Modèle utilisé par l'AntigravityAgent (intégration IDE).",
        placeholder: "ex: gemini-3.1-pro-preview-paid",
      },
      {
        path: "persistent_agents.routines_model",
        label: "Modèle des routines",
        type: "select",
        help: "Tier utilisé par les routines de fond (préparation d'articles, veille).",
        options: TIER_OPTIONS,
        fallback: "fort",
      },
    ],
  },
  {
    id: "tiers",
    title: "Composition des tiers",
    group: "routage",
    description:
      "L'ordre compte : le moteur essaie les modèles de haut en bas et s'arrête au premier qui répond. Mettre un modèle gratuit en tête d'un tier très sollicité est le levier d'économie le plus direct.",
    fields: [
      {
        path: "tiers.leger",
        label: "Tier « leger »",
        type: "list",
        ordered: true,
        help: "Modèles essayés pour les tâches simples et les réponses vocales.",
        impact: "Le premier modèle disponible de la liste traite la requête.",
        placeholder: "id du modèle…",
      },
      {
        path: "tiers.moyen",
        label: "Tier « moyen »",
        type: "list",
        ordered: true,
        help: "Modèles pour les tâches standard.",
        placeholder: "id du modèle…",
      },
      {
        path: "tiers.fort",
        label: "Tier « fort »",
        type: "list",
        ordered: true,
        help: "Modèles pour le raisonnement complexe et l'escalade qualité.",
        impact: "Ce sont généralement les modèles payants : leur ordre pilote directement la dépense.",
        placeholder: "id du modèle…",
      },
      {
        path: "tiers.automatique",
        label: "Tier « automatique »",
        type: "list",
        ordered: true,
        help: "Cascade complète utilisée quand aucun tier n'est imposé.",
        placeholder: "id du modèle…",
      },
    ],
  },
  {
    id: "routing_policy",
    title: "Politique de routage",
    group: "routage",
    description:
      "Garde-fous appliqués quelle que soit la composition des tiers, y compris aux modèles venant de models_registry.db.",
    fields: [
      {
        path: "routing_policy.excluded_models",
        label: "Modèles exclus du routage automatique",
        type: "list",
        help: "Ces modèles ne seront jamais choisis automatiquement, même s'ils figurent dans un tier ou dans le catalogue.",
        impact:
          "Filtre appliqué par core/llm_gateway.py::_resolve_tier_models. Le modèle reste appelable explicitement (query_llm_direct). Sert à empêcher l'auto-escalade vers un modèle très coûteux.",
        placeholder: "ex: claude-fable-5",
      },
    ],
  },
  {
    id: "escalation",
    title: "Escalade qualité",
    group: "routage",
    description:
      "Après revue, une réponse jugée trop faible peut être rejouée sur un tier supérieur — une seule fois.",
    fields: [
      {
        path: "cascade_quality_escalation.enabled",
        label: "Activer l'escalade qualité",
        type: "toggle",
        help: "Rejoue la tâche sur un tier plus puissant si le score de qualité est sous le seuil.",
        impact: "Améliore le résultat, mais double le coût de la tâche concernée.",
        fallback: true,
      },
      {
        path: "cascade_quality_escalation.quality_threshold",
        label: "Seuil de qualité",
        type: "number",
        help: "Score en dessous duquel la tâche est relancée (échelle 0–10).",
        impact: "Plus le seuil est haut, plus l'escalade se déclenche souvent — et plus la dépense monte.",
        fallback: 6.0,
        min: 0,
        max: 10,
        step: 0.5,
      },
      {
        path: "cascade_quality_escalation.escalated_tier",
        label: "Tier d'escalade",
        type: "select",
        help: "Tier utilisé pour la seconde tentative.",
        options: TIER_OPTIONS,
        fallback: "fort",
      },
      {
        path: "cascade_quality_escalation.eligible_domains",
        label: "Domaines éligibles",
        type: "list",
        help: "Seules les tâches de ces domaines peuvent être escaladées.",
        impact: "Restreindre cette liste limite les escalades coûteuses aux cas où elles servent vraiment.",
        placeholder: "ex: code_generation",
      },
    ],
  },

  /* ── Budgets ───────────────────────────────────────────────────────────── */
  {
    id: "budgets_session",
    title: "Garde-fous par exécution",
    group: "budgets",
    description: "Limites appliquées à une seule exécution. 0 = désactivé.",
    fields: [
      {
        path: "max_session_tokens",
        label: "Budget max de tokens / session",
        type: "number",
        help: "Nombre maximum de tokens qu'une exécution peut consommer avant arrêt.",
        impact: "Empêche une boucle d'agents de consommer sans limite.",
        fallback: 500000,
        min: 0,
        step: 1000,
      },
      {
        path: "max_execution_seconds",
        label: "Durée max d'une session (s)",
        type: "number",
        help: "Durée maximale d'une exécution avant arrêt automatique.",
        impact: "Protège contre un agent bloqué sur un outil qui ne rend jamais la main.",
        fallback: 0,
        min: 0,
      },
      {
        path: "max_execution_cost_usd",
        label: "Coût max / session (USD)",
        type: "number",
        help: "Plafond de coût d'une exécution.",
        fallback: 0,
        min: 0,
        step: 0.01,
      },
      {
        path: "auto_review",
        label: "Revue automatique post-DAG",
        type: "toggle",
        help: "Le ReviewerAgent analyse le résultat et peut demander des corrections.",
        impact: "Améliore la fiabilité, ajoute un appel LLM par exécution.",
        fallback: true,
      },
      {
        path: "hitl.interactive_auto_approve",
        label: "Auto-approbation des plans à risque",
        type: "toggle",
        help: "Approuve automatiquement les plans jugés risqués en session interactive.",
        impact: "⚠ Supprime le point de contrôle humain avant les actions sensibles. À laisser désactivé.",
        fallback: false,
      },
    ],
  },
  {
    id: "budget_guard",
    title: "Plafonds globaux (BudgetGuard)",
    group: "budgets",
    description:
      "Vérifiés avant chaque décision de cascade : ils déterminent si le moteur a encore le droit d'appeler un provider payant.",
    fields: [
      {
        path: "budget_guard.daily_budget_usd",
        label: "Budget journalier (USD)",
        type: "number",
        help: "Plafond de dépense journalier pour les API payantes.",
        impact: "Atteint, la cascade cesse de router vers les providers payants et se rabat sur le gratuit.",
        fallback: 0.5,
        min: 0,
        step: 0.05,
      },
      {
        path: "budget_guard.total_daily_budget_usd",
        label: "Budget journalier total (USD)",
        type: "number",
        help: "Plafond couvrant l'ensemble des appels, tous providers confondus.",
        fallback: 1.0,
        min: 0,
        step: 0.05,
      },
      {
        path: "budget_guard.gemini_free_tokens_per_hour",
        label: "Quota Gemini gratuit (tokens/h)",
        type: "number",
        help: "Volume horaire au-delà duquel le moteur considère le Free Tier Gemini épuisé.",
        impact: "Sert à anticiper le 429 plutôt qu'à le subir : la cascade bascule avant d'être rejetée.",
        fallback: 1000000,
        min: 0,
        step: 10000,
      },
      {
        path: "budget_guard.deepseek_free_requests_per_day",
        label: "Quota DeepSeek gratuit (req/jour)",
        type: "number",
        help: "Nombre de requêtes quotidiennes avant de considérer le palier gratuit DeepSeek épuisé.",
        fallback: 200,
        min: 0,
      },
      {
        path: "budget_guard.auditor_weekly_budget_usd",
        label: "Budget hebdo de l'Auditeur (USD)",
        type: "number",
        help: "Plafond dédié aux passages de l'agent auditeur.",
        impact: "L'auditeur analyse de gros volumes de code : ce plafond l'empêche de dériver.",
        fallback: 1.0,
        min: 0,
        step: 0.1,
      },
    ],
  },

  /* ── Autonomie ─────────────────────────────────────────────────────────── */
  {
    id: "daemon",
    title: "Daemon Sentinelle",
    group: "autonomie",
    description: "Agent de fond qui surveille en continu la santé du système (Git, Home Assistant, services).",
    fields: [
      {
        path: "persistent_agents.daemon_enabled",
        label: "Activer le Daemon",
        type: "toggle",
        help: "Boucle de surveillance permanente.",
        impact: "Consomme un appel LLM par cycle : l'intervalle pilote directement ce coût récurrent.",
        fallback: true,
      },
      {
        path: "persistent_agents.daemon_interval_minutes",
        label: "Intervalle du Daemon (min)",
        type: "number",
        help: "Fréquence du cycle de vérification.",
        impact: "10 min ≈ 144 cycles/jour. Doubler l'intervalle divise par deux le coût du daemon.",
        fallback: 10,
        min: 1,
      },
      {
        path: "persistent_agents.daemon_model",
        label: "Tier du Daemon",
        type: "select",
        help: "Puissance allouée à chaque cycle de surveillance.",
        impact: "Un tier leger suffit pour de la surveillance : inutile de payer du raisonnement ici.",
        options: TIER_OPTIONS,
        fallback: "leger",
      },
    ],
  },
  {
    id: "dreamer",
    title: "Dreamer & DreamCoder",
    group: "autonomie",
    description:
      "Le Dreamer consolide la mémoire la nuit. Le DreamCoder puise dans le backlog et code de façon autonome sur une branche Git.",
    fields: [
      {
        path: "persistent_agents.dreamer_enabled",
        label: "Activer le Dreamer",
        type: "toggle",
        help: "Consolidation mémoire nocturne et traitement du backlog.",
        fallback: true,
      },
      {
        path: "persistent_agents.dreamer_schedule",
        label: "Heure de déclenchement",
        type: "time",
        help: "Heure du cycle nocturne (HH:MM).",
        fallback: "02:00",
      },
      {
        path: "persistent_agents.dreamer_model",
        label: "Tier du Dreamer",
        type: "select",
        help: "Puissance allouée à la consolidation mémoire.",
        options: TIER_OPTIONS,
        fallback: "leger",
      },
      {
        path: "persistent_agents.dreamer_idle_trigger_hours",
        label: "Déclenchement sur inactivité (h)",
        type: "number",
        help: "Heures sans activité au-delà desquelles le Dreamer se lance aussi, hors horaire.",
        impact: "0 désactive ce déclencheur ; seul l'horaire nocturne reste actif.",
        fallback: 3,
        min: 0,
      },
      {
        path: "persistent_agents.dreamcoder_max_cycle_minutes",
        label: "Durée max d'un cycle DreamCoder (min)",
        type: "number",
        help: "Borne la durée d'un cycle de traitement du backlog.",
        impact: "Empêche un cycle de déborder sur la journée.",
        fallback: 30,
        min: 1,
      },
      {
        path: "persistent_agents.dreamcoder_repo_path",
        label: "Dépôt Git du DreamCoder",
        type: "text",
        help: "Chemin du dépôt dans lequel le DreamCoder crée ses branches. Vide = dépôt du moteur.",
        impact: "⚠ Le DreamCoder écrit réellement du code et crée des branches dans ce dépôt.",
        placeholder: "/chemin/vers/le/depot",
      },
    ],
  },
  {
    id: "auditor",
    title: "Agent Auditeur",
    group: "autonomie",
    description:
      "Audite périodiquement le code du moteur et remonte ses constats. Désactivé par défaut — activation explicite requise.",
    fields: [
      {
        path: "persistent_agents.auditor_enabled",
        label: "Activer l'Auditeur",
        type: "toggle",
        help: "Analyse autonome du code, par lots.",
        impact: "Lit de gros volumes de code : surveillez le budget hebdomadaire dédié.",
        fallback: false,
      },
      {
        path: "persistent_agents.auditor_trigger_mode",
        label: "Mode de déclenchement",
        type: "select",
        help: "Ce qui lance un passage d'audit.",
        impact:
          "« task_count » déclenche après N tâches DreamCoder terminées ; « schedule » à un créneau hebdomadaire fixe ; « both » à la première des deux conditions.",
        options: [
          { value: "task_count", label: "task_count — après N tâches terminées" },
          { value: "schedule", label: "schedule — créneau hebdomadaire" },
          { value: "both", label: "both — l'un ou l'autre" },
        ],
        fallback: "task_count",
      },
      {
        path: "persistent_agents.auditor_trigger_task_count",
        label: "Nombre de tâches déclencheur",
        type: "number",
        help: "Tâches DreamCoder terminées depuis le dernier audit avant d'en relancer un.",
        fallback: 10,
        min: 1,
      },
      {
        path: "persistent_agents.auditor_schedule_weekday",
        label: "Jour de l'audit hebdomadaire",
        type: "select",
        help: "Jour du créneau fixe (modes « schedule » et « both »).",
        options: [
          { value: 0, label: "Lundi" },
          { value: 1, label: "Mardi" },
          { value: 2, label: "Mercredi" },
          { value: 3, label: "Jeudi" },
          { value: 4, label: "Vendredi" },
          { value: 5, label: "Samedi" },
          { value: 6, label: "Dimanche" },
        ],
        fallback: 6,
      },
      {
        path: "persistent_agents.auditor_schedule_time",
        label: "Heure de l'audit",
        type: "time",
        help: "Heure du créneau hebdomadaire (HH:MM).",
        impact: "La boucle vérifie toutes les 15 min : le déclenchement a lieu dans le quart d'heure suivant.",
        fallback: "03:00",
      },
      {
        path: "persistent_agents.auditor_scopes",
        label: "Périmètres audités",
        type: "list",
        help: "Dossiers du moteur soumis à l'audit.",
        impact: "Réduire la liste réduit le corpus analysé, donc le coût de chaque passage.",
        placeholder: "ex: core",
      },
      {
        path: "persistent_agents.auditor_model_tier",
        label: "Tier de l'Auditeur",
        type: "select",
        help: "Puissance utilisée pour l'analyse initiale.",
        options: TIER_OPTIONS,
        fallback: "moyen",
      },
      {
        path: "persistent_agents.auditor_escalation_tier",
        label: "Tier d'escalade de l'Auditeur",
        type: "select",
        help: "Tier utilisé quand un constat mérite une analyse approfondie.",
        options: TIER_OPTIONS,
        fallback: "fort",
      },
      {
        path: "persistent_agents.auditor_confidence_threshold",
        label: "Seuil de confiance",
        type: "number",
        help: "Confiance minimale (0–10) pour qu'un constat soit retenu.",
        impact: "Monter le seuil réduit le bruit, au risque de manquer de vrais problèmes.",
        fallback: 6.0,
        min: 0,
        max: 10,
        step: 0.5,
      },
      {
        path: "persistent_agents.auditor_dedup_similarity_threshold",
        label: "Seuil de déduplication",
        type: "number",
        help: "Similarité (0–1) au-delà de laquelle deux constats sont considérés identiques.",
        fallback: 0.75,
        min: 0,
        max: 1,
        step: 0.05,
      },
      {
        path: "persistent_agents.auditor_max_corpus_chars",
        label: "Taille max du corpus (caractères)",
        type: "number",
        help: "Volume de code maximal envoyé au modèle en une passe.",
        impact: "Borne directement le coût en tokens d'entrée de chaque audit.",
        fallback: 150000,
        min: 1000,
        step: 10000,
      },
    ],
  },

  /* ── Performance ───────────────────────────────────────────────────────── */
  {
    id: "cache",
    title: "Cache sémantique",
    group: "performance",
    description:
      "Compare la requête entrante à l'historique par embeddings. Au-dessus du seuil, la réponse en cache est renvoyée sans appel LLM.",
    fields: [
      {
        path: "semantic_cache.enabled",
        label: "Activer le cache sémantique",
        type: "toggle",
        help: "Réutilise les réponses pour des requêtes sémantiquement proches.",
        impact:
          "Réponse instantanée et coût nul sur les redites — précieux pour les commandes vocales répétitives.",
        fallback: false,
      },
      {
        path: "semantic_cache.similarity_threshold",
        label: "Seuil de similarité",
        type: "number",
        help: "Similarité minimale (0–1) pour réutiliser une réponse en cache.",
        impact:
          "Trop bas, le moteur ressort une réponse hors sujet. 0,95 est un compromis prudent.",
        fallback: 0.95,
        min: 0,
        max: 1,
        step: 0.01,
      },
    ],
  },
];

/** Groupes de sections, pour la navigation de la vue Configuration. */
export const CONFIG_GROUPS: Array<{ id: ConfigSection["group"]; label: string; description: string }> = [
  { id: "routage", label: "Routage & modèles", description: "Qui traite quoi, et dans quel ordre." },
  { id: "budgets", label: "Budgets & garde-fous", description: "Ce que le moteur a le droit de dépenser." },
  { id: "autonomie", label: "Agents autonomes", description: "Ce que le moteur fait sans vous." },
  { id: "performance", label: "Performance", description: "Latence et réutilisation." },
];

/** Liste des secrets .env affichés (lecture seule) — alignée sur GET /api/keys. */
export const SECRET_LABELS: Record<string, string> = {
  GEMINI_API_KEY: "Gemini (Free Tier)",
  GEMINI_PAYANT_API_KEY: "Gemini (payant / GCP)",
  DEEPSEEK_API_KEY: "DeepSeek",
  ANTHROPIC_API_KEY: "Anthropic",
  OPENROUTER_API_KEY: "OpenRouter",
  MISTRAL_API_KEY: "Mistral",
  GROQ_API_KEY: "Groq",
  COHERE_API_KEY: "Cohere",
  XAI_API_KEY: "xAI (Grok)",
  MINIMAX_API_KEY: "MiniMax",
  DEEPINFRA_API_KEY: "DeepInfra",
  ZHIPU_API_KEY: "Zhipu / GLM",
  GITHUB_TOKEN: "GitHub Models",
  HASS_TOKEN: "Home Assistant (token)",
  MOTEUR_API_KEY: "Clé API du moteur",
};
