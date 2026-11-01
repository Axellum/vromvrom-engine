# Brancher la maison et la voix

> **En une phrase.** Le moteur parle à Home Assistant par son API (adresse + jeton), commande la maison avec un
> catalogue de phrases (`ha_commands.json`), et reçoit la voix **par l'agent de conversation de Home Assistant** : cet
> agent (`moteur_ia_conversation`) **n'est pas dans ce dépôt public**. Cette page dit ce qui est faisable aujourd'hui avec
> le dépôt seul, et ce qui ne l'est pas.

Sans Home Assistant, ignorez cette page : le chat, l'API et les agents fonctionnent ([01](01_installer.md)).

Pages liées : [02 Comprendre](02_comprendre.md) · [03 Réglages](03_reglages.md) · [05 Exploiter](05_exploiter.md).
Références : [`docs/architecture/carte_decision_vocale.md`](../architecture/carte_decision_vocale.md) (ordre exact des raccourcis
vocaux) et le firmware d'écran public
[Axellum/M5-Tab5-ESPHome-LVGL](https://github.com/Axellum/M5-Tab5-ESPHome-LVGL).

**Essayé pour ce guide** : l'API du moteur sans Home Assistant (`/api/ha/health`, `/api/vocal/simulate`,
`/api/vocal/config`, `/api/setup/diagnostics`). **Non essayé** : toute connexion à un vrai Home Assistant, tout
écran, toute commande réelle de la maison.

---

## 1. Qui fait quoi, et où est chaque pièce

