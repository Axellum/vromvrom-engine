"""
tools/comptes.py — État des comptes LLM (soldes, quotas, dépense) pour les agents.

[#T320] Mesuré le 12/08/2026 : « Il me reste combien de crédit chez Anthropic et chez
Gemini ? » traversait tout le moteur — routage `accounts` (#T319), raccourci vers
l'Executor, dix tours de boucle ReAct, 99 secondes — pour finir par un
`mcp_filesystem_list_directory` sur la racine du dépôt. L'agent listait des fichiers
parce qu'**aucun des 23 outils enregistrés ne savait répondre à la question**.

La donnée existait pourtant déjà, à deux endroits, mais uniquement côté serveur :
  - `core.models_db.get_quota_summary()` — saturation et solde externe par clé d'API,
    alimenté par la boucle `core.quota_collector.quota_refresh_loop` ;
  - la table `token_usage` — la dépense réellement constatée, désormais complète
    depuis #T296/#T299/#T301/#T308/#T312.

Ce module ne collecte rien et n'appelle aucune API externe : il **lit** ce que le
moteur sait déjà et le met en français lisible par un agent. Pas de rafraîchissement
déclenché depuis un outil d'agent — un agent ne doit pas pouvoir provoquer une rafale
d'appels aux APIs de facturation des fournisseurs.
"""

import logging
import time

logger = logging.getLogger("tools.comptes")

# Au-delà, une clé est considérée comme sous tension et signalée en tête de réponse.
SEUIL_SATURATION_ALERTE = 80.0


def _formater_solde(entree: dict) -> str:
    """Une ligne lisible par clé d'API."""
    cle = entree.get("api_key_id", "?")
    provider = entree.get("provider_id", "?")
    projet = entree.get("project_name") or ""
    saturation = entree.get("saturation_pct")
    solde = entree.get("external_balance_usd")
    statut = entree.get("external_status") or "?"

    morceaux = [f"{cle} ({provider}"]
    if projet:
        morceaux.append(f", projet {projet}")
    morceaux.append(")")
    ligne = "".join(morceaux)

    details = []
    if solde is not None:
        details.append(f"solde {solde:.2f} $")
    if saturation is not None:
        details.append(f"saturation {saturation:.1f} %")
    details.append(f"état {statut}")
    return f"{ligne} — " + ", ".join(details)


def get_account_status(provider: str = "") -> str:
    """Donne les soldes, quotas et la dépense constatée des comptes LLM.

    Répond aux questions du type « combien me reste-t-il de crédit chez X ? »,
    « où en sont mes quotas ? », « combien ai-je dépensé ce mois-ci ? ».

    Args:
        provider: Filtre optionnel sur un fournisseur ou une clé (ex: 'anthropic',
                  'gemini', 'deepseek'). Vide = tous les comptes.

    Returns:
        Un état lisible : soldes et saturation par clé, puis la dépense réelle
        des 30 derniers jours par fournisseur, ou un message d'erreur explicite.
    """
    filtre = (provider or "").strip().lower()
    lignes: list[str] = []

    # ── 1. Soldes et quotas par clé d'API ────────────────────────────────────
    try:
        from core.models_db import get_quota_summary
        resume = get_quota_summary()
    except Exception as e:  # pragma: no cover - dépend de l'environnement
        logger.warning(f"[COMPTES] Quotas indisponibles : {e}")
        resume = {}

    cles = resume.get("keys") or []
    if filtre:
        cles = [
            k for k in cles
            if filtre in str(k.get("provider_id", "")).lower()
            or filtre in str(k.get("api_key_id", "")).lower()
        ]

    if cles:
        statut_global = resume.get("global_status", "?")
        lignes.append(f"💳 Comptes et quotas — état global : {statut_global} "
                      f"({len(cles)} clé(s) affichée(s) sur {resume.get('keys_count', len(cles))})")

        tendues = [
            k for k in cles
            if isinstance(k.get("saturation_pct"), (int, float))
            and k["saturation_pct"] >= SEUIL_SATURATION_ALERTE
        ]
        if tendues:
            lignes.append(f"⚠️ {len(tendues)} clé(s) au-dessus de "
                          f"{SEUIL_SATURATION_ALERTE:.0f} % de saturation :")
            lignes.extend(f"   • {_formater_solde(k)}" for k in tendues)

        lignes.append("Détail par clé :")
        lignes.extend(f"   • {_formater_solde(k)}" for k in cles)
    elif filtre:
        lignes.append(f"💳 Aucune clé d'API ne correspond à « {provider} ». "
                      f"Appelle cet outil sans argument pour voir tous les comptes.")
    else:
        lignes.append("💳 Aucune donnée de quota disponible "
                      "(la boucle de rafraîchissement n'a peut-être pas encore tourné).")

    # ── 2. Dépense réellement constatée (30 jours) ───────────────────────────
    # Source de vérité : token_usage, complète depuis #T296/#T299/#T301/#T308/#T312.
    try:
        from core.runtime_db import get_connection
        depuis = time.time() - 30 * 86400
        with get_connection() as conn:
            depenses = conn.execute(
                "SELECT model, SUM(cost_usd) AS cout, COUNT(*) AS appels "
                "FROM token_usage WHERE timestamp >= ? "
                "GROUP BY model HAVING cout > 0 ORDER BY cout DESC LIMIT 15",
                (depuis,),
            ).fetchall()
            total = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM token_usage WHERE timestamp >= ?",
                (depuis,),
            ).fetchone()[0]

        lignes.append("")
        lignes.append(f"💸 Dépense réelle sur 30 jours : {total:.4f} $")
        if depenses:
            lignes.append("Par modèle (payants seulement) :")
            for row in depenses:
                modele, cout, appels = row[0], row[1], row[2]
                lignes.append(f"   • {modele} — {cout:.4f} $ sur {appels} appel(s)")
        else:
            lignes.append("   Aucun appel payant sur la période "
                          "(tout est passé sur les tiers gratuits).")
    except Exception as e:  # pragma: no cover - dépend de l'environnement
        logger.warning(f"[COMPTES] Dépense indisponible : {e}")
        lignes.append("")
        lignes.append(f"💸 Dépense indisponible : {e}")

    return "\n".join(lignes)
