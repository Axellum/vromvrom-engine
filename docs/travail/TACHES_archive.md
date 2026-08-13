# Archive des tâches moteur — froid

Ne pas lire au boot d'une session. Consulter seulement pour « est-ce que #Txxx est déjà clos ? »

Clôtures **antérieures au 12/08/2026** (158 tâches, board commun) :
`H:\AuxFilsDesIdees\contexte_ia\04_Projets\TACHES_terminees.md`

À partir de #T340, les clôtures **moteur** s'archivent ici, dans la même PR que le fix.

## Format

`- ✅ #Tid — zone — date — PR #n — une ligne de preuve`

## 13/08/2026

- ✅ #T342 — docs — 12/08 — PR #291 — kit `docs/travail/` + `prompts/agents/` dans le dépôt (`4681959`)
- ✅ #T340 — outils — 12/08 — PR #290 — token HA configuré prime sur l'en-tête inventé par le modèle
- ✅ #T325 — observabilité — 12/08 — PR #293 — `/api/metrics/cost-per-success` par session (prod 13/08 : 406 succès, $0.00027/succès)
- ✅ #T341 — agents — 12/08 — PR #292 — borne 20 appels/tour + dédup ; description `get_account_status` corrigée
- ✅ #T330 — config — 12/08 — PR #294 — helper `get_ha_url`, plus d'URL HA en dur
- ✅ #T306 — proxy — 12/08 — PR #296 — `/v1` transmet `session_id` ; `agent_courant("proxy_v1")` sur le chemin bufferisé
- ✅ #T334 — quotas — 12/08 — PR #295 — alerte soldes/quotas + hystérésis ; cycle prod 13/08 : 0 clé sous tension, DeepSeek $13.90