| Maillon | Rôle | Où le trouver |
|---|---|---|
| Écran ou satellite vocal | Détecte « Okay Nabu », envoie la voix à Home Assistant, joue la réponse | Firmware public : [Axellum/M5-Tab5-ESPHome-LVGL](https://github.com/Axellum/M5-Tab5-ESPHome-LVGL). Un autre satellite Assist de Home Assistant peut jouer ce rôle (non essayé). |
| Pipeline Assist | Voix → texte (STT), agent de conversation, texte → voix (TTS) | Home Assistant (Paramètres → Assistants vocaux) |
| **Agent de conversation `moteur_ia_conversation`** | Envoie le texte reconnu au moteur et rend sa réponse | **Absent de ce dépôt** (§ 5) |
| Moteur | Choisit le chemin, commande la maison ou interroge un modèle | Ce dépôt |
| Repli local | Quelques commandes vitales servies par Home Assistant seul quand le moteur est éteint | `deploy/ha_local_fallback/` (§ 7) |

Le moteur ne reçoit pas l'audio : il reçoit **le texte** déjà transcrit.

---

## 2. Brancher Home Assistant

### 2.1 Les trois variables

1. Dans Home Assistant : votre profil → **Sécurité** → **Jetons d'accès longue durée** → créer un jeton.
2. Dans le `.env` du moteur ([03 § 2.3](03_reglages.md)) :

```ini
HASS_URL=https://192.168.1.20:8123
HASS_TOKEN=<le jeton>
```

3. Redémarrer le moteur (le `.env` n'est lu qu'au démarrage).

`HASS_URL` n'a **pas** de valeur par défaut dans le code (`core/ha_url.py`) : sans elle, aucun appel ne part (un
avertissement l'indique dans le journal). Attention : `.env.example` contient `HASS_URL=http://192.168.1.10:8123`, que
le script d'installation recopie ; **remplacez-la**. Les anciens noms `HA_URL` et `HA_TOKEN` sont acceptés en repli.

### 2.2 HTTPS et certificats

| Situation | Réglage |
|---|---|
| Certificat valide | rien (`HA_VERIFY_TLS=true` par défaut) |
| Certificat auto-signé | `HA_CA_BUNDLE=/chemin/vers/ca.pem` (**préférable**), ou `HA_VERIFY_TLS=false` (coupe la vérification) |
| Vous visez une IP, mais le certificat est émis pour un nom | `HA_TLS_SERVER_HOSTNAME=mon-domaine.example` (le moteur vérifie alors la chaîne **et** ce nom) |

### 2.3 Vérifier

```powershell
$h = @{ Authorization = "Bearer $cle" }     # $cle : voir 01_installer.md, § 7.3
Invoke-RestMethod http://127.0.0.1:8000/api/ha/health -Headers $h
```

```bash
curl -s -H "Authorization: Bearer $CLE" http://127.0.0.1:8000/api/ha/health
```

La réponse contient `url`, `token_configured`, `verify_tls`, `ca_bundle`, `tls_server_hostname`, `reachable`,
`latency_ms`, `version`, `entity_count`, `domains` et `error`. Sans jeton, **constaté** :
`"reachable": false` et `"error": "Token Home Assistant absent du .env du moteur (HASS_TOKEN/HA_TOKEN) — aucune requête
tentée."`. La réponse avec un vrai Home Assistant (version, nombre d'entités) n'a **pas** été vue : « — ».

Autres contrôles : l'écran **Avancé → Installation** (groupe « Home Assistant » : jeton, URL, joignabilité) ;
l'écran **Utiliser → Domotique & vocal** (« tester Home Assistant en direct », périmètre et historique des commandes) ;
`GET /api/ha/entities`, `GET /api/ha/state/{id}` et `POST /api/ha/control` (voir `/docs`, le corps exact n'est pas
reproduit ici).

---

## 3. Dire au moteur quoi piloter : `ha_commands.json`

Copiez le modèle puis **remplacez les `entity_id` par les vôtres** :

```powershell
Copy-Item ha_commands.json.example ha_commands.json
```

Le fichier est un catalogue de commandes (15 dans l'exemple) :

```json
{
  "commands": [
    {
      "phrases": ["allume le salon", "allume la lumiere du salon", "mets la lumière du salon"],
      "service": "light.turn_on",
      "entity_id": "light.living_room"
    },
    {
      "phrases": ["ouvre le volet du salon"],
      "service": "script.blind_action",
      "entity_id": "",
      "service_data": { "action": "open" }
    }
  ]
}
```

| Champ | Rôle |
|---|---|
| `phrases` | Phrases reconnues. Le fichier d'exemple précise qu'elles sont comparées « après normalisation du routeur (minuscules, sans accents finaux) » : comme l'exemple, écrivez chaque phrase avec et sans accent. |
| `service` | Service Home Assistant appelé (`domaine.service`). L'exemple en utilise sept : `light.turn_on` / `turn_off`, `climate.turn_on` / `turn_off`, `switch.turn_on` / `turn_off`, `script.blind_action`. |
| `entity_id` | Entité visée (peut être vide si le service n'en a pas besoin). |
| `service_data` | Données ajoutées à l'appel (facultatif). |

Règles à connaître :

- **Redémarrer** après modification : le fichier est lu à la création du routeur (`core/router.py:200-213`).
- Une phrase du catalogue est servie **sans modèle de langage** (110 à 196 ms côté moteur sur l'installation d'origine). Sinon le moteur tente un
  rapprochement approché, puis, selon le mode, un modèle : voir
  [`carte_decision_vocale.md`](../architecture/carte_decision_vocale.md).
- Le moteur **n'utilise pas** la liste « Exposer » de Home Assistant : ce qu'il a le droit de piloter se décide ici
  (`ihm-v2/src/lib/demoVocale.ts`, réglage « Changer ce que l'IA a le droit de piloter »).
- Sans `ha_commands.json`, le code retombe sur des tables de pièces intégrées (essayé : « allume la lumière du salon »
  est encore reconnue avec `light.living_room`). Ce repli est prévu pour l'installation d'origine : définissez votre propre
  catalogue.

---

## 4. Essayer sans rien actionner : la simulation

`POST /api/vocal/simulate` répond à la question « que ferait le moteur de cette phrase ? » **sans rien exécuter** :
aucun appel à Home Assistant, aucun appel à un modèle, aucune écriture (`api/routes/vocal_simulation.py`,
`services/vocal_simulation.py`). C'est l'essai à faire avant de connecter un vrai Home Assistant.

Corps : `texte` (1 à 500 caractères) et `mode` (`ha` ou `chat`, `ha` par défaut). **Sous Windows PowerShell 5.1, envoyez le
corps en octets UTF-8**, sinon une lettre accentuée fait échouer la requête (essayé : « There was an error parsing the
body ») :

```powershell
$corps = @{ texte = "allume la lumière du salon"; mode = "ha" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/vocal/simulate -Headers $h `
  -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($corps))
```

```bash
curl -s -X POST http://127.0.0.1:8000/api/vocal/simulate \
  -H "Authorization: Bearer $CLE" -H "Content-Type: application/json" \
  -d '{"texte":"allume la lumière du salon","mode":"ha"}'
```

Réponses **constatées** (`ha_commands.json` d'exemple, sans Home Assistant) :

| Texte | Mode | `chemin` | `action_prevue` | `llm_requis` |
|---|---|---|---|---|
| allume la lumière du salon | `ha` | `ha_command` | `light.turn_on` sur `light.living_room` | `false` |
| quelle heure est-il | `chat` | `discussion_datetime` | aucune | `false` |
| raconte-moi une blague | `chat` | `discussion_chat` | aucune | `true` |

Autres champs : `explication` (une phrase), `executerait_une_action`, `mode`, `simulation: true`. Quand le chemin dépend de
l'état de la maison (une question d'état), le chemin rendu est `indetermine` : la simulation ne lit pas Home Assistant.

L'interface propose la même chose dans **Découvrir → Comprendre le moteur → « Rejouer une vraie demande »** (rejeu
d'échanges déjà enregistrés).

---

## 5. Le composant `moteur_ia_conversation` (absent de ce dépôt)

Dans l'installation d'origine, c'est un composant personnalisé de Home Assistant. Le dépôt public n'en contient **pas** le
code (recherche du nom dans tout l'arbre : seuls deux textes de l'interface le citent,
`ihm-v2/src/lib/demoVocale.ts:51` et `glossaire.ts:157`). Je ne décris donc **aucune procédure d'installation** : elle serait
inventée.

Ce qu'il fait, d'après ces textes et le code de l'API : il reçoit de Home Assistant le texte reconnu, le transmet à
`POST /api/execute` avec un **mode** (`ha` pour « Domotique », `chat` pour « Discussion »), un délai de **30 s** (Domotique) ou
**35 s** (Discussion), l'en-tête `Authorization: Bearer <MOTEUR_API_KEY>`, puis rend le champ `response` à Home Assistant, qui
le fait lire par sa synthèse vocale. Le contrat côté moteur est celui de l'API (exemple de corps ; ce n'est pas la requête
exacte du composant d'origine, que je n'ai pas vue) :

```json
{
  "user_prompt": "allume la lumière du salon",
  "source": { "type": "voice", "mode": "ha", "tts_enabled": true, "device_id": "mon_satellite", "conversation_id": "..." }
}
```

| Si vous voulez… | Piste |
|---|---|
| Écrire votre propre agent de conversation | Un composant Home Assistant qui appelle `POST /api/execute` avec le contrat ci-dessus. Spécification : [`docs/API_REFERENCE.fr.md`](../API_REFERENCE.fr.md). |
| Essayer sans composant | Le moteur expose aussi `POST /v1/chat/completions` (compatible OpenAI). Si votre Home Assistant propose un agent de conversation compatible OpenAI avec une adresse personnalisée, c'est une piste. **Non essayée, non garantie.** |
| Tester le trajet sans voix | Utiliser le chat de Home Assistant (Assist, saisie au clavier) avec l'agent branché, ou la simulation du § 4. |

---

## 6. Le trajet de la démo : « Okay Nabu » → réponse

L'écran **Découvrir → Démo vocale & maison** détaille les huit étapes ; voici le résumé (`ihm-v2/src/lib/demoVocale.ts`) :

| N° | Étape | Où | Ce qui se passe |
|---|---|---|---|
| 1 | « Okay Nabu » | écran | L'écran reconnaît lui-même le mot de réveil : aucun son ne part avant. |
| 2 | Envoi de l'audio | écran → Home Assistant | Voix en 16 kHz, une voie. L'écran doit être connecté à Home Assistant. |
| 3 | Reconnaissance vocale | Home Assistant | Le pipeline Assist transcrit (STT). **Maillon le plus fragile.** |
| 4 | Agent de conversation | Home Assistant | Le composant du § 5 envoie le texte au moteur avec un mode. |
| 5 | Moteur : routage | moteur | Raccourcis dans un ordre fixe (commande, état, heure et météo), puis seulement un modèle. Le Planificateur n'est jamais atteint par la voix. |
| 6 | Maison ou modèle | Home Assistant ou fournisseur | Un ordre part vers la maison ; une question lit un état ; une discussion interroge la cascade. |
| 7 | Synthèse vocale | Home Assistant | La réponse devient une voix. |
| 8 | Lecture | écran | L'écran joue la voix, affiche le texte, se remet à écouter. |

Trois chemins côté moteur : **commande** (« Allume la lumière du salon »), **question** (« Quelle heure est-il ? », « Quelle
température fait-il dans le salon ? »), **discussion** (« Raconte-moi une blague »). La discussion est limitée à trois
phrases sans mise en forme, garde les cinq derniers tours, ne peut **pas** commander la maison (outils en lecture seule) et
répond « Désolé, je n'ai pas su répondre. Peux-tu reformuler ? » si tous les modèles échouent ou après 20 secondes.

Durées mesurées **sur l'installation d'origine** du projet, à titre d'ordre de grandeur (la vôtre sera différente) : une
commande prend 110 à 196 ms côté moteur ; une discussion environ 3 s en médiane ; le passage complet dans Home Assistant
va de 3 à 9 s, dont l'essentiel est la reconnaissance vocale. Mesures non reproduites pour ce guide : « — ».

> **La voix commande la vraie maison.** Un essai « pour voir » peut ouvrir un volet. Prévenez les personnes présentes, ou
> essayez d'abord avec des phrases qui ne commandent rien (« Quelle heure est-il ? »).

---

## 7. Le repli local : `deploy/ha_local_fallback/`

But : si le moteur est éteint, Home Assistant sert seul un **noyau vital** de commandes (lumières, volet, clim).

| Fichier | Rôle |
|---|---|
| `custom_sentences/fr/tab5_core.yaml` | Phrases → intents (**généré**) |
| `tab5_local_fallback_intents.yaml` | Intents → services Home Assistant (**généré**) |
| `tab5_engine_health_failover.yaml` | Sonde de `/healthz` + bascule automatique du pipeline |

Les deux fichiers générés sortent de `ha_commands.json` : ne les éditez jamais à la main, **régénérez**-les :

```bash
python3 -m scripts.gen_ha_local_fallback
```

(Non essayé dans ce guide ; commande du `README.md` du dossier. Les fichiers fournis contiennent les entités d'exemple
`climate.living_room`, `light.bedroom`… et doivent être regénérés avec les vôtres.)

Déploiement dans Home Assistant (étapes du `README.md` du dossier) :

1. Copier `tab5_core.yaml` dans `<config HA>/custom_sentences/fr/`.
2. Fusionner `tab5_local_fallback_intents.yaml` dans le bloc `intent_script:` de Home Assistant.
3. Déposer `tab5_engine_health_failover.yaml` dans `packages/` et **l'adapter** (§ ci-dessous).
4. Redémarrer Home Assistant ou recharger le YAML et les intents.

**À adapter dans `tab5_engine_health_failover.yaml`** :

- l'adresse du moteur : le fichier contient `http://${DECK_HOST:-192.168.1.x}:8000/healthz`. Cette forme **n'est pas valide
  dans le YAML de Home Assistant** (c'est un motif de substitution shell) : remplacez-la à la main par
  `http://<adresse de votre moteur>:8000/healthz` ;
- `PIPELINE_LOCAL` : le libellé **exact** de l'option « pipeline local » du `select` du satellite
  (`select.m5stack_tab5_home_assistant_hmi_assistant` dans le fichier ; adaptez-le à votre satellite).

Fonctionnement : la sonde REST interroge `/healthz` toutes les 30 s ; moteur `off` ou `unavailable` plus de 60 s → le
pipeline courant est mémorisé puis le satellite bascule sur l'agent local ; moteur `on` plus de 30 s → le pipeline préféré
est restauré. `/healthz` est publique (sans clé) : un code 200 suffit.

Le fichier renvoie à un `docs/voice_assistant.md` qui n'existe pas dans ce dépôt.

---

## 8. Dépannage de la voix

| Symptôme | Cause probable | Où regarder |
|---|---|---|
| Rien après « Okay Nabu » | Écoute coupée, écran déconnecté de Home Assistant, mot mal entendu | État de l'écran dans Home Assistant (capteur de connexion), journal de l'écran |
| « Erreur », le micro passe au rouge | Aucun texte reconnu (STT) | Débogage du pipeline dans Home Assistant ; rapprocher le micro |
| « Je n'ai pas compris la commande domotique. » | Mode Domotique : phrase absente du catalogue, sans verbe d'action reconnu | Simulation (§ 4), puis ajouter la phrase à `ha_commands.json` |
| « Impossible de contacter le moteur d'agents pour le moment. » | Moteur éteint, injoignable, ou délai de 30/35 s dépassé | `GET /healthz` ; journal du moteur |
| Erreur 403 côté Home Assistant | La clé enregistrée dans Home Assistant diffère de `MOTEUR_API_KEY` | Recopier la clé, recharger l'intégration |
| « Désolé, je n'ai pas su répondre. » | Discussion : tous les modèles ont échoué, ou 20 s dépassées | Cascade et quotas : [05 Exploiter, § 4](05_exploiter.md) |
| Réponse affichée mais sans son | Volume, haut-parleur, synthèse vocale en erreur | Volume de l'écran ; débogage du pipeline |
| Une question a actionné un appareil | Un texte mal reconnu ressemble à un ordre | Simulation du texte exact (§ 4) ; garde-fou « une question n'est pas un ordre » |

Ces symptômes viennent de l'écran de démo de l'interface (`ihm-v2/src/lib/demoVocale.ts`) et n'ont pas été reproduits avec un
vrai Home Assistant.

Journal des échanges vocaux : `GET /api/vocal/audit`, `/api/vocal/stats`, `/api/vocal/conversations` (texte, chemin pris,
latence). `GET /api/vocal/config` rend le modèle de l'agent domotique et la présence (pas la valeur) des secrets ; constaté sur
une installation sans clé : tous à `false`.
