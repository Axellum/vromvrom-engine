# IHM v2 — tab5-engine

Interface de pilotage, de réglage et de compréhension du moteur multi-agents.

**Stack** : React 19 + Vite 8 + TypeScript + Zustand (état) + React Query (data serveur)
+ Tailwind CSS 4 + Chart.js + React Flow.

## Principe directeur : ne jamais afficher de donnée inventée

C'est la règle qui prime sur toutes les autres. Concrètement :

- **Aucun repli sur des mocks.** Si un endpoint échoue, l'erreur remonte à la vue et
  s'affiche avec son message. Les modules API ne renvoient jamais de données de
  substitution — trois vues le faisaient et présentaient des chiffres crédibles mais
  entièrement fictifs.
- **« — » signifie « jamais mesuré »**, pas zéro (composant `Value`).
- **Chaque bloc affiche sa source** (prop `source` de `Card`) : l'endpoint moteur d'où
  viennent les chiffres, pour pouvoir toujours remonter à l'origine d'une valeur.
- **Une requête suspendue n'est pas un résultat vide** (`queryPhase` + `PausedState`) :
  le motif `isLoading / isError / vide` laissait afficher « aucune donnée » alors que
  rien n'était revenu du serveur.
- **Un contrôle qui n'a pas de backend n'est pas affiché.** Pas d'interrupteur
  décoratif, pas de bouton qui échoue en silence.

## Navigation

| Groupe | Vues |
| --- | --- |
| **Pilotage** | Vue d'ensemble (SSE live), Chat (sélecteur de tier), Lancer une tâche |
| **Domotique** | Domotique & Vocal — santé HA réelle, entités, pipeline vocal, journal STT |
| **Moteur** | Agents, Workflows, LLMs & routage, Prompt Studio, Comparatif modèles |
| **Autonomie** | Agents autonomes — Daemon, Dreamer/DreamCoder, Auditeur |
| **Optimisation** | Opérations, Observabilité, Comptes API, Configuration (~45 réglages) |
| **Comprendre** | Installation (diagnostic effectif), Documentation (+ guides d'optimisation) |

## Authentification (propre, sans clé dans l'URL)

- **REST** (`/api/*`) : header `Authorization: Bearer <MOTEUR_API_KEY>`.
- **SSE** (`/api/stream`) : **ticket éphémère** à usage unique via `POST /api/auth/ticket`,
  puis `?ticket=<ticket>`. La clé ne transite **jamais** dans une URL.

La clé est stockée en `localStorage` (outil personnel sur LAN).

## Développement

```bash
cd ihm-v2 && npm install && npm run dev
```

Vite sur <http://localhost:5173>, proxy `/api`, `/v1`, `/ws`, `/version` → `:8000`.
Cible surchargeable : `MOTEUR_API_URL=http://192.168.0.43:8000 npm run dev`.

Le moteur (`gui_server.py`) doit tourner et autoriser l'origine de dev dans
`MOTEUR_CORS_ORIGINS` (ex : `http://localhost:5173`).

## Build & déploiement

```bash
npm run build      # → dist/, base "/"
```

`gui_server.py` sert `ihm-v2/dist` **à la racine `/`** si le build existe, et bascule
sur l'ancienne IHM (`static/`) sinon. L'IHM v1 reste accessible sous `/v1`.

> Le script `build:v2` (base `/v2/`) est un reliquat de la migration : le moteur ne
> monte plus rien sous `/v2`. Utiliser `npm run build`.

## Structure

```
src/
├── api/         # un module par domaine ; contrats vérifiés contre le backend réel
├── components/
│   ├── ui/      # primitives partagées (Card, Value, Explain, queryPhase…)
│   ├── config/  # contrôles d'édition de config.json (toggle, list ordonnée, select)
│   ├── common/  # ApiKeyGate, ErrorBoundary, Markdown, historique de sessions
│   └── layout/  # coquille + navigation groupée
├── hooks/       # useEngineStream (SSE avec reconnexion par ticket)
├── state/       # stores Zustand (moteur, chat, navigation)
├── types/       # contrat moteur↔IHM + registre des réglages (config.ts)
└── views/       # une vue par entrée de navigation
```

### Ajouter un réglage

Ajouter une entrée dans `CONFIG_SECTIONS` (`src/types/config.ts`) : chemin pointé dans
`config.json`, type de contrôle, aide, et `impact` (l'effet réel sur le comportement du
moteur). La vue Configuration s'occupe du reste (dirty-tracking, merge partiel, recherche).
