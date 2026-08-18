"""
[17/08] Banc de régression croisé — six correctifs livrés en prod (commit 6b7fcb8).

Ce fichier fige ENSEMBLE les décisions des six correctifs mergés le 17/08. Sa
valeur n'est pas de redoubler les tests unitaires de chaque correctif (ils
existent déjà) mais de CROISER les chemins : chaque phrase réelle y est jugée
par les quatre décideurs à la fois, pour verrouiller qu'un correctif n'en a pas
cassé un autre.

    #T358  services/ha_state_query._est_question_generale
    #T363  services.execute_service.match_ha_command        (garde-fou « question ≠ ordre »)
    #T364  services.ha_state_query.match_ha_state_query     (le résolveur ne devine plus)
    #T365  core.vocal_host.classify_vocal_intent            (_CALENDAR_MARKERS)
    #T366  core.vocal_tts_cache.sanitize_discussion_tts     (LaTeX → « la formule »)
    #T367  services.ha_weather_query.match_weather_query    (météo depuis l'entité HA)

Règles du banc :
- On teste la DÉCISION, jamais l'effet. `match_ha_command` est un pur matcher en
  dry-run : il retourne un `HACommandMatch` sans rien exécuter (l'exécution
  appartient à `resolve_ha_command_for_execute` et n'est jamais appelée ici).
  Aucun test n'allume, n'éteint ni ne lit rien.
- Cas non résolu figé en `xfail` (#T271) : la transcription STT est trop
  dégradée pour être reconnue par quoi que ce soit — le jour où elle le sera,
  l'XPASS strict le signalera.
"""

import pytest

from core.vocal_host import VocalIntent, classify_vocal_intent
from core.vocal_stt_normalize import normalize_vocal_stt
from core.vocal_tts_cache import sanitize_discussion_tts
from services.execute_service import match_ha_command, normalize_ha_command_prompt
from services.ha_state_query import _est_question_generale, match_ha_state_query
from services.ha_weather_query import WeatherHorizon, match_weather_query

# ─────────────────────────────────────────────────────────────────────
# Table croisée : une ligne = une phrase réelle → les QUATRE décisions.
# Colonnes : (phrase, tâche d'origine, commande, état, météo, intention).
# « commande » est le service HA attendu (None = aucune commande) ; « état »
# le kind de lecture d'état (None = aucune) ; « météo » l'horizon (None =
# aucune) ; « intention » le verdict de classify_vocal_intent.
# ─────────────────────────────────────────────────────────────────────

