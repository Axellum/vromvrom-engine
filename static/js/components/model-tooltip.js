/* ============================================================
   MODEL-TOOLTIP.JS — MODEL_CATALOG : Base de données centralisée
   des 19+ modèles avec benchmarks, pricing, profil cognitif.
   Source de vérité unique pour tout le HMI.
   ============================================================ */

// ─── Canaux d'accès (channels) ────────────────────────────────
const CHANNELS = {
    "local":            { label: "Serveur Local",       icon: "🖥️", color: "var(--color-local)",     costTag: "GRATUIT",       confidential: true  },
    "free-api":         { label: "Gemini Free (AI Studio)", icon: "🆓", color: "var(--success)",     costTag: "GRATUIT",       confidential: false },
    "cli-claude-pro":   { label: "Forfait Claude Pro",  icon: "💻", color: "var(--accent-primary)",  costTag: "20 $/mois",     confidential: true  },
    "cli-gemini-adv":   { label: "Forfait Gemini Adv.", icon: "💻", color: "var(--accent-secondary)",costTag: "19.99 $/mois",  confidential: true  },
    "paid-deepseek":    { label: "DeepSeek API",        icon: "🐋", color: "var(--color-deepseek)",  costTag: "Pay-as-you-go", confidential: false },
    "paid-gcp":         { label: "Gemini Payant (GCP)", icon: "💳", color: "var(--warning)",         costTag: "Pay-as-you-go", confidential: true  },
    "media":            { label: "Génération Médias",   icon: "🎨", color: "var(--accent-primary)",  costTag: "Pay-as-you-go", confidential: true  }
};

