"""
services/vocal_simulation.py — « Que ferait le moteur de cette phrase ? » sans rien exécuter.

Rejoue la CASCADE de décision du vocal (mode domotique `ha` et mode discussion `chat`)
en n'appelant que les fonctions de reconnaissance PURES du moteur — celles qui, dans le
vrai chemin, tranchent avant tout appel :

  mode `ha`   (api/routes/agents.py : execute_chat)
     1. question d'état            match_ha_state_query
     2. réglage relatif de la clim match_climate_relative
     3. commande déterministe      match_ha_command
     4. question du quotidien      match_weather_query puis match_datetime_query
                                   (sauf si la phrase porte un verbe d'action domotique)
     5. sinon                      routeur du moteur (LLM / entité floue) : INDÉTERMINÉ

  mode `chat` (core/vocal_host.py : handle_discussion)
     1. commande déterministe  2. question d'état  3. météo  4. heure/date
     5. spécialistes web / agenda / fichiers / réponse longue, sinon bavardage LLM

GARANTIES (testées, voir tests/unit/test_vocal_simulation.py) :
  - aucun appel Home Assistant, aucun appel LLM, aucune écriture en base, aucun réseau ;
  - le journal vocal n'est PAS alimenté (la simulation n'est pas un échange) ;
  - quand le chemin réel dépend de l'état du système (volet en mouvement, entité
    reconnue par similarité, repli LLM…), le chemin vaut « indetermine » et les booléens
    inconnus valent `null` — la simulation ne prétend pas savoir.

Elle ne remplace pas un vrai essai : elle dit ce que la RECONNAISSANCE décide, pas ce que
Home Assistant ou un fournisseur répondra ensuite.
"""

from __future__ import annotations

MODES = ("ha", "chat")

CHEMIN_INDETERMINE = "indetermine"

_RIEN_EXECUTE = (
    " Simulation : rien n'a été exécuté, ni appel à Home Assistant, ni appel à un modèle."
)


def _action(service: str, entite: str | None, donnees: dict | None) -> dict:
    """Action que le chemin déclencherait. Un script porte sa cible dans `service` (entité vide)."""
    return {"service": service, "entite": entite or None, "donnees": donnees or None}


def _reponse(
    chemin: str,
    explication: str,
    *,
    action: dict | None = None,
    llm: bool | None = False,
    execute: bool | None = False,
    mode: str,
) -> dict:
    return {
        "chemin": chemin,
        "explication": explication + _RIEN_EXECUTE,
        "action_prevue": action,
        "llm_requis": llm,
        "executerait_une_action": execute,
        "mode": mode,
        "simulation": True,
    }


# ── Étapes communes aux deux modes (fonctions pures du moteur) ──────────────

def _commande(texte: str):
    """Commande domotique déterministe reconnue, ou None. Lit ha_commands.json (fichier local)."""
    from services.execute_service import match_ha_command

    return match_ha_command(texte)


def _volet_stop_depend_de_l_etat(commande, texte: str) -> bool:
    """
    `resolve_ha_command_for_execute` abandonne un « stop » SANS le mot « volet » si le volet
    n'est pas en mouvement — un état lu dans Home Assistant. Même test que le vrai chemin.
    """
    from services.execute_service import _VOLET_SCRIPT, normalize_ha_command_prompt

    return (
        commande.service == _VOLET_SCRIPT
        and (commande.service_data or {}).get("action") == "stop"
        and "volet" not in normalize_ha_command_prompt(texte)
    )


def _etape_commande(texte: str, mode: str, chemin: str) -> dict | None:
    commande = _commande(texte)
    if commande is None:
        return None
    action = _action(commande.service, commande.entity_id, commande.service_data)
    if _volet_stop_depend_de_l_etat(commande, texte):
        return _reponse(
            CHEMIN_INDETERMINE,
            "La phrase ressemble à un « stop » du volet. Le moteur ne l'exécute que si le volet est "
            "réellement en mouvement, ce qu'il lit dans Home Assistant : le chemin dépend de l'état "
            "de la maison et ne peut pas être simulé sans l'interroger.",
            action=action, llm=False, execute=None, mode=mode,
        )
    return _reponse(
        chemin,
        f"Commande domotique reconnue sans modèle de langage (correspondance « {commande.matched_phrase} »). "
        "Le moteur appellerait directement le service Home Assistant ci-dessous, puis répondrait par une "
        "phrase toute faite.",
        action=action, llm=False, execute=True, mode=mode,
    )


def _etape_etat(texte: str, mode: str, chemin: str) -> dict | None:
    from services.ha_state_query import match_ha_state_query

    question = match_ha_state_query(texte)
    if question is None:
        return None
    return _reponse(
        chemin,
        f"Question sur l'état de la maison reconnue (type « {question.kind} », entité {question.entity_id}). "
        "Le moteur lirait cet état dans Home Assistant et formulerait la réponse sans modèle de langage ; "
        "il ne modifie rien. Si Home Assistant ne répond pas, la phrase continuerait vers les étapes suivantes.",
        llm=False, execute=False, mode=mode,
    )