_CAS = (
    # [#T358] Question de culture générale contenant un nom d'appareil : elle
    # rendait « Le volet est partiellement ouvert. » en 58 ms. Aujourd'hui elle
    # ne déclenche RIEN de domotique : elle poursuit jusqu'au chat.
    ("en une phrase, c'est quoi un volet roulant ?", "#T358",
     None, None, None, VocalIntent.CHAT),
    # [#T363] Questions réelles qui actionnaient la maison : la première
    # déclenchait light.turn_on, la seconde light.turn_off (fuzzy 0,82). Elles
    # restent des LECTURES d'état, jamais des commandes — ni une météo.
    ("est-ce que la lumière du salon est allumée ?", "#T363",
     None, "light", None, VocalIntent.CHAT),
    ("État de la lumière de la chambre.", "#T363",
     None, "light", None, VocalIntent.CHAT),
    # [#T363] Subtilité vérifiée : « est-elle » est réécrit en « éteins » par le
    # STT (normalize_vocal_stt), mais le garde-fou juge le prompt BRUT : avec le
    # point d'interrogation, c'est une question → aucune commande. Sans « ? »,
    # la transcription dégradée garde sa lecture d'ordre (voir test dédié).
    ("Est-elle la lumière de la chambre ?", "#T363",
     None, None, None, VocalIntent.CHAT),
    # [#T364] « Combien fait 17 fois 24 ? » rendait « Il fait 22 degrés dans le
    # salon. » en 38 ms : « combien » seul ne suffit plus à déclencher une
    # température.
    ("Combien fait 17 fois 24 ?", "#T364",
     None, None, None, VocalIntent.CHAT),
    # [#T371] La chambre A un capteur (`sensor.bedroom_temperature`).
    # #T364 l'avait figée à `None` (pièce « sans capteur ») : c'était faux.
    # Elle répond désormais, avec SON capteur, pas celui du salon.
    ("il fait combien dans la chambre ?", "#T371",
     None, "temperature", None, VocalIntent.WEB),
    # [#T364] « Quel est la température du salon ? » (STT abîmé) répondait sur
    # la LUMIÈRE du salon. Aujourd'hui : lecture de température, point.
    ("quel est la temperature du salon ?", "#T364",
     None, "temperature", None, VocalIntent.CHAT),
    # [#T367] L'intérieur n'est PAS la météo : le classifieur rapide voit
    # « il fait combien » → WEB (score 0,5), mais match_weather_query refuse la
    # pièce nommée et la lecture d'état intérieure gagne (étape 1b avant 1c
    # dans handle_discussion). L'intention WEB seule ne fait pas la météo.
    ("il fait combien dans le salon ?", "#T367",
     None, "temperature", None, VocalIntent.WEB),
    # [#T367] Les vraies demandes météo passent par l'entité HA de la maison.
    ("il fait combien dehors", "#T367",
     None, None, WeatherHorizon.NOW, VocalIntent.WEB),
    ("quel temps fait-il aujourd'hui ?", "#T367",
     None, None, WeatherHorizon.TODAY, VocalIntent.WEB),
    ("est-ce qu'il va pleuvoir demain ?", "#T367",
     None, None, WeatherHorizon.TOMORROW, VocalIntent.WEB),
    # [#T365] Le champ horaire du travail atteint le calendrier — formulations
    # accentuées telles que le STT les produit (les tests d'origine, écrits sans
    # accent, ont laissé passer le bug des marqueurs non accentués, #T383).
    ("À quelle heure je commence à travailler demain ?", "#T365",
     None, None, None, VocalIntent.CALENDAR),
    ("je finis à quelle heure demain ?", "#T365",
     None, None, None, VocalIntent.CALENDAR),
    ("c'est quand mon prochain jour de repos ?", "#T365",
     None, None, None, VocalIntent.CALENDAR),
    ("j'ai quoi de prévu ce week-end ?", "#T365",
     None, None, None, VocalIntent.CALENDAR),
    ("quel est mon planning de la semaine ?", "#T365",
     None, None, None, VocalIntent.CALENDAR),
    # [#T365] Garde-fou des marqueurs longs : « je commence à comprendre » reste
    # du chat, il ne bascule pas au calendrier.
    ("je commence à comprendre la relativité", "#T365",
     None, None, None, VocalIntent.CHAT),
    # [#T363] L'ordre explicite prime toujours sur la forme interrogative :
    # l'infinitif d'action fait de la question un ordre poli. Dry-run : le match
    # retourné n'est JAMAIS exécuté.
    ("Peux-tu allumer la lumière du salon ?", "#T363",
     "light.turn_on", None, None, VocalIntent.CHAT),
    ("remonte le volet", "#T363",
     "script.blind_action", None, None, VocalIntent.CHAT),
    # [#T363] Sans point d'interrogation, « est-elle » (transcription STT
    # dégradée d'« éteins ») garde sa lecture d'ordre — le « ? » est ce qui
    # tranche entre question et commande.
    ("Est-elle la lumière de la chambre", "#T363",
     "light.turn_off", None, None, VocalIntent.CHAT),
)

_IDS_CAS = [
    f"{tache} | {phrase[:44]}" for phrase, tache, *_ in _CAS
]


@pytest.mark.parametrize("phrase,tache,cmd,etat,meteo,intent", _CAS, ids=_IDS_CAS)
def test_chaque_phrase_prend_la_bonne_decision(phrase, tache, cmd, etat, meteo, intent):
    """La phrase aboutit à la décision attendue — et à AUCUNE autre.

    Chaque ligne croise les quatre décideurs : un correctif qui déborde sur le
    chemin d'un autre fait échouer sa ligne.
    """
    c = match_ha_command(phrase)
    assert (c.service if c else None) == cmd, (
        f"[{tache}] {phrase!r} → commande {c.service if c else None!r}"
    )
    e = match_ha_state_query(phrase)
    assert (e.kind if e else None) == etat, (
        f"[{tache}] {phrase!r} → lecture d'état {e.kind if e else None!r}"
    )
    m = match_weather_query(phrase)
    assert (m.horizon.value if m else None) == meteo, (
        f"[{tache}] {phrase!r} → météo {m.horizon.value if m else None!r}"
    )
    i, score = classify_vocal_intent(phrase)
    assert i == intent, f"[{tache}] {phrase!r} → intention {i.name} (score {score})"


