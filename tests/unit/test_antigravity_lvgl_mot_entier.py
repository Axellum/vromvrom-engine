"""
tests/unit/test_antigravity_lvgl_mot_entier.py — « ui » n'est plus une sous-chaîne (#T317).

`AntigravityAgent.invoke()` injectait les templates LVGL Premium dès que l'objectif
contenait l'un de ses mots-clés, cherchés avec `kw in objective_lower`. Or **« ui » est
une sous-chaîne d'une bonne partie du français courant** : « qui », « aujourd'hui »,
« celui », « lui », « puis », « suis », « depuis », « produit »…

Autrement dit, la quasi-totalité des objectifs déclenchaient l'injection.

Sans conséquence visible aujourd'hui : `docs/LVGL_PREMIUM_TEMPLATES.md` a été supprimé
du dépôt, donc `_load_lvgl_templates()` rend une chaîne vide et l'injection est un
no-op. Le jour où ce fichier revient, chaque tâche contenant « qui » paie un contexte
de design qu'elle n'a pas demandé — c'est le mécanisme exact qui poussait la cascade
vers le tier fort payant (#T295).

Découvert le 12/08/2026 en cherchant l'origine d'un prompt de 289 966 tokens. Ce
n'était pas la cause (cf. #T314), mais le défaut est réel et se corrige en une ligne.
"""

import pytest

from agents.antigravity_agent import AntigravityAgent


def concerne(objectif) -> bool:
    """Accès indirect : sur master la méthode n'existe pas encore, et on veut que
    l'échec porte sur le comportement testé, pas sur la collecte du module.

    Le repli reproduit la détection de master (sous-chaîne) pour que les tests de
    faux positifs échouent en disant *ce qui* est faux, et non « attribut absent ».
    """
    fonction = getattr(AntigravityAgent, "_objectif_concerne_lvgl", None)
    if fonction is None:  # master
        mots = ["lvgl", "ecran", "écran", "ui", "design", "layout", "widget", "dashboard"]
        return any(kw in (objectif or "").lower() for kw in mots)
    return fonction(objectif)


# ── Les faux positifs du français courant (échouent sur master) ─────────────

@pytest.mark.parametrize("objectif", [
    "Analyser la liste des fichiers obtenue et identifier ce qui est en double",
    "Résume-moi les mails d'aujourd'hui",
    "Celui qui parle en premier",
    "Vérifie lui-même le résultat",
    "Depuis quand le service est-il tombé ?",
    "Je suis en train de relire le produit",
    "Puis-je relancer la tâche ?",
])
def test_le_francais_courant_ne_declenche_plus_lvgl(objectif):
    """« qui », « aujourd'hui », « celui », « lui », « depuis », « suis », « puis »."""
    assert concerne(objectif) is False, f"Faux positif sur « {objectif} »"


# ── Les vrais objectifs LVGL doivent toujours passer ───────────────────────

@pytest.mark.parametrize("objectif", [
    "Refais le dashboard LVGL du Tab5",
    "Corrige le layout de la page météo",
    "Ajoute un widget de température",
    "Revois le design de l'écran d'accueil",
    "Améliore l'UI du bandeau central",
    "Le rendu de l'ecran principal est cassé",
])
def test_les_vrais_objectifs_lvgl_declenchent_toujours(objectif):
    assert concerne(objectif) is True, f"Faux négatif sur « {objectif} »"


# ── Robustesse ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("objectif", ["", "   ", None])
def test_objectif_vide_ne_leve_pas(objectif):
    assert concerne(objectif) is False


def test_ponctuation_collee_au_mot():
    """« l'UI, » doit compter : la tokenisation sépare sur la ponctuation."""
    assert concerne("Revois l'UI, puis le reste.") is True


def test_casse_indifferente():
    assert concerne("REFAIS LE DASHBOARD") is True