// ─── Catalogue complet des modèles ────────────────────────────
const MODEL_CATALOG = {

    // ═══════════════════════════════════════════════════════════
    // FAMILLE ANTHROPIC (Claude)
    // ═══════════════════════════════════════════════════════════

    // ─── Claude Opus 4.8 — Modèle le plus récent (2026) ───────
    "claude-opus-4-8": {
        title: "Claude Opus 4.8", family: "anthropic", icon: "🧠",
        channel: "cli-claude-pro",
        aliases: [],
        benchmarks: {
            "SWE-bench":    { score: 92.1, unit: "%", rank: "🥇", desc: "Résolution de bugs GitHub réels (SOTA 2026)" },
            "GPQA Diamond": { score: 96.4, unit: "%", rank: "🥇", desc: "Raisonnement scientifique expert" },
            "HumanEval":    { score: 99.0, unit: "%", rank: "🥇", desc: "Génération de code Python" }
        },
        perf: {
            ttft: "0.40–0.50s", throughput: "40–55 tok/s",
            contextWindow: 1000000, effectiveContext: 1000000
        },
        pricing: {
            type: "subscription", inputPerM: 5.00, outputPerM: 25.00,
            amortizedPerM: 0.57, currency: "USD",
            note: "Inclus dans Claude Pro 20$/mois (CLI) — SOTA 2026"
        },
        strengths: ["architecture", "debugging", "agents", "refactoring", "long-context", "coding-SOTA"],
        weaknesses: ["lent sur sorties volumineuses", "coûteux en API directe"],
        bestFor: "Tâches agents ultra-complexes, refactoring massif, bugfixes insaisissables, SOTA codage",
        hallucination_risk: "très faible",
        confidential: true,
        desc: "Modèle Anthropic le plus récent et le plus puissant (2026). SWE-bench 92.1% — meilleur score mondial. Capacités d'agent autonome de niveau 3.",
        diff: "SWE-bench 92.1% 🥇 (2026) · GPQA 96.4% 🥇 · Contexte 1M tokens · Dépasse Opus 4.7 sur toutes les tâches de codage.",
        usage: "Utiliser comme modèle Expert (antigravity_model) pour les tâches les plus complexes : architecture, bugs critiques, agents autonomes.",
        cognitiveProfile: "Raisonnement scientifique de très haute précision. Ne devine pas — demande si quelque chose est ambigu. Mémoire d'attention maximale sur 1M tokens. Idéal pour les workflows multi-étapes avec outils."
    },

    "claude-opus-4-7": {
        title: "Claude Opus 4.7", family: "anthropic", icon: "🧠",
        channel: "cli-claude-pro",
        aliases: ["claude-opus-4.6-thinking-cli", "claude-opus-4-0"],
        benchmarks: {
            "SWE-bench":    { score: 87.6, unit: "%", rank: "🥇", desc: "Résolution de bugs GitHub réels" },
            "GPQA Diamond": { score: 94.2, unit: "%", rank: "🥇", desc: "Raisonnement scientifique/technique" }
        },
        perf: {
            ttft: "0.45–0.55s", throughput: "35–45 tok/s",
            contextWindow: 1000000, effectiveContext: 1000000
        },
        pricing: {
            type: "subscription", inputPerM: 5.00, outputPerM: 25.00,
            amortizedPerM: 0.57, currency: "USD",
            note: "Inclus dans Claude Pro 20$/mois (CLI)"
        },
        strengths: ["architecture", "debugging", "agents", "refactoring", "long-context"],
        weaknesses: ["lent", "littéral strict", "coûteux en API directe"],
        bestFor: "Conception d'architectures, refactoring lourd, agents autonomes, bugs insaisissables",
        hallucination_risk: "très faible",
        confidential: true,
        desc: "Modèle le plus puissant d'Anthropic. Leader mondial pour le codage complexe et les tâches agentiques.",
        diff: "SWE-bench 87.6% 🥇 · GPQA 94.2% 🥇 · Contexte 1M tokens. Inclus dans Claude Pro.",
        usage: "Planification d'architectures, diagnostic de bugs complexes, agents Level 3.",
        cognitiveProfile: "Logique d'une rigueur scientifique extrême et littéralisme absolu. Ne devine pas le non-dit. Détecte les contradictions et refuse les requêtes incohérentes. Rétention d'attention 100% sur 1M tokens."
    },

    "claude-sonnet-4-6": {
        title: "Claude Sonnet 4.6", family: "anthropic", icon: "⚡",
        channel: "cli-claude-pro",
        aliases: ["claude-sonnet-4.6-thinking-cli", "claude-sonnet-4-5"],
        benchmarks: {
            "OSWorld":      { score: 94, unit: "%", rank: "🥇", desc: "Contrôle d'interface & outils" },
            "SWE-bench":    { score: 84.2, unit: "%", rank: "🥈", desc: "Résolution de bugs GitHub" },
            "GPQA Diamond": { score: 91.8, unit: "%", rank: "🥈", desc: "Raisonnement technique" }
        },
        perf: {
            ttft: "0.18–0.25s", throughput: "85–110 tok/s",
            contextWindow: 200000, effectiveContext: 200000
        },
        pricing: {
            type: "subscription", inputPerM: 3.00, outputPerM: 15.00,
            amortizedPerM: 0.57, currency: "USD",
            note: "Inclus dans Claude Pro 20$/mois (CLI/IDE)"
        },
        strengths: ["tool-calling", "code-standard", "pair-programming", "rapidité"],
        weaknesses: ["contexte 200k seulement", "placeholders si prompt vague"],
        bestFor: "Modification de code en direct, pair-programming, exécution d'outils CLI",
        hallucination_risk: "faible",
        confidential: true,
        desc: "Bourreau de travail rapide. Champion mondial pour l'utilisation d'outils et le contrôle système.",
        diff: "OSWorld 94% 🥇 · Débit 85–110 tok/s · Contexte 200k. Inclus dans Claude Pro.",
        usage: "Code standard, refactoring interactif, exécution d'outils, pair-programming.",
        cognitiveProfile: "Pragmatique et ultra-rapide. Adhère très bien aux structures de fichiers complexes. Réactif pour corriger les erreurs après retour du linter."
    },

    "claude-haiku-4-5": {
        title: "Claude Haiku 4.5", family: "anthropic", icon: "🐇",
        channel: "cli-claude-pro",
        aliases: [],
        benchmarks: {
            "GPQA Diamond": { score: 68.0, unit: "%", rank: "", desc: "Raisonnement technique" }
        },
        perf: {
            ttft: "<0.12s", throughput: ">130 tok/s",
            contextWindow: 200000, effectiveContext: 120000
        },
        pricing: {
            type: "subscription", inputPerM: 1.00, outputPerM: 5.00,
            amortizedPerM: 0.57, currency: "USD",
            note: "Inclus dans Claude Pro (utilisé en interne par CLI)"
        },
        strengths: ["ultra-rapide", "classification", "routage", "JSON"],
        weaknesses: ["logique superficielle", "attention dégradée >120k"],
        bestFor: "Classification rapide, routage de requêtes, formatage JSON, filtrage de logs",
        hallucination_risk: "moyen",
        confidential: true,
        desc: "Modèle ultra-rapide pour les micro-tâches et la classification à haut volume.",
        diff: "TTFT <0.12s · Débit >130 tok/s · Contexte 200k. Utilisé en interne par Claude Code.",
        usage: "Classification, routage, extraction de métadonnées rapide.",
        cognitiveProfile: "Logique d'orientation simple. Très discipliné pour le formatage JSON direct mais manque de profondeur pour les bugs algorithmiques."
    },

    // Alias legacy
    "claude": {
        title: "Claude CLI (Code)", family: "anthropic", icon: "💻",
        channel: "cli-claude-pro", aliases: [],
        benchmarks: {},
        perf: { ttft: "~0.3s", throughput: "~80 tok/s", contextWindow: 200000, effectiveContext: 200000 },
        pricing: { type: "subscription", inputPerM: 3.00, outputPerM: 15.00, amortizedPerM: 0.57, currency: "USD", note: "Défaut CLI = Sonnet 4.6" },
        strengths: ["code", "outils", "architecture"], weaknesses: ["quota partagé Pro"],
        bestFor: "Édition et refactorisation de code, conception d'architectures",
        hallucination_risk: "faible", confidential: true,
        desc: "Agent CLI officiel d'Anthropic (défaut = Sonnet 4.6).",
        diff: "Compris dans Claude Pro (quota ~1.5M tokens/h, ~35M/mois). Contexte 200k.",
        usage: "Édition et refactorisation de code, conception d'architectures.",
        cognitiveProfile: "Hérite des capacités de Sonnet 4.6 par défaut."
    },
    "claude-sonnet-4.6-thinking-cli": null,  // → résolu dynamiquement via aliases
    "claude-opus-4.6-thinking-cli": null,    // → résolu dynamiquement via aliases

    // ═══════════════════════════════════════════════════════════
    // FAMILLE GOOGLE (Gemini) — CLI / Abonnement Gemini Advanced
    // ═══════════════════════════════════════════════════════════

    "gemini-3.5-flash-high-cli": {
        title: "Gemini 3.5 Flash High (CLI)", family: "google", icon: "⚡",
        channel: "cli-gemini-adv", aliases: [],
        benchmarks: { "MMLU-Pro": { score: 74.2, unit: "%", rank: "", desc: "Connaissances générales" } },
        perf: { ttft: "0.25–0.35s", throughput: "100–120 tok/s", contextWindow: 1000000, effectiveContext: 800000 },
        pricing: { type: "subscription", inputPerM: 1.50, outputPerM: 9.00, amortizedPerM: 0.20, currency: "USD", note: "Inclus Gemini Advanced (canal haute priorité)" },
        strengths: ["rapidité", "multimodal", "contexte 1M", "thoughts persistés"],
        weaknesses: ["moins rigoureux sur code complexe que Claude"],
        bestFor: "Lecture de gros fichiers, exécution d'outils en parallèle, agents rapides",
        hallucination_risk: "moyen", confidential: true,
        desc: "Modèle rapide via le canal haute priorité d'Antigravity IDE.",
        diff: "Compris dans Gemini Advanced. ~4M tokens/h, ~100M/mois. Contexte 1M.",
        usage: "Lecture de gros fichiers, exécution d'outils en parallèle.",
        cognitiveProfile: "Structure son raisonnement via sa trace de pensée persistée en cache. Très réactif sur les flux multimodaux."
    },

    "gemini-3.5-flash-medium-cli": {
        title: "Gemini 3.5 Flash Medium (CLI)", family: "google", icon: "⚡",
        channel: "cli-gemini-adv", aliases: [],
        benchmarks: {},
        perf: { ttft: "0.25–0.35s", throughput: "100–120 tok/s", contextWindow: 1000000, effectiveContext: 800000 },
        pricing: { type: "subscription", inputPerM: 1.50, outputPerM: 9.00, amortizedPerM: 0.20, currency: "USD", note: "Inclus Gemini Advanced (canal standard)" },
        strengths: ["polyvalent", "rapide", "économique en quota"],
        weaknesses: ["priorité standard = potentiellement plus lent sous charge"],
        bestFor: "Assistance générale, explications de code de taille moyenne",
        hallucination_risk: "moyen", confidential: true,
        desc: "Modèle polyvalent via le canal standard de l'IDE.",
        diff: "Compris dans Gemini Advanced. Quota ~4M tokens/h. Contexte 1M.",
        usage: "Assistance générale, explications de code de taille moyenne.",
        cognitiveProfile: "Identique à Flash High mais sur le canal standard (priorité moindre en cas de congestion)."
    },



    "gemini-cli": {
        title: "Gemini CLI (Antigravity)", family: "google", icon: "💻",
        channel: "cli-gemini-adv", aliases: ["antigravity"],
        benchmarks: {},
        perf: { ttft: "~0.3s", throughput: "~100 tok/s", contextWindow: 1000000, effectiveContext: 800000 },
        pricing: { type: "subscription", inputPerM: 1.50, outputPerM: 9.00, amortizedPerM: 0.20, currency: "USD", note: "Inclus Gemini Advanced" },
        strengths: ["polyvalent", "accès fichiers IDE"], weaknesses: ["dépendant du binaire IDE"],
        bestFor: "Agent d'exécution polyvalent via Antigravity IDE",
        hallucination_risk: "moyen", confidential: true,
        desc: "CLI Antigravity avec accès direct au workspace.",
        diff: "Compris dans Gemini Advanced. Quota ~4M tokens/h.",
        usage: "Agent d'exécution polyvalent.",
        cognitiveProfile: "Agent polyvalent héritant de Gemini Flash via le binaire Antigravity IDE."
    },

    // ═══════════════════════════════════════════════════════════
    // FAMILLE GOOGLE (Gemini) — API Gratuite (AI Studio Free Tier)
    // ═══════════════════════════════════════════════════════════

    "gemini-3.5-flash-free": {
        title: "Gemini 3.5 Flash (Free)", family: "google", icon: "🆓",
        channel: "free-api", aliases: ["gemini-3.5-flash", "gemini-flash", "gemini"],
        benchmarks: { "MMLU-Pro": { score: 74.2, unit: "%", rank: "", desc: "Connaissances générales" } },
        perf: { ttft: "0.25–0.35s", throughput: "100–120 tok/s", contextWindow: 1000000, effectiveContext: 800000 },
        pricing: { type: "free", inputPerM: 0, outputPerM: 0, amortizedPerM: 0, currency: "USD", note: "Gratuit (15 RPM, 1M TPM, 1500 RPD)" },
        quotas: { rpm: 15, tpm: 1000000, rpd: 1500 },
        strengths: ["gratuit", "rapide", "contexte 1M", "thoughts persistés"],
        weaknesses: ["non confidentiel (entraînement)", "quotas stricts", "pas de SLA"],
        bestFor: "Tâches non sensibles, tests de prompts, agents d'exécution économiques",
        hallucination_risk: "moyen", confidential: false,
        desc: "Modèle Flash gratuit via AI Studio. Ultra-rapide et économique.",
        diff: "⚠️ Non confidentiel (données utilisées pour l'entraînement). Gratuit à 100%. Limites : 15 RPM, 1M TPM, 1500 RPD.",
        usage: "Tâches non sensibles, tests rapides de prompts, exécution de routine.",
        cognitiveProfile: "Structure son raisonnement via la trace de pensée. S'auto-corrige bien sur les structures simples mais peut faillir sur les dépendances imbriquées."
    },



    "gemini-3.1-flash-lite-free": {
        title: "Gemini 3.1 Flash Lite (Free)", family: "google", icon: "🆓",
        channel: "free-api", aliases: ["gemini-3.1-flash-lite"],
        benchmarks: {},
        perf: { ttft: "0.12–0.18s", throughput: ">140 tok/s", contextWindow: 1000000, effectiveContext: 250000 },
        pricing: { type: "free", inputPerM: 0, outputPerM: 0, amortizedPerM: 0, currency: "USD", note: "Gratuit" },
        strengths: ["ultra-rapide", "gratuit", "micro-tâches"],
        weaknesses: ["non confidentiel", "attention dégradée >250k", "hallucinations code complexe"],
        bestFor: "Extraction d'entités, traduction, micro-automations",
        hallucination_risk: "élevé sur code", confidential: false,
        desc: "Modèle ultra-léger dédié aux micro-tâches à haut débit.",
        diff: "⚠️ Non confidentiel. Gratuit. >140 tok/s. Dégradation >250k tokens.",
        usage: "Extraction d'entités, traduction rapide, micro-automations.",
        cognitiveProfile: "Logique élémentaire. Sujet aux hallucinations dès que la logique requiert >2 étapes."
    },

    "gemini-2.5-pro-free": {
        title: "Gemini 2.5 Pro (Free)", family: "google", icon: "🆓",
        channel: "free-api", aliases: ["gemini-2.5-pro"],
        benchmarks: {},
        perf: { ttft: "0.80–0.95s", throughput: "30–40 tok/s", contextWindow: 2000000, effectiveContext: 2000000 },
        pricing: { type: "free", inputPerM: 0, outputPerM: 0, amortizedPerM: 0, currency: "USD", note: "Gratuit" },
        strengths: ["gratuit", "contexte 2M", "raisonnement stable"],
        weaknesses: ["non confidentiel", "lent", "génération précédente"],
        bestFor: "RAG sur documentation HA volumineuse, débogage d'intégrations matérielles",
        hallucination_risk: "faible", confidential: false,
        desc: "Modèle Pro 2.5 gratuit. Stable pour l'analyse lourde.",
        diff: "⚠️ Non confidentiel. Gratuit. Contexte 2M tokens. Génération précédente.",
        usage: "RAG documentation HA, débogage intégrations matérielles.",
        cognitiveProfile: "Raisonnement scientifique stable. Moins agile sur le codage temps réel."
    },

    "gemini-2.5-flash-free": {
        title: "Gemini 2.5 Flash (Free)", family: "google", icon: "🆓",
        channel: "free-api", aliases: ["gemini-2.5-flash"],
        benchmarks: {},
        perf: { ttft: "0.30–0.40s", throughput: "75–85 tok/s", contextWindow: 1000000, effectiveContext: 600000 },
        pricing: { type: "free", inputPerM: 0, outputPerM: 0, amortizedPerM: 0, currency: "USD", note: "Gratuit" },
        strengths: ["gratuit", "audio natif", "rapide"],
        weaknesses: ["non confidentiel", "attention dégradée >600k"],
        bestFor: "Assistant vocal local, génération de configs YAML",
        hallucination_risk: "moyen", confidential: false,
        desc: "Flash 2.5 avec support audio natif.",
        diff: "⚠️ Non confidentiel. Gratuit. Audio natif. Contexte 1M.",
        usage: "Requêtes audio directes, génération YAML.",
        cognitiveProfile: "Supporte nativement l'audio en entrée pour décoder le langage parlé."
    },

    "gemini-2.0-flash-tts-free": {
        title: "Gemini 2.0 Flash TTS (Free)", family: "google", icon: "🔊",
        channel: "free-api", aliases: ["gemini-2.0-flash-tts"],
        benchmarks: {},
        perf: { ttft: "~0.25s", throughput: "N/A (audio)", contextWindow: 500000, effectiveContext: 500000 },
        pricing: { type: "free", inputPerM: 0, outputPerM: 0, amortizedPerM: 0, currency: "USD", note: "Gratuit" },
        strengths: ["synthèse vocale native", "latence ultra-basse", "gratuit"],
        weaknesses: ["non confidentiel", "sortie audio uniquement"],
        bestFor: "Synthèse vocale, interaction vocale directe",
        hallucination_risk: "faible", confidential: false,
        desc: "Modèle natif Text-to-Speech et Speech-to-Speech.",
        diff: "⚠️ Non confidentiel. Gratuit. Latence ~250-400ms.",
        usage: "Synthèse vocale de haute qualité, conversation interactive.",
        cognitiveProfile: "Comprend et génère directement de l'audio avec intonation naturelle."
    },

    // ═══════════════════════════════════════════════════════════
    // FAMILLE GOOGLE (Gemini) — API Payante (GCP)
    // ═══════════════════════════════════════════════════════════

    "gemini-3.5-flash-paid": {
        title: "Gemini 3.5 Flash (GCP)", family: "google", icon: "💳",
        channel: "paid-gcp", aliases: [],
        benchmarks: { "MMLU-Pro": { score: 74.2, unit: "%", rank: "", desc: "" } },
        perf: { ttft: "0.25–0.35s", throughput: "100–120 tok/s", contextWindow: 1000000, effectiveContext: 800000 },
        pricing: { type: "payg", inputPerM: 1.282575, outputPerM: 7.69545, amortizedPerM: null, currency: "EUR", note: "Tarifs contractuels GCP (EUR). Cached: 0.128257€/M" },
        strengths: ["confidentiel", "sans limite de quota", "context caching -90%"],
        weaknesses: ["coûteux vs gratuit", "tarif doublé >128k"],
        bestFor: "Production parallélisée massive, données sensibles",
        hallucination_risk: "moyen", confidential: true,
        desc: "Modèle Flash payant via GCP. Confidentiel, sans quota.",
        diff: "🔒 Confidentiel. 1.28€/M in, 7.70€/M out. Context Caching -90%.",
        usage: "Requêtes de production parallélisées massives.",
        cognitiveProfile: "Identique à Flash Free mais sur infrastructure GCP confidentielle."
    },



    // GCP Payants supplémentaires (tarifs contractuels)

    "gemini-2.5-pro-paid": {
        title: "Gemini 2.5 Pro (GCP)", family: "google", icon: "💳",
        channel: "paid-gcp", aliases: [],
        benchmarks: {},
        perf: { ttft: "0.80–0.95s", throughput: "30–40 tok/s", contextWindow: 2000000, effectiveContext: 2000000 },
        pricing: { type: "payg", inputPerM: 1.068812, outputPerM: 8.5505, amortizedPerM: null, currency: "EUR", note: "Tarif contractuel GCP (EUR). Cached: 0.106881€/M" },
        strengths: ["confidentiel", "contexte 2M", "raisonnement stable"],
        weaknesses: ["lent", "cher en sortie", "génération précédente"],
        bestFor: "RAG documentation HA volumineuse, débogage intégrations matérielles",
        hallucination_risk: "faible", confidential: true,
        desc: "Modèle Pro 2.5 payant. Raisonnement scientifique stable, contexte 2M.",
        diff: "🔒 Confidentiel. 1.07€/M in, 8.55€/M out. Contexte 2M tokens.",
        usage: "RAG documentation HA, débogage intégrations matérielles.",
        cognitiveProfile: "Raisonnement logique et scientifique très stable. Moins agile sur le codage temps réel."
    },

    "gemini-2.5-flash-paid": {
        title: "Gemini 2.5 Flash (GCP)", family: "google", icon: "💳",
        channel: "paid-gcp", aliases: [],
        benchmarks: {},
        perf: { ttft: "0.30–0.40s", throughput: "75–85 tok/s", contextWindow: 1000000, effectiveContext: 600000 },
        pricing: { type: "payg", inputPerM: 0.256515, outputPerM: 2.137625, amortizedPerM: null, currency: "EUR", note: "Tarif contractuel GCP (EUR). Audio: 0.855050€/M" },
        strengths: ["confidentiel", "audio natif", "économique"],
        weaknesses: ["attention dégradée >600k"],
        bestFor: "Assistant vocal, génération YAML, traitement audio",
        hallucination_risk: "moyen", confidential: true,
        desc: "Flash 2.5 payant. Support audio natif à tarif avantageux.",
        diff: "🔒 Confidentiel. 0.26€/M in, 2.14€/M out. Audio natif.",
        usage: "Requêtes audio directes, génération YAML.",
        cognitiveProfile: "Supporte nativement l'audio en entrée pour décoder le langage parlé."
    },

    "gemini-2.0-flash-tts-paid": {
        title: "Gemini 2.0 Flash TTS (GCP)", family: "google", icon: "💳",
        channel: "paid-gcp", aliases: [],
        benchmarks: {},
        perf: { ttft: "~0.25s", throughput: "N/A (audio)", contextWindow: 500000, effectiveContext: 500000 },
        pricing: { type: "payg", inputPerM: 0.427525, outputPerM: 8.5505, amortizedPerM: null, currency: "EUR", note: "Tarif contractuel GCP. Sortie audio haute consommation." },
        strengths: ["synthèse vocale native", "confidentiel", "latence ultra-basse"],
        weaknesses: ["sortie audio uniquement", "cher en sortie"],
        bestFor: "Synthèse vocale de haute qualité, conversation interactive",
        hallucination_risk: "faible", confidential: true,
        desc: "Modèle natif Text-to-Speech GCP. Latence ~250-400ms.",
        diff: "🔒 Confidentiel. 0.43€/M in, 8.55€/M out audio. Latence 250-400ms.",
        usage: "Synthèse vocale, interaction vocale pour enceintes intelligentes.",
        cognitiveProfile: "Comprend et génère directement de l'audio avec intonation naturelle et modulations dynamiques."
    },

    "gemini-3.1-flash-lite-paid": {
        title: "Gemini 3.1 Flash Lite Preview (GCP)", family: "google", icon: "💳",
        channel: "paid-gcp", aliases: [],
        benchmarks: {},
        perf: { ttft: "0.12–0.18s", throughput: ">140 tok/s", contextWindow: 1000000, effectiveContext: 250000 },
        pricing: { type: "payg", inputPerM: 0.213762, outputPerM: 1.282575, amortizedPerM: null, currency: "EUR", note: "Tarif contractuel GCP. Preview." },
        strengths: ["confidentiel", "ultra-rapide", "économique"],
        weaknesses: ["preview", "hallucinations code complexe"],
        bestFor: "Extraction d'entités, traduction, micro-automations confidentielles",
        hallucination_risk: "élevé sur code", confidential: true,
        desc: "Flash Lite payant. Ultra-rapide, très économique.",
        diff: "🔒 Confidentiel. 0.21€/M in, 1.28€/M out. >140 tok/s. Preview.",
        usage: "Extraction d'entités, traduction rapide.",
        cognitiveProfile: "Logique élémentaire. Très bon respect des consignes de formatage court."
    },

    "gemini-3-pro-short-paid": {
        title: "Gemini 3 Pro Short (GCP)", family: "google", icon: "💳",
        channel: "paid-gcp", aliases: [],
        benchmarks: {},
        perf: { ttft: "~0.40s", throughput: "~50 tok/s", contextWindow: 2000000, effectiveContext: 2000000 },
        pricing: { type: "payg", inputPerM: 1.7101, outputPerM: 10.2606, amortizedPerM: null, currency: "EUR", note: "Tarif contractuel GCP. Optimisé réponses courtes." },
        strengths: ["confidentiel", "réponses ultra-courtes", "précis"],
        weaknesses: ["très cher en sortie (10.26€/M)"],
        bestFor: "Notifications domotiques concises, alarmes, messages HMI",
        hallucination_risk: "faible", confidential: true,
        desc: "Pro optimisé pour les réponses synthétiques et courtes.",
        diff: "🔒 Confidentiel. 1.71€/M in, 10.26€/M out. Ultra-court.",
        usage: "Génération de notifications domotiques concises, alarmes.",
        cognitiveProfile: "Style ultra-court sans bavardage. Très précis pour les ordres de contrôle direct."
    },

    // ═══════════════════════════════════════════════════════════
    // FAMILLE DEEPSEEK
    // ═══════════════════════════════════════════════════════════

    "deepseek-chat": {
        title: "DeepSeek V4 Flash (Chat)", family: "deepseek", icon: "🐋",
        channel: "paid-deepseek", aliases: ["deepseek", "deepseek-v4-flash"],
        benchmarks: {},
        perf: { ttft: "~0.18s", throughput: "90–100 tok/s", contextWindow: 128000, effectiveContext: 128000 },
        pricing: { type: "payg", inputPerM: 0.14, outputPerM: 0.28, amortizedPerM: null, currency: "USD", note: "Cache hit: 0.0028$/M. Coût plancher du marché." },
        strengths: ["ultra low-cost", "rapide", "bon généraliste"],
        weaknesses: ["non confidentiel (données envoyées en Chine)", "contexte 128k seulement"],
        bestFor: "Tâches secondaires, documentation, résumés, traduction",
        hallucination_risk: "moyen", confidential: false,
        desc: "Modèle généraliste ultra-économique. Coût plancher du marché LLM.",
        diff: "0.14$/M in, 0.28$/M out. Cache: 0.003$/M. Contexte 128k.",
        usage: "Tâches secondaires, documentation, résumés, traduction.",
        cognitiveProfile: "Bon compromis vitesse/coût. Sujet à des régressions d'attention dans les contextes longs."
    },

    "deepseek-reasoner": {
        title: "DeepSeek R1 Reasoner", family: "deepseek", icon: "🧮",
        channel: "paid-deepseek", aliases: ["deepseek-r1"],
        benchmarks: {
            "LiveCodeBench": { score: 93.5, unit: "%", rank: "🥇", desc: "Logique algorithmique" },
            "Codeforces":    { score: 3206, unit: "Elo", rank: "🥇", desc: "Programmation compétitive" },
            "MATH-500":      { score: 97.3, unit: "%", rank: "🥇", desc: "Logique mathématique" }
        },
        perf: { ttft: "2–6s", throughput: "~40 tok/s", contextWindow: 128000, effectiveContext: 128000 },
        pricing: { type: "payg", inputPerM: 0.14, outputPerM: 0.28, amortizedPerM: null, currency: "USD", note: "Redirigé vers deepseek-v4-flash. Même tarif." },
        strengths: ["champion algorithmique 🥇", "CoT visible", "auto-correction", "ultra low-cost"],
        weaknesses: ["TTFT très élevé (2-6s)", "verbeux", "contexte 128k", "non confidentiel"],
        bestFor: "Algorithmes complexes, Jinja2/YAML HA avancés, bugs logiques, mathématiques",
        hallucination_risk: "moyen (mais auto-détecté via CoT)",
        confidential: false,
        desc: "Champion absolu de la logique algorithmique et mathématique. CoT visible.",
        diff: "LiveCodeBench 93.5% 🥇 · Codeforces 3206 Elo 🥇 · MATH 97.3% 🥇. 0.14$/M in.",
        usage: "Planner, découpe de tâches complexes, bugs algorithmiques profonds.",
        cognitiveProfile: "Génère une longue trace de réflexion <think> avant d'écrire sa réponse. Évalue constamment ses propres affirmations, teste des hypothèses, détecte ses erreurs et s'auto-corrige (backtracking)."
    },



    // ═══════════════════════════════════════════════════════════
    // LOCAL (LM Studio)
    // ═══════════════════════════════════════════════════════════

    "local": {
        title: "LM Studio (Qwen 2.5 14B)", family: "local", icon: "🖥️",
        channel: "local", aliases: [],
        benchmarks: {
            "RULER": { score: 57.5, unit: "%", rank: "⚠️", desc: "Efficacité de contexte réel (moyenne 50-65%)" }
        },
        perf: { ttft: "<0.08s", throughput: "35–55 tok/s", contextWindow: 1000000, effectiveContext: 550000 },
        pricing: { type: "local", inputPerM: 0, outputPerM: 0, amortizedPerM: 0, currency: "USD", note: "Coût électricité uniquement. RTX 5070 Ti." },
        strengths: ["gratuit", "confidentiel 100%", "airgapped", "latence réseau 0"],
        weaknesses: ["attention effondrée >550k (RULER)", "raisonnement limité", "VRAM limitée"],
        bestFor: "Résumés de contexte, extraction JSON/Regex, RAG domotique <500k tokens",
        hallucination_risk: "élevé sur code complexe",
        confidential: true,
        desc: "Inférence locale sur RTX 5070 Ti. 100% gratuit et confidentiel.",
        diff: "0.00$/token. RULER 50-65% ⚠️. Airgapped. RTX 5070 Ti + 64 Go RAM.",
        usage: "Résumés de contexte, extraction de données, RAG domotique (<500k tokens).",
        cognitiveProfile: "Confidentialité absolue et latence prévisible. Ses capacités s'effondrent sur les problèmes logiques abstraits hors de ses données d'entraînement. Attention: Lost in the Middle au-delà de 500k tokens."
    },

    // ═══════════════════════════════════════════════════════════
    // OUTILS SPÉCIALISÉS (Recherche, Médias)
    // ═══════════════════════════════════════════════════════════

    "gemini-search-grounding": {
        title: "Gemini Search Grounding", family: "google", icon: "🔍",
        channel: "paid-gcp", aliases: ["google-search-grounding"],
        benchmarks: {},
        perf: { ttft: "+1.0–2.5s", throughput: "N/A (couche)", contextWindow: null, effectiveContext: null },
        pricing: { type: "payg", inputPerM: 0, outputPerM: 0, amortizedPerM: null, currency: "USD", note: "14 $/1 000 requêtes (Gemini 3.x). 5 000 gratuites/mois." },
        strengths: ["données temps réel", "réduit hallucinations temporelles", "sources citées"],
        weaknesses: ["surcoût par requête (14$/1k)", "latence +1-2.5s", "pas de SLA"],
        bestFor: "Prévisions météo locales, actualités domotiques, dernières versions d'APIs",
        hallucination_risk: "très faible (données vérifiées)",
        confidential: true,
        desc: "Recherche Google intégrée au modèle. Injecte des données temps réel.",
        diff: "14$/1k requêtes. 5k gratuites/mois. Latence +1-2.5s. Sources citées.",
        usage: "Veille technique, météo locale exacte, documentation à jour.",
        cognitiveProfile: "Couche d'intégration dynamique qui enrichit le prompt avec des résultats Google Search avant la génération. Signale l'incertitude si résultats contradictoires."
    },

    "imagen-4": {
        title: "Imagen 4", family: "google", icon: "🎨",
        channel: "media", aliases: [],
        benchmarks: {},
        perf: { ttft: "4–8s", throughput: "1 image", contextWindow: null, effectiveContext: null },
        pricing: { type: "payg", inputPerM: 0, outputPerM: 0, amortizedPerM: null, currency: "USD", note: "0.03 $ à 0.12 $ / image selon résolution et passes." },
        strengths: ["texte dans l'image précis", "photoréalisme", "intégration GCP"],
        weaknesses: ["pas d'inpainting avancé"],
        bestFor: "Illustrations HMI, icônes personnalisées, arrière-plans",
        hallucination_risk: "N/A",
        confidential: true,
        desc: "Génération d'images photoréalistes Google. 0.03-0.12$/image.",
        diff: "0.03-0.12$/image. Temps: 4-8s. Texte dans l'image très précis.",
        usage: "Illustrations d'arrière-plan HMI, icônes du tableau de bord.",
        cognitiveProfile: "Diffusion latent propriétaire. Excellente précision pour le texte intégré dans les images."
    },

    "veo-3": {
        title: "Veo 3", family: "google", icon: "🎬",
        channel: "media", aliases: [],
        benchmarks: {},
        perf: { ttft: "plusieurs min", throughput: "1 vidéo", contextWindow: null, effectiveContext: null },
        pricing: { type: "payg", inputPerM: 0, outputPerM: 0, amortizedPerM: null, currency: "USD", note: "0.25 $ à 1.50 $ / seconde de vidéo générée." },
        strengths: ["physique réaliste", "1080p", "cinématique"],
        weaknesses: ["temps de génération élevé", "coûteux pour clips longs", "micro-déformations >5s"],
        bestFor: "Boucles d'animation, effets visuels de veille pour dalles tactiles",
        hallucination_risk: "N/A",
        confidential: true,
        desc: "Génération de vidéos cinématiques 1080p Google. 0.25-1.50$/seconde.",
        diff: "0.25-1.50$/s vidéo. Génération en minutes. Physique réaliste.",
        usage: "Animations d'ambiance haut de gamme, effets visuels de veille.",
        cognitiveProfile: "Modèle de diffusion vidéo temporel. Gère la physique des mouvements de manière réaliste."
    },

    "flux-1.1-pro": {
        title: "Flux 1.1 Pro", family: "bfl", icon: "🖼️",
        channel: "media", aliases: [],
        benchmarks: {},
        perf: { ttft: "2–4s", throughput: "1 image", contextWindow: null, effectiveContext: null },
        pricing: { type: "payg", inputPerM: 0, outputPerM: 0, amortizedPerM: null, currency: "USD", note: "~0.04 $ / image (API Replicate ou BFL)." },
        strengths: ["roi adhérence prompts 🥇", "fidélité anatomique", "texte rendu", "ultra-rapide"],
        weaknesses: ["hors écosystème GCP (Replicate/BFL)"],
        bestFor: "Mockups UI/UX pour HMI, textures photoréalistes, maquettes d'écrans",
        hallucination_risk: "N/A",
        confidential: false,
        desc: "Roi 2026 de l'adhérence aux prompts. 0.04$/image. Ultra-rapide.",
        diff: "~0.04$/image. 2-4s. Meilleure fidélité aux prompts du marché.",
        usage: "Mockups complets d'interfaces UI/UX, textures de visualisation 3D.",
        cognitiveProfile: "Modèle hybride flow-matching/diffusion. Parvient à matérialiser exactement tous les éléments demandés. Gère l'anatomie humaine de manière remarquable."
    }
};

