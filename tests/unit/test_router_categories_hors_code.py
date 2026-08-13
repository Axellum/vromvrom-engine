"""
tests/unit/test_router_categories_hors_code.py — calendrier, mail, comptes (#T319).

Mesuré le 12/08/2026 en interrogeant directement le classifieur du slow-path sur les
demandes de la campagne « tout-terrain hors code » :

    « il me reste combien de crédit… »  → account_balance      (confiance 95 %)
    « qu'est-ce que j'ai de prévu… »    → calendar_query       (confiance 95 %)
    « résume-moi les mails… »           → email_summarization  (confiance 95 %)

**Les trois classifications sont justes.** Toutes les trois étaient jetées par le test
`llm_category in self.categories` — le routeur n'avait que 8 catégories, aucune pour ces
familles. La requête repartait donc sans catégorie : vers le Planner, donc sur le chemin
DAG, donc dans le mur HITL. C'est exactement ce qui est arrivé à « il me reste combien de
crédit chez Anthropic et Gemini ? » : **bloquée en attente d'approbation sans qu'aucun
outil n'ait tourné**.

Deux corrections, testées ici :
  1. trois catégories ajoutées, avec des mots-clés qui ne recouvrent aucune catégorie
     existante (« coût », « facture », « tarif » restent à `analysis`) ;
  2. `_normaliser_categorie_llm` ramène le libellé libre du classifieur sur le
     vocabulaire fermé du routeur, au lieu de le jeter.

⚠️ Ce que ces catégories NE font PAS : elles ne changent pas l'accès aux outils. Vérifié
le 12/08 — l'executor atteignait déjà `get_calendar_events` depuis la catégorie `database`
(fausse). Ce qu'elles changent, c'est le **chemin** : raccourci lecture vers l'Executor au
lieu du Planner/DAG/HITL.
"""

from unittest.mock import MagicMock

import pytest

from core.router import CATEGORY_TO_CONTEXT, Router


def _routeur() -> Router:
    return Router(llm_gateway=MagicMock())


# ── Les trois nouvelles catégories existent et sont cohérentes ───────────────

@pytest.mark.parametrize("categorie", ["calendar", "email", "accounts"])
def test_categorie_declaree(categorie):
    r = _routeur()
    assert categorie in r.categories
    assert r.categories[categorie]["keywords"], "catégorie sans mot-clé = catégorie morte"
    assert categorie in CATEGORY_TO_CONTEXT, "absente de CATEGORY_TO_CONTEXT"


# ── Normalisation du libellé libre du classifieur (le cœur de #T319) ────────

@pytest.mark.parametrize("brut,attendu", [
    # Les trois libellés RÉELLEMENT mesurés le 12/08.
    ("calendar_query", "calendar"),
    ("email_summarization", "email"),
    ("account_balance", "accounts"),
    # Variantes proches immédiates.
    ("agenda", "calendar"),
    ("gmail", "email"),
    ("billing", "accounts"),
    ("Calendar-Query", "calendar"),      # casse et tiret
    ("  calendar_query  ", "calendar"),  # espaces
    # Préfixe canonique générique.
    ("calendar_lookup_next_week", "calendar"),
    ("sysadmin_diagnostic", "sysadmin"),
    # Déjà canonique : inchangé.
    ("files", "files"),
    ("home_assistant", "home_assistant"),
])
def test_normalisation(brut, attendu):
    assert _routeur()._normaliser_categorie_llm(brut) == attendu


def test_libelle_inconnu_rendu_tel_quel():
    """Un libellé hors vocabulaire n'est pas deviné : l'appelant le rejettera.

    Une catégorie fausse envoie la requête au mauvais tier — plus cher qu'une
    catégorie absente (#T294).
    """
    r = _routeur()
    assert r._normaliser_categorie_llm("quelque_chose_dinvente") == "quelque_chose_dinvente"
    assert r._normaliser_categorie_llm("quelque_chose_dinvente") not in r.categories


def test_libelle_vide():
    assert _routeur()._normaliser_categorie_llm("") == ""


# ── Couche 1 (mots-clés) : les demandes tombent au bon endroit ───────────────