def _etape_meteo(texte: str, mode: str, chemin: str) -> dict | None:
    from services.ha_weather_query import match_weather_query

    meteo = match_weather_query(texte)
    if meteo is None:
        return None
    return _reponse(
        chemin,
        f"Question météo reconnue (horizon : {meteo.horizon.value}). Le moteur lirait l'entité météo de la "
        "maison dans Home Assistant et répondrait sans modèle de langage. Si la prévision demandée est "
        "absente, la phrase continuerait vers les étapes suivantes.",
        llm=False, execute=False, mode=mode,
    )


def _etape_heure(texte: str, mode: str, chemin: str) -> dict | None:
    from services.datetime_query import match_datetime_query

    genre = match_datetime_query(texte)
    if genre is None:
        return None
    quoi = "l'heure" if genre == "time" else "la date"
    return _reponse(
        chemin,
        f"Question sur {quoi} reconnue. Le moteur la calcule avec l'horloge de la machine, sans modèle de "
        "langage ni Home Assistant.",
        llm=False, execute=False, mode=mode,
    )


# ── Mode domotique ─────────────────────────────────────────────────────────

def _simuler_domotique(texte: str) -> dict:
    mode = "ha"
    reponse = _etape_etat(texte, mode, "ha_state_query")
    if reponse:
        return reponse

    from services.execute_service import normalize_ha_command_prompt, resolve_climate_entity
    from services.ha_climate_control import match_climate_relative

    relatif = match_climate_relative(texte)
    if relatif is not None:
        entite = resolve_climate_entity(normalize_ha_command_prompt(texte))
        sens = "plus chaud" if relatif.delta > 0 else "plus froid"
        return _reponse(
            "ha_climate_relative",
            f"Réglage RELATIF de la climatisation reconnu ({sens} de {abs(relatif.delta)} °C). Le moteur lirait "
            "la consigne actuelle dans Home Assistant, ajouterait l'écart, puis réglerait la climatisation : la "
            "température visée dépend donc de la consigne du moment (inconnue ici).",
            action=_action("climate.set_temperature", entite, None),
            llm=False, execute=True, mode=mode,
        )

    reponse = _etape_commande(texte, mode, "ha_command")
    if reponse:
        return reponse

    from services.execute_service import prompt_has_domotic_action

    if not prompt_has_domotic_action(texte):
        reponse = _etape_meteo(texte, mode, "ha_weather") or _etape_heure(texte, mode, "ha_datetime")
        if reponse:
            return reponse

    return _reponse(
        CHEMIN_INDETERMINE,
        "Aucun raccourci sans modèle de langage ne reconnaît cette phrase. En mode domotique elle passerait "
        "par le routeur du moteur, qui peut encore la rattacher à une entité par correspondance floue (selon "
        "les entités connues de Home Assistant), puis, à défaut, la confier à un modèle domotique. Le chemin "
        "exact dépend de l'état du système et ne peut pas être simulé sans l'exécuter.",
        llm=None, execute=None, mode=mode,
    )


# ── Mode discussion ────────────────────────────────────────────────────────

def _simuler_discussion(texte: str) -> dict:
    mode = "chat"
    for etape, chemin in (
        (_etape_commande, "discussion_ha_command"),
        (_etape_etat, "discussion_ha_state"),
        (_etape_meteo, "discussion_ha_weather"),
        (_etape_heure, "discussion_datetime"),
    ):
        reponse = etape(texte, mode, chemin)
        if reponse:
            return reponse

    from core.vocal_host import VocalIntent, classify_vocal_intent

    intention, _score = classify_vocal_intent(texte)
    if intention in (VocalIntent.WEB, VocalIntent.CALENDAR):
        quoi = "une recherche sur le web" if intention == VocalIntent.WEB else "l'agenda Google"
        return _reponse(
            f"vocal_host_{intention.value}",
            f"Aucun raccourci local ne reconnaît la phrase ; elle relève de {quoi}. Le moteur la confierait à un "
            "modèle spécialisé (appel à un fournisseur LLM). Il ne commande aucun appareil.",
            llm=True, execute=False, mode=mode,
        )
    if intention in (VocalIntent.FILES, VocalIntent.DEEP):
        quoi = "une recherche dans les fichiers" if intention == VocalIntent.FILES else "une réponse approfondie"
        return _reponse(
            f"vocal_host_{intention.value}_async",
            f"Aucun raccourci local ne reconnaît la phrase ; elle demande {quoi}. Le moteur répondrait tout de "
            "suite « je te préviens quand c'est prêt » et lancerait la tâche en arrière-plan avec un modèle. Il "
            "ne commande aucun appareil.",
            llm=True, execute=False, mode=mode,
        )
    return _reponse(
        "discussion_chat",
        "Aucun raccourci local ne reconnaît la phrase : c'est de la conversation. Le moteur la confierait à un "
        "modèle de langage (cascade des modèles rapides). Les outils qu'on lui donne ne font que lire (liste "
        "et état des entités, mémoire) : il ne peut commander aucun appareil.",
        llm=True, execute=False, mode=mode,
    )


def simuler(texte: str, mode: str) -> dict:
    """
    Simule le vocal pour `texte`. `mode` : « ha » (domotique) ou « chat » (discussion).

    Pure : aucune entrée-sortie réseau, aucune base, aucun LLM (voir le module).
    """
    texte = (texte or "").strip()
    if mode == "chat":
        return _simuler_discussion(texte)
    if mode == "ha":
        return _simuler_domotique(texte)
    raise ValueError(f"Mode inconnu : {mode!r} (attendu : {', '.join(MODES)})")