// ─── Résolution d'alias (legacy → canonical) ─────────────────
function resolveModelId(modelId) {
    if (!modelId) return modelId;
    // Accès direct
    if (MODEL_CATALOG[modelId] && MODEL_CATALOG[modelId] !== null) return modelId;
    // Recherche par alias
    for (const [canonId, entry] of Object.entries(MODEL_CATALOG)) {
        if (entry && entry.aliases && entry.aliases.includes(modelId)) return canonId;
    }
    // Tentative de fallback en retirant -free/-paid
    const baseId = modelId.replace("-free", "").replace("-paid", "");
    if (MODEL_CATALOG[baseId] && MODEL_CATALOG[baseId] !== null) return baseId;
    return modelId;
}

function getModelEntry(modelId) {
    const resolved = resolveModelId(modelId);
    return MODEL_CATALOG[resolved] || null;
}

// ─── Liste complète des modèles disponibles (pour checkboxes, etc.) ───
// ATTENTION : Cette liste est la source de vérité statique pour les tiers-config.
// Elle est enrichie dynamiquement par initDynamicModelCatalog() au démarrage.
// Format : { id: <id exact en BDD>, name: <libellé affiché dans les checkboxes> }
const AVAILABLE_MODELS = [
    // ── Abonnements CLI Claude ──────────────────────────────────
    { id: "claude-opus-4-8",          name: "[ABO CLI] Claude Opus 4.8 🥇 (SOTA 2026)" },
    { id: "claude-opus-4-7",          name: "[ABO CLI] Claude Opus 4.7" },
    { id: "claude-sonnet-4-6",        name: "[ABO CLI] Claude Sonnet 4.6" },
    { id: "claude-sonnet-4-5",        name: "[ABO CLI] Claude Sonnet 4.5" },
    { id: "claude-haiku-4-5",         name: "[ABO CLI] Claude Haiku 4.5 (rapide)" },
    { id: "claude-opus-4-5",          name: "[ABO CLI] Claude Opus 4.5" },
    { id: "claude-opus-4-0",          name: "[ABO CLI] Claude Opus 4.0 (legacy)" },
    // ── Abonnements CLI Gemini ──────────────────────────────────
    { id: "gemini-cli",               name: "[ABO CLI] Gemini CLI (défaut)" },
    { id: "gemini-3.5-flash-high-cli",name: "[ABO CLI] Gemini 3.5 Flash (High Priority)" },
    { id: "gemini-3.5-flash-medium-cli", name: "[ABO CLI] Gemini 3.5 Flash (Standard)" },
    // ── Gemini API Gratuit (Free Tier) ──────────────────────────
    { id: "gemini-3.5-flash",         name: "[GRATUIT API] Gemini 3.5 Flash" },
    { id: "gemini-3.1-flash-lite",    name: "[GRATUIT API] Gemini 3.1 Flash Lite" },
    { id: "gemini-2.5-flash",         name: "[GRATUIT API] Gemini 2.5 Flash" },
    // ── Modèles locaux (LM Studio) ──────────────────────────────
    { id: "gemma-4-31b",              name: "[LOCAL] Gemma 4 31B" },
    { id: "gemma-4-26b-a4b",          name: "[LOCAL] Gemma 4 26B MoE (A4B)" },
    { id: "gemma-4-e4b",              name: "[LOCAL] Gemma 4 E4B" },
    { id: "qwen3.6-35b-a3b",          name: "[LOCAL] Qwen 3.6 35B MoE" },
    { id: "qwen3-coder-next",         name: "[LOCAL] Qwen 3 Coder Next" },
    { id: "qwen2.5-coder-32b",        name: "[LOCAL] Qwen 2.5 Coder 32B" },
    { id: "qwen2.5-14b-instruct-1m",  name: "[LOCAL] Qwen 2.5 14B 1M" },
    { id: "deepseek-coder-v2-lite",   name: "[LOCAL] DeepSeek Coder V2 Lite" },
    // ── DeepSeek API (Pay-as-you-go) ────────────────────────────
    { id: "deepseek-v4-flash",        name: "[PAYANT API] DeepSeek V4 Flash" },
    { id: "deepseek-v4-pro",          name: "[PAYANT API] DeepSeek V4 Pro" },
    { id: "deepseek-chat",            name: "[PAYANT API] DeepSeek V4 Chat" },
    { id: "deepseek-reasoner",        name: "[PAYANT API] DeepSeek R1 Reasoner" },

    // ── Cerebras (gratuit) ──────────────────────────────────────
    { id: "llama3.3-70b",             name: "[GRATUIT Cerebras] Llama 3.3 70B" },
    { id: "gpt-oss-120b",             name: "[GRATUIT Cerebras] GPT OSS 120B" },
    { id: "llama3.1-8b",              name: "[GRATUIT Cerebras] Llama 3.1 8B" },
    { id: "zai-glm-4.7",              name: "[GRATUIT Cerebras] Zai GLM 4.7" },
    // ── OpenRouter (gratuit) ────────────────────────────────────
    { id: "meta-llama/llama-3.3-70b-instruct:free", name: "[GRATUIT OpenRouter] Llama 3.3 70B" },
    { id: "meta-llama/llama-3.1-8b-instruct:free",  name: "[GRATUIT OpenRouter] Llama 3.1 8B" },
    // ── Gemini GCP Payant ───────────────────────────────────────
    { id: "gemini-3.5-flash-paid",    name: "[PAYANT GCP] Gemini 3.5 Flash" },
    { id: "gemini-2.5-flash-paid",    name: "[PAYANT GCP] Gemini 2.5 Flash" },
    { id: "gemini-2.5-pro-paid",      name: "[PAYANT GCP] Gemini 2.5 Pro" },
    { id: "gemini-3.1-pro-preview-paid", name: "[PAYANT GCP] Gemini 3.1 Pro Preview" },
    // ── Mistral ─────────────────────────────────────────────────
    { id: "mistral-large-latest",     name: "[GRATUIT API] Mistral Large" },
    { id: "codestral-latest",         name: "[GRATUIT API] Codestral" },
];