@pytest.mark.parametrize("prompt,attendu", [
    ("Qu'est-ce que j'ai de prévu demain ?", "calendar"),
    ("Montre-moi mon agenda de la semaine", "calendar"),
    ("J'ai un rendez-vous quand ?", "calendar"),
    ("Résume-moi les mails importants d'hier", "email"),
    ("Cherche dans ma boite gmail", "email"),
    ("Il me reste combien de crédit chez Anthropic ?", "accounts"),
    ("Quel est mon quota restant ?", "accounts"),
])
def test_couche_mots_cles_nouvelles_categories(prompt, attendu):
    r = _routeur()
    mots = r._tokenize(prompt)
    _, dominante, score = r._score_categories(mots, set(mots))
    assert dominante == attendu, f"« {prompt} » → {dominante} au lieu de {attendu}"
    assert score > 0


# ── NON-RÉGRESSION : la couche 1 existante ne bouge pas ─────────────────────

@pytest.mark.parametrize("prompt,attendu", [
    # Le garde-fou du prompt de session : ne pas élargir une catégorie « pour que
    # ça passe ». « coût » et « facture » doivent RESTER à `analysis`.
    ("Fais-moi une analyse du coût des modèles", "analysis"),
    ("Sors-moi la facture du mois", "analysis"),
    # Les 8 catégories d'origine, inchangées.
    ("Allume la lumière du salon", "home_assistant"),
    ("Écris une fonction python", "code_generation"),
    ("Fais une requête sql sur la table", "database"),
    ("Lis le fichier de config", "files"),
    ("Bonjour", "casual_chat"),
    ("Vérifie l'uptime du deck en ssh", "sysadmin"),
])
def test_non_regression_categories_existantes(prompt, attendu):
    r = _routeur()
    mots = r._tokenize(prompt)
    _, dominante, _ = r._score_categories(mots, set(mots))
    assert dominante == attendu, f"RÉGRESSION : « {prompt} » → {dominante} au lieu de {attendu}"


@pytest.mark.parametrize("prompt", [
    "Fais-moi une analyse du coût des modèles",
    "Sors-moi la facture du mois",
    "Compare les tarifs des providers",
])
def test_le_vocabulaire_tarifaire_ne_bascule_pas_vers_accounts(prompt):
    """
    Le garde-fou explicite du prompt de session : **ne jamais élargir une catégorie
    pour que ça passe**. `accounts` parle de solde/quota/crédit restant, pas de prix
    ni de facturation — ceux-là appartiennent à `analysis`.

    (« Compare les tarifs » tombe aujourd'hui sur AUCUNE catégorie, parce que
    `analysis` porte « tarif » au singulier et qu'il n'y a pas de racinisation :
    défaut préexistant, sans rapport avec #T319. Ce qui compte ici est qu'il ne
    parte pas vers `accounts`.)
    """
    r = _routeur()
    mots = r._tokenize(prompt)
    _, dominante, _ = r._score_categories(mots, set(mots))
    assert dominante != "accounts", f"« {prompt} » capté à tort par accounts"


def test_les_nouvelles_categories_ne_volent_aucun_mot_cle():
    """
    Un mot-clé partagé rend le routage dépendant de l'ordre d'itération du
    dictionnaire. On garde ce que l'on ajoute : aucune des trois catégories de
    #T319 ne doit reprendre un mot-clé déjà utilisé.

    Portée volontairement limitée aux nouvelles catégories : la collision
    préexistante « benchmark » (partagé par `analysis` et `sysadmin` sur master)
    n'est pas de ce ressort — elle est signalée à part, sans faire échouer #T319.
    """
    r = _routeur()
    nouvelles = {"calendar", "email", "accounts"}
    anciennes_kw = {
        kw: cat
        for cat, data in r.categories.items() if cat not in nouvelles
        for kw in data["keywords"]
    }
    collisions = [
        f"'{kw}' déjà utilisé par {anciennes_kw[kw]}, repris par {cat}"
        for cat in nouvelles
        for kw in r.categories[cat]["keywords"]
        if kw in anciennes_kw
    ]
    assert not collisions, "Mots-clés volés : " + " · ".join(collisions)


def test_collision_preexistante_benchmark_documentee():
    """
    Sentinelle : « benchmark » est partagé par `analysis` et `sysadmin` sur master.
    Ce test documente la dette et **échouera si elle est corrigée** — auquel cas il
    faudra le supprimer, pas le contourner.
    """
    r = _routeur()
    assert "benchmark" in r.categories["analysis"]["keywords"]
    assert "benchmark" in r.categories["sysadmin"]["keywords"]
