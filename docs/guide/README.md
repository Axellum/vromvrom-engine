# Guide d'utilisation du Moteur IA

Ce guide s'adresse à quelqu'un qui découvre **vromvrom-engine** (le « Moteur IA ») et veut l'installer chez lui, comprendre ce
qu'il fait et le régler, **avec ou sans Home Assistant**. Il est en français ; le [README du dépôt](../../README.fr.md)
([English](../../README.md)) donne la vue d'ensemble.

Le Moteur IA est un serveur Python qui reçoit des demandes (chat, API, outils MCP, voix via Home Assistant), choisit le chemin le
moins cher qui suffit (des règles d'abord, un modèle ensuite), agit, et compte les jetons et les dollars.

## Les pages

| Page | À qui elle s'adresse | Contenu |
|---|---|---|
| [01 Installer](01_installer.md) | Tout le monde, en premier | Prérequis, installation Windows et Linux, Docker, premier lancement, première demande, service permanent, client MCP |
| [02 Comprendre](02_comprendre.md) | Qui veut savoir ce qui se passe quand on envoie une demande | Chemins d'une demande, choix du modèle, agents, validation humaine, boucles de fond, visite de l'interface |
| [03 Réglages](03_reglages.md) | Qui règle le moteur | Chaque variable du `.env` et chaque clé de `config.json`, avec son défaut, son effet, son redémarrage |
| [04 Maison et voix](04_maison_et_voix.md) | Qui a Home Assistant | Brancher Home Assistant, catalogue de commandes, simulation sans effet, voix, repli local |
| [05 Exploiter](05_exploiter.md) | Qui fait tourner le moteur dans la durée | Santé, sauvegarde et restauration, mise à jour, dépannage, sécurité, **coûts** |

## Dans quel ordre lire

1. **[01 Installer](01_installer.md)** : une installation qui répond à `/healthz` et à une première demande.
2. **[05 Exploiter, § 6 (coûts)](05_exploiter.md)** : avant de poser une clé de fournisseur, sachez ce qui consomme des jetons tout
   seul.
3. **[02 Comprendre](02_comprendre.md)** : pour savoir ce que vous faites tourner.
4. **[03 Réglages](03_reglages.md)** quand vous voulez changer un comportement.
5. **[04 Maison et voix](04_maison_et_voix.md)** si vous avez Home Assistant. Sans lui, ignorez cette page.

## Documentation déjà publiée (non répétée ici)

| Document | Pour |
|---|---|
| [`README.fr.md`](../../README.fr.md) / [`README.md`](../../README.md) | Présentation, installation rapide, fournisseurs |
| [`STRATEGIES.fr.md`](../../STRATEGIES.fr.md) | Quels modèles utiliser, à quel prix (certaines sections de configuration y sont plus anciennes que le code : voir [03 § 6](03_reglages.md)) |
| [`docs/API_REFERENCE.fr.md`](../API_REFERENCE.fr.md) | Les routes de l'API |
| [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) | L'architecture : cliché de juin 2026, antérieur à ce guide (la page [02](02_comprendre.md) est à jour) |
| [`docs/architecture/carte_decision_vocale.md`](../architecture/carte_decision_vocale.md) | L'ordre exact des raccourcis vocaux |
| [`ihm-v2/README.md`](../../ihm-v2/README.md) | L'interface web : développement et construction |

## Comment lire les chiffres et les commandes

- Chaque défaut, variable, route ou menu vient du code de ce dépôt ; le fichier est cité. **« — »** veut dire inconnu ou non
  mesuré, et **« non essayé »** que la chose a été lue dans le code mais pas exécutée.
- Les commandes Windows sont écrites pour **PowerShell 5.1** (pas de `&&`, pas de `curl -H` : on utilise `Invoke-RestMethod`),
  avec l'équivalent bash pour Linux et macOS.
- Une adresse comme `192.168.1.20` ou une entité comme `light.living_room` est un **exemple** : remplacez-la par la vôtre.
- Les pages ont été vérifiées par un démarrage réel du moteur sur Windows 11 avec Python 3.13 (sans clé de fournisseur ni Home
  Assistant). Linux, macOS, Docker et les unités systemd n'ont pas été essayés.

## Une limite à connaître tout de suite

Le composant Home Assistant qui transmet la voix au moteur (`moteur_ia_conversation`) **n'est pas dans ce dépôt**. Le chat,
l'API, les agents et le serveur MCP fonctionnent sans lui ; la voix de bout en bout demande ce composant, ou un équivalent que
vous écrivez ([04 § 5](04_maison_et_voix.md)).