# ─────────────────────────────────────────────────────────────────────
# [#T358] _est_question_generale, testée directement (même appel que
# match_ha_state_query : texte normalisé + ensemble de mots).
# ─────────────────────────────────────────────────────────────────────

def _norm(prompt: str) -> tuple[str, set[str]]:
    """Même normalisation que match_ha_state_query (l. 149-153)."""
    norm = normalize_ha_command_prompt(prompt)
    return norm, set(norm.split())


@pytest.mark.parametrize("phrase", [
    # Phrase exacte mesurée en prod (réponse « Le volet est partiellement
    # ouvert. » en 58 ms, deux fois de suite) et ses voisines généralisables.
    "en une phrase, c'est quoi un volet roulant ?",
    "explique-moi comment marche une ampoule LED",
    "comment fonctionne une clim ?",
    "a quoi sert un thermostat ?",
    "quelle difference entre un store et un volet ?",
])
def test_t358_question_de_culture_generale_detectee(phrase):
    norm, word_set = _norm(phrase)
    assert _est_question_generale(norm, word_set) is True


@pytest.mark.parametrize("phrase", [
    # L'adjectif d'état tranche en faveur de l'installation, même avec un
    # article indéfini : ce sont des lectures d'état, pas des définitions.
    "est-ce que la lumiere du salon est allumee ?",
    "le volet est ouvert ?",
    "la clim est allumee ?",
])
def test_t358_la_question_detat_reelle_nest_pas_generale(phrase):
    norm, word_set = _norm(phrase)
    assert _est_question_generale(norm, word_set) is False


# ─────────────────────────────────────────────────────────────────────
# [#T363] Subtilité du garde-fou : le prompt BRUT, jamais le normalisé.
# ─────────────────────────────────────────────────────────────────────

def test_t363_le_garde_fou_juge_le_brut_pas_le_normalise():
    """normalize_vocal_stt réécrit « est-elle » en « éteins » (vocal_stt_normalize
    l. 38) : sur le texte normalisé, la question devient littéralement un ordre
    d'extinction. Si le garde-fou jugeait ce texte-là, il ne verrait plus jamais
    la forme interrogative — d'où le jugement sur le brut, point d'entrée du
    correctif #T363.
    """
    brut = "Est-elle la lumiere de la chambre ?"
    assert "eteins" in normalize_vocal_stt(brut)   # le STT a réécrit la question
    assert match_ha_command(brut) is None          # mais la question ne commande pas


def test_t363_sans_point_d_interrogation_la_transcription_degradee_commande():
    """Sans « ? », la lecture « ordre » est conservée : on ne perd pas de vraies
    commandes mal transcrites. C'est le point d'interrogation qui tranche.
    """
    cmd = match_ha_command("Est-elle la lumiere de la chambre")
    assert cmd is not None and cmd.service == "light.turn_off"


# ─────────────────────────────────────────────────────────────────────
# [#T364] Le résolveur ne devine plus : l'entité visée est exacte.
# ─────────────────────────────────────────────────────────────────────

def test_t364_la_temperature_du_salon_ne_repond_pas_sur_la_lumiere():
    """« Quel est la température du salon ? » répond la température du salon —
    plus jamais « La lumière du salon est allumée. » (branche lumière, #T364)."""
    q = match_ha_state_query("quel est la temperature du salon ?")
    assert q is not None and q.kind == "temperature"
    assert q.entity_id == "climate.living_room"


# ─────────────────────────────────────────────────────────────────────
# [#T365] Les marqueurs accentués — le point que les tests d'origine
# (écrits sans accent) ont laissé passer, corrigé par #T383.
# ─────────────────────────────────────────────────────────────────────