// ─── Modèles recommandés par tier (basé sur les vrais IDs BDD) ───────
const RECOMMENDED_BY_TIER = {
    // Tier LÉGER : modèles locaux et gratuits ultra-rapides
    leger: [
        "gemma-4-31b", "qwen3-coder-next", "deepseek-coder-v2-lite",
        "gemini-3.5-flash", "gemini-3.1-flash-lite"
    ],
    // Tier MOYEN : APIs gratuites fiables pour les tâches courantes
    moyen: [
        "gemini-3.5-flash-high-cli", "gemini-3.5-flash", "gemini-2.5-flash",
        "deepseek-v4-flash", "gpt-oss-120b"
    ],
    // Tier FORT : meilleurs modèles pour la réflexion et le codage complexe
    fort: [
        "claude-opus-4-8", "claude-opus-4-7", "claude-sonnet-4-6",
        "deepseek-v4-pro", "deepseek-reasoner", "gemini-3.1-pro-preview-paid"
    ],
    // Tier AUTOMATIQUE : gamme complète pour sélection dynamique
    automatique: [
        "claude-opus-4-8", "claude-sonnet-4-6", "gemini-3.5-flash-high-cli",
        "gemini-3.5-flash", "deepseek-v4-flash", "gemma-4-31b"
    ]
};

