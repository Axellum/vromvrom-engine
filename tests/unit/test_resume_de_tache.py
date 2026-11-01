"""
tests/unit/test_resume_de_tache.py — le résumé d'une tâche autonome (#T421).

`result_summary` recevait `pipeline_result["response"]` brut : le TRANSCRIPT du
DAG, pas une conclusion. Relevé sur la tâche #50 du 19/08 en production, un
« résumé » de ~2 400 caractères qui contient, dans l'ordre :

    Plan DAG généré avec 6 étapes réparties sur 3 niveaux logiques.
    Plan DAG généré avec 1 étapes réparties sur 1 niveaux logiques.
    <think> …raisonnement interne… </think>
    ## Analyse de l'erreur   (une erreur d'OUTIL, intermédiaire)
    ## Plan d'action correctif  (un bloc JSON)
    ## Résumé   (…de cette erreur d'outil, pas du livrable)
    Plan DAG généré avec 6 étapes réparties sur 3 niveaux logiques.

Rien là-dedans ne dit ce qui a été livré. Et ce texte partait tel quel dans le
corps des PR autonomes : aucune PR de nuit n'était lisible sans ouvrir le diff.

Le moteur connaît pourtant les faits — fichiers du commit, verdict du rejeu des
tests, provider, coût. Ce sont des MESURES. Elles passent en tête ; la prose de
l'agent suit, nettoyée, annoncée pour ce qu'elle est.
"""

import pytest

from agents import dreamer_agent as da

# Extrait fidèle du transcript réel de la tâche #50 (19/08, production).
_TRANSCRIPT_REEL = """Plan DAG généré avec 6 étapes réparties sur 3 niveaux logiques.

Plan DAG généré avec 1 étapes réparties sur 1 niveaux logiques.

<think>
L'erreur indique clairement que le problème est une tentative d'exécution de code
Python inline via `python3 -c` qui a été refusé par le bac à sable de sécurité.
Je vais proposer ce plan d'action.
</think>

## Analyse de l'erreur

L'outil a tenté d'utiliser `python3 -c` pour lire le fichier, ce qui a été refusé.

Résultat 'run_terminal_command' : total 1792 drwxr-xr-x 3 deck ubuntu 12288 Aug 18 21:38 .

## Résumé

Le problème est un mauvais choix d'outil. La tâche peut être réexécutée.

Plan DAG généré avec 6 étapes réparties sur 3 niveaux logiques."""


# ── Nettoyage du transcript ─────────────────────────────────────────────────

def test_le_bruit_d_orchestration_disparait():
    propre = da._nettoyer_transcript(_TRANSCRIPT_REEL)

    assert "Plan DAG généré" not in propre
    assert "<think>" not in propre
    # Le bloc entier saute, pas seulement ses balises.
    assert "Je vais proposer ce plan" not in propre
    assert "bac à sable de sécurité" not in propre
    assert "Résultat 'run_terminal_command'" not in propre
    assert "drwxr-xr-x" not in propre


def test_la_prose_utile_survit():
    propre = da._nettoyer_transcript(_TRANSCRIPT_REEL)

    assert "## Analyse de l'erreur" in propre
    assert "Le problème est un mauvais choix d'outil." in propre


def test_un_transcript_vide_ne_casse_rien():
    assert da._nettoyer_transcript("") == ""
    assert da._nettoyer_transcript(None) == ""
    assert da._dernier_mot_agent("") == ""
    assert da._incidents_transcript("") == []


def test_le_dernier_mot_garde_la_fin_pas_le_debut():
    """
    Le transcript se lit dans l'ordre : ce que l'agent écrit en dernier est ce
    qui ressemble le plus à un bilan. Tronquer par le début le jetterait.
    """
    texte = "\n\n".join(f"Paragraphe {i} rempli de contenu." for i in range(200))

    queue = da._dernier_mot_agent(texte, max_chars=200)

    assert "Paragraphe 199" in queue
    assert "Paragraphe 0." not in queue
    assert queue.startswith("…")


# ── Incidents ───────────────────────────────────────────────────────────────

