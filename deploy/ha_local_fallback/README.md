# Noyau de secours local + auto-bascule pipeline vocal

But : garder **deux agents vocaux** (local + moteur/cloud) sans les inconvénients.
Le moteur reste la **source de vérité** domotique ; l'agent local n'assure qu'un
**noyau vital** de commandes, servi même quand le moteur (Steam Deck) est éteint.

## Contenu

| Fichier | Rôle |
|---------|------|
| `custom_sentences/fr/tab5_core.yaml` | Phrases → intents (GÉNÉRÉ) |
| `tab5_local_fallback_intents.yaml` | `intent_script` : intents → services HA (GÉNÉRÉ) |
| `tab5_engine_health_failover.yaml` | Sonde `/healthz` + bascule auto du pipeline |

## Source de vérité unique

Les deux fichiers `GÉNÉRÉ` sortent de **`ha_commands.json`** (moteur) via :

```bash
python3 -m scripts.gen_ha_local_fallback
```

Ne **jamais** les éditer à la main : régénérer après toute modif de
`ha_commands.json`. Ainsi le noyau local ne diverge jamais du moteur. Seul le
sous-ensemble VITAL est exporté (lumières on/off, volet open/close/stop, clim
on/off) ; les fonctions riches (clim température/mode, lecture d'état,
discussion) restent au moteur uniquement.

## Déploiement HA (Freebox)

1. Copier `custom_sentences/fr/tab5_core.yaml` dans
   `<config_HA>/custom_sentences/fr/tab5_core.yaml`.
2. Fusionner `tab5_local_fallback_intents.yaml` dans le bloc `intent_script:`
   (ou l'inclure).
3. Déposer `tab5_engine_health_failover.yaml` dans `packages/` et **adapter** :
   - `MOTEUR_URL` (IP:port du Deck, défaut `192.168.1.x:8000`) ;
   - `PIPELINE_LOCAL` = libellé exact de l'option « pipeline local » du
     `select.m5stack_tab5_home_assistant_hmi_assistant`.
4. Redémarrer HA (ou recharger YAML + intents).

## Fonctionnement de la bascule

- Sonde REST `binary_sensor.tab5_engine_online` ping `/healthz` toutes les 30 s.
- Moteur `off`/`unavailable` > 60 s → mémorise le pipeline courant puis bascule
  le satellite sur l'agent LOCAL.
- Moteur `on` > 30 s → restaure le pipeline préféré.

La sonde `/healthz` est **publique** (sans `MOTEUR_API_KEY`) : répondre 200
suffit à prouver que le moteur est vivant.