const TIER_COLORS = {
    leger: "var(--accent-primary)",
    moyen: "var(--success)",
    fort: "var(--accent-secondary)",
    automatique: "var(--warning)"
};

const TIER_BG_COLORS = {
    leger: "rgba(129, 140, 248, 0.08)",
    moyen: "rgba(16, 185, 129, 0.08)",
    fort: "rgba(167, 139, 250, 0.08)",
    automatique: "rgba(245, 158, 11, 0.08)"
};

// ─── Fonctions utilitaires pour le badge canal ────────────────
function getChannelBadgeHtml(channelKey) {
    const ch = CHANNELS[channelKey];
    if (!ch) return '';
    const bgMap = {
        "local": "rgba(100,100,100,0.15)",
        "free-api": "rgba(16,185,129,0.12)",
        "cli-claude-pro": "rgba(129,140,248,0.12)",
        "cli-gemini-adv": "rgba(167,139,250,0.12)",
        "paid-deepseek": "rgba(0,212,170,0.12)",
        "paid-gcp": "rgba(245,158,11,0.12)"
    };
    return `<span style="display:inline-flex;align-items:center;gap:0.2rem;font-size:0.62rem;padding:0.1rem 0.35rem;border-radius:3px;background:${bgMap[channelKey] || 'rgba(255,255,255,0.05)'};color:${ch.color};font-weight:600;white-space:nowrap;">${ch.icon} ${ch.costTag}</span>`;
}

