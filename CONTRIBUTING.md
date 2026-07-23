# Contribuer à vromvrom-engine

Merci de vouloir contribuer ! Ce projet est né d'un besoin personnel en domotique et a évolué vers quelque chose de potentiellement utile pour la communauté. Toute aide est bienvenue.

## Avant de commencer

1. **Ouvrir une issue** avant de travailler sur une grosse fonctionnalité — pour s'assurer qu'elle s'aligne avec la direction du projet.
2. **Pour les corrections de bugs**, une PR directe est parfaite.

## Workflow

**Toute mise à jour de `main` passe par une Pull Request** (branche protégée) :
CI obligatoire = scan anti-fuite OSS + pytest (+ Bandit informatif).

```bash
# Forker le repo, puis :
git clone https://github.com/<votre-username>/vromvrom-engine.git
cd vromvrom-engine

# Créer une branche dédiée
git checkout -b feat/nom-de-la-fonctionnalite
# ou
git checkout -b fix/nom-du-bug

# Installer les dépendances de dev
pip install -r requirements-dev.txt

# Travailler, puis lancer les vérifs locales
python scripts/oss_secrets_scan.py
pytest

# Committer (convention Conventional Commits)
git commit -m "feat(router): add OpenAI-compatible provider"

# Pousser la branche et ouvrir une PR → attendre le CI vert → merge
```

### Mainteneurs — sync depuis le miroir privé

Ne **jamais** pousser directement sur `main`. Flux :

1. Exporter / anonymiser (IP `192.168.0.x` → placeholders, aucun token/clé).
2. Branche `sync/…` + PR.
3. CI vert (`🔒 OSS secrets scan`, `🧪 Pytest`, `test-and-scan`).
4. Merge de la PR uniquement.

## Conventions de code

- **Python 3.11+** — typage strict avec annotations
- **Async par défaut** — toute nouvelle I/O doit être `async/await`
- **Pydantic pour les schémas** — pas de dicts non typés dans les interfaces publiques
- **Tests** — toute nouvelle fonctionnalité doit avoir au moins un test pytest
- **Commentaires en français ou anglais** — les deux sont acceptés

## Axes prioritaires (contributions les plus utiles)

| Priorité | Sujet | Difficulté |
|---|---|---|
| 🔴 Haute | Dockerisation (Dockerfile + docker-compose.yml) | Moyenne |
| 🔴 Haute | Guide "Quick Start" testé sur Linux/Mac | Faible |
| 🟡 Moyenne | Support provider OpenAI natif (sans proxy) | Faible |
| 🟡 Moyenne | Tests d'intégration end-to-end | Moyenne |
| 🟢 Basse | Nouvelles icônes/améliorations IHM | Faible |
| 🟢 Basse | Support de nouveaux providers LLM | Faible |

## Signaler un bug

Utiliser le template d'issue GitHub. Inclure :
- Version Python et OS
- Contenu de `config.json` (sans les clés API)
- Logs d'erreur complets
- Étapes pour reproduire

## Questions

Ouvrir une **Discussion** GitHub — pas une Issue.

---

*Ce projet suit le [Code de Conduite Contributor Covenant](https://www.contributor-covenant.org/).*
