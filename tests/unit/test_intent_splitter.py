"""
test_intent_splitter.py — Le découpage multi-intent ne hache plus les consignes (#T264).

Contexte mesuré le 10/08 en production : `/api/run` recevait une consigne
structurée d'une dizaine de lignes et la découpait en **5 « intents »**
exécutés en parallèle, dont « CONTRAINTE : utilise UNIQUEMENT l'outil
read_file » lancée comme une tâche, et « dans une tâche finale UNIQUE dépendant
des trois » devenue un fragment isolé qui ne dépendait plus de rien. 4 fragments
sur 5 ont fini en timeout.

Le rejeu des **1304 requêtes réelles** de production (vocal_audit_log +
sessions) a montré que 40 d'entre elles étaient découpées, dont toutes les
requêtes vocales à contexte injecté (« Question utilisateur : … Données
récupérées : … »), coupées en 5 morceaux. Après correctif : 3, et aucune
régression.

Les cas ci-dessous sont donc tirés du trafic réel, pas inventés.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.intent_splitter import LONGUEUR_MAX_DECOUPAGE, IntentSplitter


@pytest.fixture
def splitter():
    return IntentSplitter()


# Le prompt exact qui partait en 5 intents en production le 10/08.
CONSIGNE_STRUCTUREE = (
    "Analyse en LECTURE SEULE trois fichiers de ce depot : deploy/README.md, pyproject.toml "
    "et docs/design_provider_plugin_contract.md. Ces trois lectures sont INDEPENDANTES : "
    "traite-les en parallele dans un meme stage. Pour chacun, resume en 3 points. Puis, dans "
    "une tache finale UNIQUE dependant des trois, produis une synthese comparative. "
    "CONTRAINTE : utilise UNIQUEMENT loutil read_file."
)

# Forme réelle des prompts vocaux : question + contexte injecté. Découpés en 5
# fragments avant le correctif, sur du trafic quotidien. Les 7 prompts de ce
# type relevés en production mesurent entre 741 et 1343 caractères — la taille
# compte, donc l'échantillon la respecte : raccourci, il ne testerait pas le
# même chemin (c'est la garde de longueur qui les protège).
PROMPT_AVEC_CONTEXTE = (
    "Question utilisateur :  Quel est mon planning du 25-7-2026 ?  Données récupérées : "
    "📅 10 événement(s) à venir : Réunion equipe le 25/07 à 09:00. Dentiste le 25/07 à 14:30. "
    "Courses le 26/07 à 10:00. Anniversaire Paul le 27/07 à 19:00. Sport le 28/07 à 18:00. "
    "Point projet le 29/07 à 11:00. Livraison colis le 29/07. Médecin le 30/07 à 08:45. "
    "Déjeuner Marie le 30/07 à 12:30. Révision voiture le 31/07 à 09:00. "
    "Aucun conflit détecté sur la période. Prochain créneau libre : le 25/07 entre 10:00 et 14:30."
)


class TestConsignesStructurees:
    """Ce qui ne doit PLUS être découpé."""

    def test_la_consigne_de_production_reste_entiere(self, splitter):
        assert splitter.split(CONSIGNE_STRUCTUREE) == [CONSIGNE_STRUCTUREE]

    def test_prompt_vocal_avec_contexte_injecte(self, splitter):
        assert splitter.split(PROMPT_AVEC_CONTEXTE) == [PROMPT_AVEC_CONTEXTE]

    def test_au_dela_du_seuil_de_longueur(self, splitter):
        long = "Allume la lumière du salon et donne-moi la météo. " * 8
        assert len(long) > LONGUEUR_MAX_DECOUPAGE
        assert splitter.split(long) == [long]

    @pytest.mark.parametrize("prompt", [
        "Lis le fichier a.py et affiche son contenu. CONTRAINTE : ne modifie rien",
        "Analyse le module et donne un résumé, interdiction d'écrire un fichier",
        "Vérifie la config et affiche les erreurs — utilise uniquement read_file",
    ])
    def test_une_contrainte_n_est_pas_une_tache(self, splitter, prompt):
        assert len(splitter.split(prompt)) == 1

    @pytest.mark.parametrize("prompt", [
        "Lis les trois fichiers et affiche la synthèse des trois",
        "Analyse le module et donne le résultat de cette tâche",
        "Compile le projet et affiche les erreurs dépendant du build",
        "Lis a.py et b.py et affiche leurs résultats comparés",
    ])
    def test_fragment_qui_renvoie_a_un_autre(self, splitter, prompt):
        assert len(splitter.split(prompt)) == 1

    def test_verbe_orphelin(self, splitter):
        """« Recherche et liste les fichiers » produisait le fragment « Recherche »."""
        prompt = "Recherche et liste les fichiers du projet dans le dossier /homeassistant"
        assert splitter.split(prompt) == [prompt]

    def test_prose_dictee_avec_puis(self, splitter):
        """
        Les 6 seules occurrences de « puis » dans les 1280 requêtes courtes du
        corpus réel sont de la parole transcrite, pas des enchaînements de
        tâches. Elles sont neutralisées par la garde du verbe orphelin — d'où le
        maintien de « puis » comme marqueur (retirer le marqueur aurait cassé un
        cas légitime sans rien corriger, vérifié par rejeu).
        """
        prompt = "Et puis il y a des oeufs d'amour."
        assert splitter.split(prompt) == [prompt]


class TestVraisMultiIntents:
    """Ce qui doit CONTINUER d'être découpé."""

    def test_deux_ordres_coordonnes(self, splitter):
        resultat = splitter.split("Allume la lumière du salon et donne-moi la météo")
        assert len(resultat) == 2
        assert "Allume la lumière du salon" in resultat[0]
        assert "météo" in resultat[1]

    def test_deux_actions_sur_le_code(self, splitter):
        """Cas tiré du trafic réel, toujours découpé après correctif."""
        resultat = splitter.split(
            "Lis le contenu de api.py dans moteur-master et affiche la définition de la route /api/sessions"
        )
        assert len(resultat) == 2

    def test_possessif_leur_ne_bloque_pas_un_vrai_split(self, splitter):
        """Bugbot : ``leurs?`` nu matchait le possessif français ordinaire."""
        resultat = splitter.split(
            "Allume la lumière du salon et donne-moi la date de leur anniversaire"
        )
        assert len(resultat) == 2
        assert "Allume la lumière du salon" in resultat[0]
        assert "anniversaire" in resultat[1]


class TestNonRegression:
    """Le trafic courant — court, mono-intent — n'est pas affecté."""

    @pytest.mark.parametrize("prompt", [
        "allume la lumière du salon",
        "quelle est la météo demain ?",
        "Quel est mon planning du jour ?",
        "éteins tout",
        "il fait combien dans le salon ?",
    ])
    def test_requetes_vocales_courantes(self, splitter, prompt):
        assert splitter.split(prompt) == [prompt]

    def test_prompt_vide_ou_minuscule(self, splitter):
        assert splitter.split("") == [""]
        assert splitter.split("ok") == ["ok"]