function getCostIndicator(entry) {
    if (!entry || !entry.pricing) return '';
    const p = entry.pricing;
    if (p.type === 'local' || p.type === 'free') return '<span title="Gratuit" style="font-size:0.7rem;">🆓</span>';
    if (p.type === 'subscription') return '<span title="Forfait mensuel" style="font-size:0.7rem;">💻</span>';
    // payg
    const cost = (p.inputPerM || 0) + (p.outputPerM || 0);
    if (cost < 1) return '<span title="Ultra low-cost" style="font-size:0.7rem;">💰</span>';
    if (cost < 10) return '<span title="Modéré" style="font-size:0.7rem;">💰💰</span>';
    return '<span title="Coûteux" style="font-size:0.7rem;">💰💰💰</span>';
}

// ─── Mini-barres de benchmarks pour tooltip ───────────────────
function renderMiniBenchmarks(entry) {
    if (!entry || !entry.benchmarks || Object.keys(entry.benchmarks).length === 0) return '';
    let html = '<div style="display:flex;flex-direction:column;gap:0.25rem;margin-top:0.4rem;padding-top:0.4rem;border-top:1px solid var(--border-color);">';
    for (const [name, bm] of Object.entries(entry.benchmarks)) {
        if (!bm || bm.score == null) continue;
        const pct = bm.unit === '%' ? bm.score : Math.min(100, (bm.score / 4000) * 100); // normaliser Elo
        const color = pct >= 90 ? 'var(--success)' : pct >= 70 ? 'var(--warning)' : 'var(--error)';
        html += `
            <div style="font-size:0.65rem;display:flex;align-items:center;gap:0.35rem;">
                <span style="width:75px;color:var(--text-muted);flex-shrink:0;">${name}</span>
                <div style="flex:1;height:4px;background:rgba(255,255,255,0.06);border-radius:2px;overflow:hidden;">
                    <div style="width:${pct}%;height:100%;background:${color};border-radius:2px;transition:width 0.6s ease;"></div>
                </div>
                <span style="font-weight:700;font-size:0.62rem;color:${color};min-width:42px;text-align:right;">${bm.score}${bm.unit} ${bm.rank || ''}</span>
            </div>`;
    }
    html += '</div>';
    return html;
}


