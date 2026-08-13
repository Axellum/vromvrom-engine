"""
Une session en échec ne doit plus s'annoncer « ✅ Tâche terminée. » (#T328).

Mesuré en PRODUCTION le 12/08, session `chat_683ea8b2cd` — « Quelle est la
température actuelle dans le salon ? » :

    09:23:10  [ha_agent] Erreur d'outil détectée de call_api : Connection aborted,
              RemoteDisconnected('Remote end closed connection without response')
    09:23:10  [ha_agent] Échec final de la tâche après auto-correction infructueuse
    09:23:10  [ERROR] core.engine - Erreur depuis ha_agent
    09:23:11  [CHECKPOINT] État sauvegardé (phase: failed)

… et l'utilisateur reçoit, après 60,9 secondes : « ✅ Tâche terminée. »

Les trois sorties du pipeline mentaient de concert :
  - `record_session_end(session_id, "success")` — statut EN DUR ;
  - `"status": "completed"` — EN DUR ;
  - `"response": response_text or "✅ Tâche terminée."` — repli sur réponse vide.

Double dégât : l'utilisateur croit sa demande satisfaite, **et** la base
enregistre un succès. C'est ainsi que s'accumulent des « tâches réussies » sans
coût ni résultat dans les métriques (#T325 : 88 succès attribués à `unknown`).

Même famille que #T286 (« le harnais comptait une réponse vide comme un
succès »), mais côté serveur — donc visible par l'utilisateur final, et par
WhatsApp, qui teste `status == "completed"` pour afficher « ✅ Résultat ».
"""

from services.pipeline_service import detecter_echec_sans_resultat


def _tache(status: str, result_data=None, error_message=None) -> dict:
    """Entrée d'historique au format réel de `state_update_to_dict`."""
    return {
        "agent_name": "ha_agent",
        "status": status,
        "result_data": result_data,
        "next_agent": "END",
        "error_message": error_message,
    }


def test_le_cas_mesure_est_signale_en_echec():
    """Tout a échoué, rien n'a été produit → la session doit se déclarer en échec."""
    historique = [_tache(
        "error",
        error_message="Erreur dans l'outil 'call_api' : Erreur de réseau HTTP: "
                      "('Connection aborted.', RemoteDisconnected(...))",
    )]

    erreur = detecter_echec_sans_resultat(historique, results=[])

    assert erreur is not None, "l'échec serait annoncé « ✅ Tâche terminée. »"
    assert "call_api" in erreur


def test_session_reussie_reste_reussie():
    """Non-régression : un résultat exploitable = succès, quoi qu'il y ait à côté."""
    resultat = _tache("success", result_data="Il fait 22,4 °C dans le salon.")

    assert detecter_echec_sans_resultat([resultat], results=[resultat]) is None


def test_succes_partiel_n_est_pas_un_echec():
    """Une tâche échouée parmi d'autres ne doit pas effacer ce qui a abouti."""
    ok = _tache("success", result_data="Volet fermé.")
    ko = _tache("error", error_message="timeout sur la clim")

    assert detecter_echec_sans_resultat([ok, ko], results=[ok]) is None


def test_derniere_erreur_retenue():
    """Sur plusieurs échecs, c'est la dernière erreur qui est remontée."""
    historique = [
        _tache("error", error_message="première panne"),
        _tache("error", error_message="dernière panne"),
    ]

    assert detecter_echec_sans_resultat(historique, results=[]) == "dernière panne"


def test_echec_sans_message_reste_signale():
    """Un échec muet doit quand même produire un verdict d'échec, pas un succès."""
    historique = [_tache("error")]

    erreur = detecter_echec_sans_resultat(historique, results=[])

    assert erreur is not None
    assert "aucun détail" in erreur


def test_error_message_seul_suffit_a_marquer_l_echec():
    """Un statut resté à « success » mais porteur d'une erreur ne trompe pas le verdict."""
    historique = [_tache("success", error_message="échec silencieux de l'outil")]

    assert detecter_echec_sans_resultat(historique, results=[]) == "échec silencieux de l'outil"


def test_historique_vide_ne_fabrique_pas_d_echec():
    """Aucune tâche, aucune erreur : ce n'est pas à cette fonction de trancher."""
    assert detecter_echec_sans_resultat([], results=[]) is None