def test_t365_les_formulations_accentuees_du_stt_atteignent_le_calendrier():
    """Le STT produit des accents ; _normalize_prompt les conserve. Les marqueurs
    doivent exister dans les deux orthographes, faute de quoi ils ne matchent
    rien en production (bug #T383, invisible dans les tests non accentués)."""
    for phrase in (
        "À quelle heure je commence à travailler demain ?",
        "je finis à quelle heure demain ?",
        "je termine à quelle heure ?",
        "c'est quand mon prochain jour de repos ?",
        "j'ai quoi de prévu ce week-end ?",
        "quel est mon planning de la semaine ?",
    ):
        intent, score = classify_vocal_intent(phrase)
        assert intent == VocalIntent.CALENDAR, phrase
        assert score > 0, phrase


# ─────────────────────────────────────────────────────────────────────
# [#T366] Le LaTeX ne part plus à la synthèse vocale.
# ─────────────────────────────────────────────────────────────────────

def test_t366_le_latex_devient_la_formule_lisible_tts():
    """Formule exacte rendue le 17/08 pour « donne-moi la formule de la
    relativité générale » : le TTS l'ânonnait symbole par symbole."""
    formule = (
        r"L'équation s'écrit \(G_{\mu\nu} + \Lambda\,g_{\mu\nu} = "
        r"\frac{8\pi G}{c^{4}}\,T_{\mu\nu}\) et c'est tout."
    )
    out = sanitize_discussion_tts(formule)
    assert "la formule" in out
    assert "\\" not in out, "aucune commande LaTeX résiduelle ne doit être lue"


def test_t366_les_prix_en_dollars_survivent_au_nettoyage():
    """« $…$ » n'est neutralisé que s'il contient une marque LaTeX : un prix ne
    doit jamais être avalé."""
    assert "5 $" in sanitize_discussion_tts("Ce café coûte 5 $, c'est raisonnable.")
    assert "10 $ et 20 $" in sanitize_discussion_tts(
        "Compte entre 10 $ et 20 $ pour le trajet."
    )


# ─────────────────────────────────────────────────────────────────────
# [#T367] « il fait combien dans le salon ? » n'est PAS une météo —
# croisement explicite avec la lecture d'état intérieure (#T364).
# ─────────────────────────────────────────────────────────────────────

def test_t367_la_question_interieure_nest_pas_une_meteo():
    """La pièce nommée rend la main à la température intérieure : aucune météo
    ne doit partir, la lecture d'état reste la décision."""
    assert match_weather_query("il fait combien dans le salon ?") is None
    q = match_ha_state_query("il fait combien dans le salon ?")
    assert q is not None and q.kind == "temperature"


# ─────────────────────────────────────────────────────────────────────
# [#T271] Cas NON résolu, figé comme tel : la transcription STT dégradée
# n'est reconnue par aucun chemin. xfail strict — pas skip, pas d'omission.
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.xfail(
    strict=True,
    reason=(
        "#T271 : « C'est à qui l'heure que j'en boche ? » n'est reconnu par aucun "
        "chemin — la transcription STT est trop dégradée pour qu'un marqueur en "
        "sorte (vérifié le 17/08 : commande, état, météo, calendrier → tous None). "
        "Tant que rien ne la reconnaît, ce test échoue (xfail attendu) ; le jour "
        "où le STT ou le moteur la reconnaît, il passe et l'XPASS strict alerte "
        "pour retirer le xfail."
    ),
)
def test_t271_la_transcription_degradee_sera_reconnue_un_jour():
    """Aujourd'hui, aucune décision n'est prise : la phrase part au chat, qui ne
    peut pas deviner une heure de travail dans ce bruit. Ce n'est pas acceptable
    pour toujours — le test documente l'ambition, pas la résignation."""
    phrase = "C'est à qui l'heure que j'en boche ?"
    assert any((
        match_ha_command(phrase) is not None,
        match_ha_state_query(phrase) is not None,
        match_weather_query(phrase) is not None,
        classify_vocal_intent(phrase)[0] != VocalIntent.CHAT,
    ))