def test_les_incidents_sont_comptes_pas_recopies():
    """
    La tâche #52 du 19/08 a été déclarée `completed` avec trois
    « Credit balance is too low » dans son résumé. Les compter, c'est dire
    qu'une réussite a coûté des essais.
    """
    brut = "bla Credit balance is too low bla Credit balance is too low fin"

    incidents = da._incidents_transcript(brut)

    assert incidents == ["2 × « Credit balance is too low »"]


def test_un_transcript_sain_ne_signale_aucun_incident():
    assert da._incidents_transcript("Tout s'est bien passé.") == []


# ── Le résumé complet ───────────────────────────────────────────────────────

@pytest.fixture
def resume():
    return da._resume_de_tache(
        _TRANSCRIPT_REEL,
        fichiers=["tools/sync_db_to_markdown.py", "tests/unit/test_sync_db_to_markdown.py"],
        ajouts=53,
        retraits=40,
        verdict_tests="12 passed",
        verdict_frontend=None,
        provider="deepseek-free",
        gouverneur="deepseek-chat",
        tokens=1260794,
        cout=0.289922,
    )


def test_les_faits_passent_avant_la_prose(resume):
    """Ce qui est mesuré se lit en premier ; ce qui est raconté vient après."""
    assert resume.index("**Livré**") < resume.index("Dernier mot de l'agent")


def test_le_resume_dit_ce_qui_a_ete_livre(resume):
    assert "2 fichier(s), +53 / −40" in resume
    assert "`tools/sync_db_to_markdown.py`" in resume
    assert "`tests/unit/test_sync_db_to_markdown.py`" in resume


def test_le_resume_porte_le_verdict_des_tests(resume):
    assert "**Tests livrés** : 12 passed" in resume


def test_le_resume_porte_les_modeles_et_le_cout(resume):
    assert "`deepseek-free`" in resume
    assert "`deepseek-chat`" in resume
    assert "1 260 794 jetons" in resume
    assert "$0.2899" in resume


def test_la_prose_est_annoncee_pour_ce_qu_elle_est(resume):
    """
    Le dernier mot d'un agent n'est pas une conclusion : sur la tâche #50, le
    « ## Résumé » parle d'une erreur d'outil intermédiaire. L'étiquette évite
    qu'un relecteur le prenne pour un bilan de livraison.
    """
    assert "ce n'est pas une conclusion garantie" in resume


def test_l_absence_de_test_dans_le_diff_est_dite(resume):
    sans_test = da._resume_de_tache(
        "", fichiers=["core/x.py"], ajouts=1, retraits=0,
        verdict_tests=None, verdict_frontend=None,
        provider="p", gouverneur="g", tokens=1, cout=0.0)

    assert "aucun test dans le diff" in sans_test


def test_un_commit_sans_fichier_est_signale_comme_anomalie():
    """
    Ne devrait jamais arriver (la porte #T388 l'attrape avant), mais un résumé
    qui affiche « 0 fichier » sans le dire se lirait comme une réussite normale.
    """
    vide = da._resume_de_tache(
        "", fichiers=[], ajouts=0, retraits=0,
        verdict_tests=None, verdict_frontend=None,
        provider="p", gouverneur="g", tokens=0, cout=0.0)

    assert "anomalie" in vide


def test_le_frontend_n_apparait_que_s_il_est_concerne(resume):
    assert "**Frontend**" not in resume

    avec_front = da._resume_de_tache(
        "", fichiers=["ihm-v2/src/A.tsx"], ajouts=10, retraits=0,
        verdict_tests=None, verdict_frontend="build OK (2 518 modules)",
        provider="p", gouverneur="g", tokens=1, cout=0.0)

    assert "**Frontend** : build OK (2 518 modules)" in avec_front


def test_une_longue_liste_de_fichiers_est_bornee():
    beaucoup = da._resume_de_tache(
        "", fichiers=[f"core/f{i}.py" for i in range(30)], ajouts=1, retraits=0,
        verdict_tests=None, verdict_frontend=None,
        provider="p", gouverneur="g", tokens=1, cout=0.0)

    assert "et 18 autre(s)" in beaucoup
