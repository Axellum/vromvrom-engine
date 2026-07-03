# 🗺️ Guide des Stratégies LLM

> **Quels modèles utiliser ?** Ce guide vous aide à configurer vromvrom-engine
> selon votre budget, votre matériel et vos exigences de performance.
> Les snippets `.env` + `config.json` sont prêts à copier-coller.

🇬🇧 **[English version → STRATEGIES.md](STRATEGIES.md)**

---

## Les 4 stratégies en un coup d'œil

| Stratégie | Coût mensuel | Performance | Complexité |
|---|---|---|---|
| [🆓 Gratuit & Local](#-stratégie-1--gratuit--local-uniquement) | **0 €** | Bonne | Faible |
| [🐉 Puissance chinoise](#-stratégie-2--modèles-chinois--haute-perf-faible-coût) | **~5–15 €** | Excellente | Faible |
| [⚡ Optimisé Claude](#-stratégie-3--apicli-claude--coût-additionnel-quasi-nul) | **~0 € extra** | Excellente | Moyenne |
| [🚀 Pleine puissance](#-stratégie-4--pleine-puissance--coût-optimisé) | **~20–50 €** | État de l'art | Moyenne |

> **Point clé** : le routage Elo de vromvrom-engine signifie que vous n'avez pas
> à vous engager sur un seul modèle. Mixez librement les providers — le moteur
> apprend lequel performe le mieux *pour chaque type de tâche*.

---

## 🆓 Stratégie 1 — Gratuit & Local uniquement

**Objectif** : faire tourner un moteur d'orchestration capable à coût zéro.
**Idéal pour** : makers, hobbyistes, utilisateurs soucieux de leur vie privée, setups hors-ligne.

### Comment ça fonctionne
- **Gemini free tier** (Google AI Studio) : jusqu'à 5 clés en rotation → 5× le quota gratuit
- **GitHub Models** : GPT-4o-mini + Llama 3.3 70B gratuitement avec n'importe quel compte GitHub
- **LM Studio ou Ollama** : inférence locale sur votre GPU/CPU pour les tâches lourdes

### Modèles recommandés

| Rôle | Modèle | Provider | Coût |
|---|---|---|---|
| Planificateur (raisonnement) | `llama-3.3-70b-instruct` | GitHub Models | Gratuit |
| Exécuteur (tâches rapides) | `gemini-2.0-flash` | Google AI Studio | Gratuit |
| Reviewer | `gpt-4o-mini` | GitHub Models | Gratuit |
| Compression / résumé | `qwen2.5-14b-instruct` | LM Studio (local) | Gratuit |
| Embeddings (RAG) | `nomic-embed-text` | Ollama (local) | Gratuit |

### Configuration `.env`

```env
# Jusqu'à 5 clés Gemini pour la rotation de quota — obtenez-les sur aistudio.google.com/apikey
GEMINI_API_KEY=AIza...cle1
GEMINI_API_KEY_2=AIza...cle2
GEMINI_API_KEY_3=AIza...cle3
GEMINI_MODEL=gemini-2.0-flash

# GitHub Models — gratuit avec n'importe quel compte GitHub
# Générer sur : github.com/settings/tokens (aucun scope particulier requis)
GITHUB_TOKEN=ghp_...

# Inférence locale — pointer vers votre instance LM Studio ou Ollama
# Aucune clé nécessaire, juste l'URL
```

### Configuration `config.json`

```json
{
  "planner_model":  "fort",
  "executor_model": "leger",
  "reviewer_model": "leger",
  "tiers": {
    "leger": ["local", "gemini-flash", "github-gpt4o-mini"],
    "moyen": ["github-llama-70b", "gemini-flash"],
    "fort":  ["github-llama-70b", "gemini-pro"],
    "automatique": ["local", "gemini-flash", "github-gpt4o-mini", "github-llama-70b"]
  },
  "local_llm": {
    "lmstudio_url": "http://localhost:1234",
    "ollama_url":   "http://localhost:11434",
    "model": "qwen2.5-14b-instruct"
  }
}
```

### Performance attendue
- Tâches simples (chat, résumés) : ✅ Excellente — local ou Gemini Flash
- Raisonnement complexe : ⚠️ Bonne — Llama 70B via GitHub Models est capable mais avec de la latence
- Fenêtre de contexte : ✅ Jusqu'à 128K tokens avec Llama 3.3

> **Astuce** : tournez 5 clés Gemini pour multiplier votre quota gratuit par 5.
> Chaque clé donne ~1500 requêtes/jour sur le free tier.

---

## 🐉 Stratégie 2 — Modèles chinois → Haute perf, faible coût

**Objectif** : performance proche des frontières à une fraction du prix des providers occidentaux.
**Idéal pour** : développeurs qui veulent une vraie capacité sans une vraie facture.

### Pourquoi les modèles chinois ?

DeepSeek V3 coûte **50× moins cher que GPT-4o** et lui est comparable sur la plupart des benchmarks.
DeepSeek R1 rivalise avec OpenAI o1 sur les tâches de raisonnement à **$0.14/M tokens** contre **$15/M tokens**.
Ce ne sont pas des compromis — ce sont les champions actuels du rapport qualité/prix.

### Modèles recommandés

| Rôle | Modèle | Provider | Coût input | Coût output |
|---|---|---|---|---|
| Planificateur (raisonnement profond) | `deepseek-reasoner` (R1) | DeepSeek | $0.14/M | $2.19/M |
| Exécuteur (tâches standard) | `deepseek-chat` (V3) | DeepSeek | $0.07/M | $1.10/M |
| Tâches rapides / routage | `gemini-2.0-flash` | Google (gratuit) | Gratuit | Gratuit |
| Résumé / compression | `qwen2.5-14b` | LM Studio (local) | $0 | $0 |
| Embeddings | `gemini-embedding-004` | Google (gratuit) | Gratuit | Gratuit |

> **Coût mensuel réaliste pour une utilisation intensive** : ~5–15 €

### Configuration `.env`

```env
# DeepSeek — clé sur platform.deepseek.com
DEEPSEEK_API_KEY=sk-...

# Gemini gratuit pour le routage et les embeddings
GEMINI_API_KEY=AIza...
GEMINI_MODEL=gemini-2.0-flash
```

### Configuration `config.json`

```json
{
  "planner_model":  "fort",
  "executor_model": "moyen",
  "reviewer_model": "leger",
  "tiers": {
    "leger": ["local", "gemini-flash", "deepseek-chat"],
    "moyen": ["deepseek-chat", "gemini-flash"],
    "fort":  ["deepseek-reasoner", "deepseek-chat"],
    "automatique": ["local", "gemini-flash", "deepseek-chat", "deepseek-reasoner"]
  }
}
```

### Performance attendue
- Raisonnement / planification : ✅ Excellente — R1 égale o1 sur la plupart des benchmarks
- Génération de code : ✅ Excellente — V3 est top-3 mondial
- Latence : ⚠️ DeepSeek peut être lent aux heures de pointe (fuseau horaire chinois)
- Confidentialité : ⚠️ Données traitées en Chine — déconseillé pour les données sensibles

> **Astuce** : le routeur Elo détectera automatiquement si DeepSeek est lent ou indisponible
> et basculera sur Gemini Flash — vous restez opérationnel sans intervention manuelle.

---

## ⚡ Stratégie 3 — API/CLI Claude → Coût additionnel quasi nul

**Objectif** : profiter de l'intelligence frontier de Claude en payant presque rien de plus.
**Idéal pour** : développeurs ayant un abonnement Claude Pro ($20/mois).

### L'insight

Si vous payez déjà Claude Pro/Max, le **Claude Code CLI est inclus**.
vromvrom-engine peut l'appeler en sous-processus — ce qui signifie que vous consommez
votre quota d'abonnement inclus au lieu de payer des tarifs API au token.

**Coût effectif des appels Claude via CLI : ~0 € extra** (déjà dans votre abonnement).

### Modèles recommandés

| Rôle | Modèle | Accès | Coût |
|---|---|---|---|
| Planificateur (tâches complexes) | `claude-opus-4-5` | Claude Code CLI | Inclus dans Pro |
| Exécuteur (standard) | `claude-sonnet-4-5` | Claude Code CLI | Inclus dans Pro |
| Tâches rapides / fallback | `gemini-2.0-flash` | Google (gratuit) | Gratuit |
| Raisonnement lourd | `deepseek-reasoner` | DeepSeek API | $0.14/M |
| Local / compression | `qwen2.5-14b` | LM Studio | Gratuit |

### Configuration `.env`

```env
# Claude Code CLI doit être installé : npm install -g @anthropic-ai/claude-code
# Aucune clé API nécessaire — le CLI utilise votre session navigateur / abonnement Pro

# Gemini comme fallback gratuit
GEMINI_API_KEY=AIza...
GEMINI_MODEL=gemini-2.0-flash

# DeepSeek pour le raisonnement lourd (très bon marché)
DEEPSEEK_API_KEY=sk-...
```

### Configuration `config.json`

```json
{
  "planner_model":  "fort",
  "executor_model": "moyen",
  "reviewer_model": "leger",
  "tiers": {
    "leger": ["local", "gemini-flash"],
    "moyen": ["claude-sonnet-cli", "gemini-flash"],
    "fort":  ["claude-opus-cli", "deepseek-reasoner"],
    "automatique": ["local", "gemini-flash", "claude-sonnet-cli", "claude-opus-cli"]
  }
}
```

### Comment fonctionne le provider CLI

```python
# Le moteur appelle Claude Code CLI en sous-processus :
# claude --model claude-opus-4-5 --output-format json -p "votre prompt"
# Il parse la sortie JSON et la traite comme n'importe quel autre provider.
# Si votre quota Pro est épuisé, il bascule automatiquement sur le provider suivant du tier.
```

### Performance attendue
- Qualité : ✅ État de l'art (Claude Opus est actuellement dans le top mondial)
- Coût : ✅ Quasi nul si vous avez déjà Claude Pro
- Latence : ⚠️ Le sous-processus CLI ajoute ~1–2s de surcharge vs API directe
- Fiabilité : ✅ Fallback automatique sur Gemini si le quota CLI est épuisé

> **Astuce** : utilisez `claude-sonnet-4-5` pour 90% des tâches (plus rapide, moins gourmand en quota)
> et réservez `claude-opus-4-5` uniquement pour le Planificateur.

---

## 🚀 Stratégie 4 — Pleine puissance + coût optimisé

**Objectif** : qualité état de l'art sur les tâches complexes, modèles économiques sur les tâches simples.
**Idéal pour** : utilisateurs avancés, déploiements en production, workflows exigeants.

### La philosophie

Toutes les tâches ne nécessitent pas GPT-4o. Le routeur Elo dirige :
- Recherches simples → Gemini Flash gratuit (~0ms de surcharge)
- Tâches dev standard → DeepSeek V3 (~0.001€ par tâche)
- Planification complexe → Gemini 2.5 Pro ou Claude Opus (~0.01–0.05€ par tâche)
- Chaînes de raisonnement → DeepSeek R1 (~0.003€ par tâche, fraction du prix d'o1)

**Résultat** : qualité frontier, ~10× moins cher qu'utiliser GPT-4o/Claude pour tout.

### Modèles recommandés

| Rôle | Modèle | Provider | Pourquoi |
|---|---|---|---|
| Planificateur stratégique | `gemini-2.5-pro` | Google | Meilleur pour la planification structurée, contexte énorme |
| Tâches de raisonnement | `deepseek-reasoner` | DeepSeek | Équivaut o1, 50× moins cher |
| Génération de code | `claude-sonnet-4-5` | Anthropic API | Top qualité code |
| Exécution standard | `deepseek-chat` | DeepSeek | Meilleur rapport qualité/prix |
| Routage rapide / chat | `gemini-2.0-flash` | Google (free tier) | Quasi instantané, gratuit |
| Compression locale | `qwen2.5-14b` | LM Studio | Résumé à coût zéro |

### Configuration `.env`

```env
# Google Gemini (gratuit + payant)
GEMINI_API_KEY=AIza...           # clé free tier
GEMINI_PAYANT_API_KEY=AIza...    # clé GCP payante pour Gemini 2.5 Pro
GEMINI_MODEL=gemini-2.0-flash

# DeepSeek — l'épine dorsale de l'optimisation des coûts
DEEPSEEK_API_KEY=sk-...

# Anthropic — pour les tâches code où la qualité prime
ANTHROPIC_API_KEY=sk-ant-...

# GitHub Models comme fallback gratuit supplémentaire
GITHUB_TOKEN=ghp_...
```

### Configuration `config.json`

```json
{
  "planner_model":  "fort",
  "executor_model": "moyen",
  "reviewer_model": "leger",
  "tiers": {
    "leger": ["local", "gemini-flash", "deepseek-chat"],
    "moyen": ["deepseek-chat", "claude-sonnet", "gemini-flash"],
    "fort":  ["gemini-2.5-pro", "deepseek-reasoner", "claude-opus"],
    "automatique": ["local", "gemini-flash", "deepseek-chat", "deepseek-reasoner", "gemini-2.5-pro"]
  },
  "local_llm": {
    "lmstudio_url": "http://localhost:1234",
    "model": "qwen2.5-14b-instruct"
  }
}
```

### Décomposition des coûts (exemple : 100 tâches complexes/jour)

| Type de tâche | % des tâches | Modèle utilisé | Coût/tâche | Coût journalier |
|---|---|---|---|---|
| Chat simple / recherche | 40% | Gemini Flash | 0.00 € | 0.00 € |
| Tâches dev standard | 35% | DeepSeek V3 | ~0.001 € | ~0.035 € |
| Raisonnement complexe | 20% | DeepSeek R1 | ~0.003 € | ~0.06 € |
| Planification stratégique | 5% | Gemini 2.5 Pro | ~0.02 € | ~0.10 € |
| **Total** | 100% | Mix | — | **~0.20 €/jour** |

> ~6 €/mois pour 100 tâches complexes/jour. GPT-4o seul pour le même volume : ~60 €+/mois.

---

## 🔧 Comment le routage Elo améliore ces configs dans le temps

Quelle que soit la stratégie choisie, le système Elo apprend en continu :

```
Semaine 1 : DeepSeek R1 obtient de bons scores sur vos tâches "analyse_code"
            → son Elo "analyse_code" monte
            → le moteur route davantage "analyse_code" vers R1

Semaine 2 : Gemini Flash commence à échouer sur les lookups "home_assistant"
            → son Elo "home_assistant" chute
            → le moteur route ces tâches vers DeepSeek ou le local

Semaine 3 : votre matrice de routage personnelle est optimisée POUR VOS tâches
            → meilleurs résultats, coût réduit, sans réglage manuel
```

**Vous ne configurez pas le moteur une fois pour toutes — il se configure lui-même.**

---

## 📊 Référence rapide des providers

| Provider | Free tier | Points forts | Points faibles |
|---|---|---|---|
| **Google Gemini Flash** | ✅ Généreux | Rapidité, multimodal, contexte énorme | Moins précis sur le raisonnement complexe |
| **Google Gemini 2.5 Pro** | ❌ Payant | Meilleure planification, 1M de contexte | Coût, pas le moins cher |
| **DeepSeek V3** | ❌ Payant | Meilleur rapport qualité/prix, excellent code | Ralentissements aux heures de pointe |
| **DeepSeek R1** | ❌ Payant | Raisonnement = équivaut o1 | Plus lent, pensée verbeuse |
| **GitHub Models** | ✅ Gratuit | GPT-4o-mini + Llama 70B | Limites de débit, pas de fine-tuning |
| **Claude Opus/Sonnet** | ❌ Payant (CLI=gratuit) | Top code + suivi d'instructions | Coût si via API |
| **LM Studio / Ollama** | ✅ Gratuit | Confidentialité, latence zéro, hors-ligne | GPU requis pour la qualité |
| **OpenRouter** | ❌ Payant | Accès à 200+ modèles | Couche de latence supplémentaire |

---

*Dernière mise à jour : juillet 2026 — Le paysage des modèles évolue vite. Le routeur Elo s'adapte automatiquement.*