/**
 * Genere le tooltip enrichi (sans le badge) — pour checkboxes tiers-config et agents
 * Affiche : titre, canal, description, forces/faiblesses, perf, benchmarks, note abonnement
 */
function getModelTooltipOnly(modelId) {
    const entry = getModelEntry(modelId);
    const fallback = { title: modelId, desc: "Non repertorie.", diff: "Standard.", usage: "Polyvalent.", channel: "free-api", pricing: {type:'free'}, benchmarks: {}, strengths: [], weaknesses: [], perf: {} };
    const d = entry || fallback;

    const chKey = d.channel || 'free-api';
    const isCliModel = chKey.startsWith('cli-') || modelId === 'claude';
    const isClaudeCli = chKey === 'cli-claude-pro';
    const parentAbo = isClaudeCli ? 'Claude Pro (20$/mois)' : 'Gemini Advanced (19.99$/mois)';
    const hourlyLimitStr = isClaudeCli ? '1.5M' : '4M';
    const monthlyLimitStr = isClaudeCli ? '35M' : '100M';

    const channelBadge = getChannelBadgeHtml(chKey);
    const aboBadge = isCliModel ? `<span class="badge-abo">INCLUS ABO</span>` : '';
    const costIndicator = getCostIndicator(d);

    // Section Forces & Faiblesses
    let strengthsHtml = '';
    if (d.strengths && d.strengths.length > 0) {
        const tags = d.strengths.slice(0, 5).map(s =>
            `<span class="tooltip__tag">${s}</span>`
        ).join('');
        strengthsHtml = `
        <div class="tooltip__section">
            <div class="tooltip__section-label">Forces</div>
            <div class="tooltip__strengths">${tags}</div>
        </div>`;
    }

    let weaknessesHtml = '';
    if (d.weaknesses && d.weaknesses.length > 0) {
        const tags = d.weaknesses.slice(0, 3).map(w =>
            `<span class="tooltip__tag tooltip__tag--warn">${w}</span>`
        ).join('');
        weaknessesHtml = `
        <div class="tooltip__section">
            <div class="tooltip__section-label">Limites</div>
            <div class="tooltip__strengths">${tags}</div>
        </div>`;
    }

    // Section Performance
    let perfHtml = '';
    if (d.perf && (d.perf.contextWindow || d.perf.ttft || d.perf.throughput)) {
        const ctx = d.perf.contextWindow ? `${(d.perf.contextWindow / 1000).toFixed(0)}k tokens` : '–';
        const ttft = d.perf.ttft || '–';
        const tput = d.perf.throughput || '–';
        perfHtml = `
        <div class="tooltip__section">
            <div class="tooltip__section-label">Performance</div>
            <div style="display:flex;gap:var(--space-md);flex-wrap:wrap;margin-top:2px;font-size:0.68rem;color:var(--text-muted);">
                <span>Contexte : <strong style="color:var(--text-primary);">${ctx}</strong></span>
                <span>TTFT : <strong style="color:var(--text-primary);">${ttft}</strong></span>
                <span>Debit : <strong style="color:var(--text-primary);">${tput}</strong></span>
            </div>
        </div>`;
    }

    const miniB = renderMiniBenchmarks(d);

    const aboNote = isCliModel
        ? `<div style="margin-top:0.5rem;padding-top:0.4rem;border-top:1px solid var(--border-color);color:var(--accent-secondary);font-size:0.72rem;line-height:1.3;">
            <div><strong>Abo :</strong> ${parentAbo}</div>
            <div style="display:flex;gap:0.4rem;margin-top:0.2rem;font-size:0.65rem;">
                <span style="background:rgba(255,255,255,0.05);border:1px solid var(--border-color);padding:0.1rem 0.25rem;border-radius:3px;">~${hourlyLimitStr}/h</span>
                <span style="background:rgba(255,255,255,0.05);border:1px solid var(--border-color);padding:0.1rem 0.25rem;border-radius:3px;">~${monthlyLimitStr}/m</span>
            </div>
        </div>` : '';

    return `
        <div class="tooltip" style="left:0;transform:translateX(0) translateY(6px);bottom:120%;" onclick="event.stopPropagation();">
            <div class="tooltip__title"><span>${d.title || modelId}</span>${channelBadge}${aboBadge}${costIndicator}</div>
            <div class="tooltip__desc"><strong>Description :</strong> ${d.desc || ''}</div>
            <div class="tooltip__diff"><strong>Atouts :</strong> ${d.diff || ''}</div>
            <div class="tooltip__usage"><strong>Usage :</strong> ${d.usage || ''}</div>
            ${strengthsHtml}
            ${weaknessesHtml}
            ${perfHtml}
            ${miniB}
            ${aboNote}
        </div>
    `;
}

/**
 * Initialisation dynamique du catalogue de modèles depuis la BDD SQLite (Zone 6)
 */
async function initDynamicModelCatalog() {
    try {
        console.log("[Models DB] Chargement dynamique du catalogue de modèles...");
        const dbData = await fetchModelsDB();
        if (!dbData || !dbData.models) {
            console.warn("[Models DB] Données de modèles indisponibles, utilisation du fallback statique.");
            return;
        }

        // 1. Mettre à jour les providers/channels si nécessaire
        if (dbData.providers) {
            dbData.providers.forEach(p => {
                // Mapping provider_id (BDD) → channel key (model-tooltip.js)
                // Inclut les vrais IDs de models_registry.db
                const mapping = {
                    "local":        "local",
                    "gemini_free":  "free-api",
                    "gemini_cli":   "cli-gemini-adv",
                    "gemini_paid":  "paid-gcp",
                    "anthropic":    "cli-claude-pro",
                    "claude_cli":   "cli-claude-pro",   // Vrai ID en BDD
                    "deepseek":     "paid-deepseek",

                    "cerebras":     "free-api",
                    "mistral":      "free-api",
                    "cohere":       "free-api",
                    "openrouter":   "free-api",
                    "cloud_apis":   "paid-gcp",
                    "media":        "media"
                };
                const channelKey = mapping[p.id];
                if (channelKey && CHANNELS[channelKey]) {
                    CHANNELS[channelKey].label = p.name;
                    CHANNELS[channelKey].confidential = p.confidentiality === "confidentiel";
                }
            });
        }

        // 2. Mettre à jour et enrichir MODEL_CATALOG avec la base
        dbData.models.forEach(m => {
            const modelId = m.id;
            
            // Si le modèle n'existe pas dans le catalogue statique, on l'initialise
            if (!MODEL_CATALOG[modelId]) {
                const providerToChannel = {
                    "local":        "local",
                    "gemini_free":  "free-api",
                    "gemini_cli":   "cli-gemini-adv",
                    "gemini_paid":  "paid-gcp",
                    "anthropic":    "cli-claude-pro",
                    "claude_cli":   "cli-claude-pro",  // Vrai ID BDD
                    "deepseek":     "paid-deepseek",

                    "cerebras":     "free-api",
                    "mistral":      "free-api",
                    "cohere":       "free-api",
                    "openrouter":   "free-api",
                    "cloud_apis":   "paid-gcp",
                    "media":        "media"
                };
                
                MODEL_CATALOG[modelId] = {
                    title: m.display_name || modelId,
                    family: m.provider_id === "anthropic" ? "anthropic" : (m.provider_id.startsWith("gemini") ? "google" : m.provider_id),
                    icon: m.supports_audio ? "🔊" : (m.provider_id === "local" ? "🖥️" : "🧠"),
                    channel: providerToChannel[m.provider_id] || "free-api",
                    aliases: [],
                    benchmarks: {},
                    perf: {},
                    pricing: {},
                    strengths: m.speciality ? [m.speciality] : [],
                    weaknesses: [],
                    bestFor: m.recommended_use || "",
                    hallucination_risk: "moyen",
                    confidential: m.provider_id === "local" || m.provider_id === "anthropic" || m.provider_id === "gemini_adv" || m.provider_id === "gemini_paid",
                    desc: m.notes || "",
                    diff: "",
                    usage: m.recommended_use || "",
                    cognitiveProfile: ""
                };
            }
            
            const entry = MODEL_CATALOG[modelId];
            
            // Écraser ou mettre à jour avec les métriques réelles de la BDD
            entry.title = m.display_name || entry.title;
            
            // Contexte et performances réelles
            entry.perf = entry.perf || {};
            entry.perf.contextWindow = m.context_input || entry.perf.contextWindow;
            entry.perf.effectiveContext = m.context_input || entry.perf.effectiveContext;
            if (m.ttft_ms) {
                entry.perf.ttft = `${(m.ttft_ms / 1000).toFixed(2)}s`;
            } else {
                entry.perf.ttft = m.ttft_ms === 0 ? "0s" : entry.perf.ttft;
            }
            if (m.throughput_tps) {
                entry.perf.throughput = `${m.throughput_tps.toFixed(0)} tok/s`;
            }
            
            // Tarification réelle
            entry.pricing = entry.pricing || {};
            entry.pricing.currency = m.currency || "USD";
            
            if (m.provider_id === "local") {
                entry.pricing.type = "local";
                entry.pricing.inputPerM = 0;
                entry.pricing.outputPerM = 0;
            } else if (m.cost_input_per_m === 0 && m.cost_output_per_m === 0) {
                entry.pricing.type = "free";
                entry.pricing.inputPerM = 0;
                entry.pricing.outputPerM = 0;
            } else if (m.provider_id === "anthropic" || m.provider_id === "gemini_adv") {
                // Abonnement
                entry.pricing.type = "subscription";
                entry.pricing.inputPerM = m.cost_input_per_m || 0;
                entry.pricing.outputPerM = m.cost_output_per_m || 0;
                entry.pricing.amortizedPerM = m.cost_input_per_m || 0;
            } else {
                // Pay-as-you-go
                entry.pricing.type = "payg";
                entry.pricing.inputPerM = m.cost_input_per_m || 0;
                entry.pricing.outputPerM = m.cost_output_per_m || 0;
                if (m.cost_cached_per_m) {
                    entry.pricing.note = `Cache hit : ${m.cost_cached_per_m} $/M`;
                }
            }
            
            if (m.notes) entry.desc = m.notes;
        });

        console.log("[Models DB] Catalogue de modèles synchronisé avec SQLite !");
        
        // Notification de mise à jour s'il y a lieu de ré-afficher l'onglet
        if (typeof activeTab !== "undefined" && activeTab === "catalog" && typeof renderCatalog === "function") {
            const pane = document.querySelector(".tab-pane.active");
            if (pane) renderCatalog(pane);
        }
    } catch (err) {
        console.error("[Models DB] Échec de la synchronisation du catalogue :", err);
    }
}



