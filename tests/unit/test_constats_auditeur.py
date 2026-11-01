"""
tests/unit/test_constats_auditeur.py — un constat d'audit se vérifie et se contractualise (#T420).

Deux mesures, sur les 81 tentatives autonomes des 18–19/08.

**Le rendement par forme de consigne :**

    contrat « 1 fichier visé + 1 test + VÉRIFIER D'ABORD »   6/11 = 55 %
    contrat « Zone : <étiquette du board> »                 11/35 = 31 %
    constat brut de l'auditeur, sans contrat                 1/8  = 12 %

L'auditeur produisait la troisième forme — `add_task(titre, "[fichier] constat")` —
alors qu'il a la matière de la première : il nomme le fichier exact.

**Les faux constats :** deux fois, l'auditeur a annoncé un fichier Python
tronqué qui compilait parfaitement.

    #47 (18/08)  core/budget_guard.py « code tronqué »   -> 642 lignes, ast.parse OK
    #59 (19/08)  agents/executor.py « SyntaxError »      -> ligne 715 complète, ast.parse OK

La première a brûlé ses 3 tentatives et fini `abandoned`. C'est la seule
famille de constats réfutable sans LLM, en quelques millisecondes.
"""

import pytest

from core import auditor_agent as aud

# ── Pré-vol : réfuter un constat de syntaxe ─────────────────────────────────

@pytest.fixture
def depot(tmp_path):
    """Un dépôt minimal avec un fichier qui compile et un qui ne compile pas."""
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "sain.py").write_text(
        "def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "core" / "casse.py").write_text(
        'def f():\n    logger.warning(f"oups : {e"\n', encoding="utf-8")
    return tmp_path


def test_le_vocabulaire_du_constat_de_syntaxe_est_reconnu():
    """Les formulations réelles des deux faux constats, à la lettre."""
    assert aud._constat_de_syntaxe(
        "Erreur de syntaxe critique (code tronqué) dans core/budget_guard.py", "")
    assert aud._constat_de_syntaxe(
        "Erreur de syntaxe fatale (SyntaxError) en fin de fichier",
        "Le fichier se termine brusquement sur une ligne tronquée")


def test_un_constat_ordinaire_n_est_pas_pris_pour_un_constat_de_syntaxe():
    assert not aud._constat_de_syntaxe(
        "Non-libération des ressources en cas d'exception",
        "`await detector.close()` n'est pas dans un bloc `finally`.")


def test_le_faux_constat_est_refute(depot):
    """Le cas #47 / #59 : le fichier cité compile, le constat tombe."""
    motif = aud._constat_refute(
        "Erreur de syntaxe fatale (SyntaxError) en fin de fichier",
        "Le fichier se termine brusquement sur une ligne tronquée.",
        "core/sain.py",
        racine=str(depot),
    )

    assert motif is not None
    assert "réfuté" in motif
    assert "core/sain.py" in motif


def test_un_vrai_constat_de_syntaxe_survit(depot):
    """On ne réfute que ce qui est faux : un fichier réellement cassé passe."""
    assert aud._constat_refute(
        "Erreur de syntaxe", "Le fichier ne parse pas.",
        "core/casse.py", racine=str(depot)) is None


def test_l_incertitude_ne_tue_jamais_un_constat(depot):
    """
    Fichier absent, chemin non-Python, lecture impossible : `None` veut dire
    « je ne sais pas ». On ne jette un constat que sur une preuve.
    """
    for fichier in ("core/inexistant.py", "docs/notes.md", ""):
        assert aud._constat_refute(
            "Erreur de syntaxe", "code tronqué", fichier, racine=str(depot)) is None
        assert aud._fichier_python_valide(fichier, racine=str(depot)) is None


def test_un_constat_non_syntaxique_n_est_jamais_refute(depot):
    """Le pré-vol ne juge QUE ce qu'il sait juger."""
    assert aud._constat_refute(
        "Fuite de ressources", "close() hors du finally.",
        "core/sain.py", racine=str(depot)) is None


# ── Contrat de délégation ───────────────────────────────────────────────────

@pytest.fixture
def contrat():
    return aud._contrat_delegation(
        "Non-libération des ressources de HAAnomalyDetector",
        "[agents/dreamer_agent.py] `await detector.close()` n'est pas dans un `finally`.",
        "agents/dreamer_agent.py",
    )


def test_le_contrat_donne_un_perimetre_de_fichier(contrat):
    """C'est ce qui distingue la famille à 55 % de celle à 12 %."""
    assert "Fichier visé : agents/dreamer_agent.py" in contrat
    assert "tests/unit/test_dreamer_agent.py" in contrat


def test_le_contrat_impose_de_verifier_avant_de_corriger(contrat):
    assert "VÉRIFIER AVANT DE CORRIGER" in contrat
    assert "Il peut être FAUX" in contrat


def test_le_contrat_dit_qu_un_constat_refute_est_une_reussite(contrat):
    """
    Sans cette phrase, un agent à qui on ordonne de corriger corrige — même ce
    qui n'existe pas. C'est ce qui a produit les 3 tentatives de la tâche #47.
    """
    assert "constat non reproduit" in contrat
    assert "C'est une réussite, pas un échec" in contrat
    assert "n'invente AUCUN correctif" in contrat


def test_le_contrat_porte_ses_validations(contrat):
    assert "pytest tests/unit/test_dreamer_agent.py -q" in contrat
    assert "ruff check agents/dreamer_agent.py" in contrat


def test_le_contrat_demande_un_verdict_dans_le_resume(contrat):
    assert "verdict sur le constat" in contrat


def test_sans_fichier_le_contrat_garde_l_avertissement():
    """
    Un finding sans `file` n'a pas de périmètre à donner — mais l'avertissement
    « ce constat peut être faux » vaut toujours.
    """
    c = aud._contrat_delegation("Titre", "Description libre.", "")

    assert "VÉRIFIER AVANT DE CORRIGER" in c
    assert "Fichier visé" not in c
    assert "Description libre." in c


@pytest.mark.parametrize(
    ("fichier", "attendu"),
    [
        ("agents/executor.py", "tests/unit/test_executor.py"),
        ("core/llm/builtin_providers.py", "tests/unit/test_builtin_providers.py"),
        ("api\\routes\\streaming.py", "tests/unit/test_streaming.py"),
    ],
)
def test_le_fichier_de_test_suit_la_convention(fichier, attendu):
    assert aud._fichier_de_test_suggere(fichier) == attendu