// ═══════════════════════════════════════════════════════════════
// BADGE HTML ENRICHI + TOOLTIP EPINGLE (clic pour fixer)
// ═══════════════════════════════════════════════════════════════

function getModelBadgeHtml(modelId, customLabel) {
    customLabel = customLabel || null;
    var entry = getModelEntry(modelId);
    var d = entry || { title: modelId, desc: "Non repertorie.", diff: "", usage: "", channel: "free-api", pricing: {type:"free"}, benchmarks: {}, strengths: [], weaknesses: [], perf: {}, cognitiveProfile: "" };
    var label = customLabel || d.title || modelId;
    var chKey = d.channel || "free-api";
    var isCliModel = chKey.startsWith("cli-") || modelId === "claude";
    var isClaudeCli = chKey === "cli-claude-pro";
    var parentAbo = isClaudeCli ? "Claude Pro (20$/mois)" : "Gemini Advanced (19.99$/mois)";
    var hLim = isClaudeCli ? "1.5M" : "4M";
    var mLim = isClaudeCli ? "35M" : "100M";
    var channelBadge = getChannelBadgeHtml(chKey);
    var costIndicator = getCostIndicator(d);
    var aboBadge = isCliModel ? '<span class="badge-abo">INCLUS ABO</span>' : "";
    var aboNote = isCliModel ? '<div style="margin-top:0.5rem;color:var(--accent-secondary);font-size:0.72rem;"><div><strong>Abo :</strong> ' + parentAbo + '</div><div style="font-size:0.65rem;">~' + hLim + '/h · ~' + mLim + '/m</div></div>' : "";
    var strengths = (d.strengths || []).slice(0,5).map(function(s){return '<span class="tooltip__tag">'+s+'</span>';}).join("");
    var weaknesses = (d.weaknesses || []).slice(0,3).map(function(w){return '<span class="tooltip__tag tooltip__tag--warn">'+w+'</span>';}).join("");
    var strengthsHtml = strengths ? '<div class="tooltip__section"><div class="tooltip__section-label">Forces</div><div class="tooltip__strengths">'+strengths+'</div></div>' : "";
    var weaknessesHtml = weaknesses ? '<div class="tooltip__section"><div class="tooltip__section-label">Limites</div><div class="tooltip__strengths">'+weaknesses+'</div></div>' : "";
    var perfParts = [];
    if (d.perf) {
        if (d.perf.contextWindow) perfParts.push("Contexte : <strong>" + (d.perf.contextWindow/1000).toFixed(0) + "k tok</strong>");
        if (d.perf.ttft) perfParts.push("TTFT : <strong>" + d.perf.ttft + "</strong>");
        if (d.perf.throughput) perfParts.push("Debit : <strong>" + d.perf.throughput + "</strong>");
    }
    var perfHtml = perfParts.length ? '<div class="tooltip__section"><div class="tooltip__section-label">Performance</div><div style="font-size:0.68rem;color:var(--text-muted);">' + perfParts.join(" · ") + "</div></div>" : "";
    var cogHtml = d.cognitiveProfile ? '<div class="tooltip__section"><div class="tooltip__section-label">Profil cognitif</div><div style="font-size:0.68rem;color:var(--text-muted);font-style:italic;">' + d.cognitiveProfile.substring(0,160) + (d.cognitiveProfile.length > 160 ? "..." : "") + "</div></div>" : "";
    var miniB = renderMiniBenchmarks(d);
    var uid = "badge-" + modelId.replace(/[^a-zA-Z0-9]/g, "-") + "-" + Math.random().toString(36).slice(2,6);
    return '<div class="tooltip-wrap" id="' + uid + '" onclick="toggleTooltipPin(\''+uid+'\', event)"><span class="model-badge '+(isCliModel?"model-badge--cli":"")+'">'+costIndicator+" "+label+'</span><div class="tooltip"><div class="tooltip__title"><span>'+d.title+'</span><span style="display:flex;gap:3px;">'+channelBadge+aboBadge+'</span></div><div class="tooltip__desc"><strong>Description :</strong> '+d.desc+'</div><div class="tooltip__diff"><strong>Atouts :</strong> '+d.diff+'</div><div class="tooltip__usage"><strong>Usage :</strong> '+d.usage+'</div>'+strengthsHtml+weaknessesHtml+perfHtml+miniB+cogHtml+aboNote+'<div style="margin-top:0.4rem;text-align:right;font-size:0.58rem;color:var(--text-muted);opacity:0.4;">Clic = epingler</div></div></div>';
}

function toggleTooltipPin(uid, event) {
    event.stopPropagation();
    var el = document.getElementById(uid);
    if (!el) return;
    var wasPinned = el.classList.contains("tooltip-pinned");
    document.querySelectorAll(".tooltip-wrap.tooltip-pinned").forEach(function(w){ w.classList.remove("tooltip-pinned"); });
    if (!wasPinned) {
        el.classList.add("tooltip-pinned");
        var tooltip = el.querySelector(".tooltip");
        if (tooltip) {
            var rect = el.getBoundingClientRect();
            if (rect.top < 250) { tooltip.classList.add("tooltip--below"); } else { tooltip.classList.remove("tooltip--below"); }
        }
        setTimeout(function() {
            var closer = function(e) { if (!el.contains(e.target)) { el.classList.remove("tooltip-pinned"); document.removeEventListener("click", closer); } };
            document.addEventListener("click", closer);
        }, 10);
    }
}
