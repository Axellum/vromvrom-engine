"""
[#T434] Un slot sale contamine toutes les tâches qui passent après lui.

Mesuré le 21/08 sur `dc3` : un `stash` réappliqué par `git_safety` sur un master
qui avait bougé a laissé des marqueurs `<<<<<<< Updated upstream` dans
`scripts/recolte_dreamcoder.py`. Le fichier ne parsait plus, et les TROIS tâches
suivantes — dont deux analyses qui n'y touchaient pas — ont été refusées par la
porte #T394 (« le code produit ne parse pas »), sur les dégâts d'une autre.

Un clone de slot est jetable : son travail retenu vit sur sa branche, son travail
non retenu n'a pas à survivre.
"""

import asyncio
import subprocess

import pytest

from agents import dreamer_agent


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


@pytest.fixture
def slot(tmp_path):
    """Un clone de slot avec un dépôt d'origine, comme en production."""
    origine = tmp_path / "origine"
    origine.mkdir()
    _git("init", "-q", "-b", "master", cwd=origine)
    _git("config", "user.email", "t@example.invalid", cwd=origine)
    _git("config", "user.name", "T", cwd=origine)
    (origine / "module.py").write_text("VALEUR = 1\n", encoding="utf-8")
    _git("add", "-A", cwd=origine)
    _git("commit", "-qm", "initial", cwd=origine)

    clone = tmp_path / "slot"
    _git("clone", "-q", str(origine), str(clone), cwd=tmp_path)
    _git("config", "user.email", "t@example.invalid", cwd=clone)
    _git("config", "user.name", "T", cwd=clone)
    return clone


def test_un_slot_propre_reste_intact(slot):
    asyncio.run(dreamer_agent._preparer_slot(str(slot), False))
    assert (slot / "module.py").read_text(encoding="utf-8") == "VALEUR = 1\n"
    assert _git("status", "--porcelain", cwd=slot).stdout.strip() == ""


def test_un_fichier_suivi_modifie_est_remis_a_zero(slot):
    """Le cas réel : des marqueurs de conflit laissés dans un fichier Python."""
    (slot / "module.py").write_text(
        "<<<<<<< Updated upstream\nVALEUR = 1\n=======\nVALEUR = 2\n>>>>>>> Stashed changes\n",
        encoding="utf-8")

    asyncio.run(dreamer_agent._preparer_slot(str(slot), False))

    contenu = (slot / "module.py").read_text(encoding="utf-8")
    assert "<<<<<<<" not in contenu
    assert contenu == "VALEUR = 1\n"
    import ast
    ast.parse(contenu)  # la porte #T394 ne doit plus avoir de raison de refuser


def test_les_fichiers_non_suivis_sont_balayes(slot):
    """Les scripts jetables d'une tâche précédente ne doivent pas entrer au commit suivant."""
    (slot / "_tmp_inspect.py").write_text("print('reste')\n", encoding="utf-8")
    (slot / "brouillon").mkdir()
    (slot / "brouillon" / "note.txt").write_text("x", encoding="utf-8")

    asyncio.run(dreamer_agent._preparer_slot(str(slot), False))

    assert not (slot / "_tmp_inspect.py").exists()
    assert not (slot / "brouillon").exists()


def test_le_slot_revient_sur_sa_base(slot):
    """Un slot resté sur une branche de tâche repart de master (#T392)."""
    _git("checkout", "-q", "-b", "task/99_123", cwd=slot)
    branche = asyncio.run(dreamer_agent._preparer_slot(str(slot), False))
    assert branche == dreamer_agent.BASE_SLOT
    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=slot).stdout.strip() == dreamer_agent.BASE_SLOT


def test_le_travail_deja_commite_sur_une_branche_survit(slot):
    """Nettoyer l'arbre ne doit pas détruire ce qui est DÉJÀ commité ailleurs."""
    _git("checkout", "-q", "-b", "task/77_456", cwd=slot)
    (slot / "livrable.md").write_text("rapport", encoding="utf-8")
    _git("add", "-A", cwd=slot)
    _git("commit", "-qm", "travail de la tache 77", cwd=slot)

    asyncio.run(dreamer_agent._preparer_slot(str(slot), False))

    assert _git("cat-file", "-e", "task/77_456:livrable.md", cwd=slot).returncode == 0


def test_les_stashs_en_attente_sont_purges(slot):
    """
    [#T435] Un `stash` d'agent laissé par une tâche précédente est réappliqué par
    `git_safety` sur une base qui a bougé — il remet des marqueurs de conflit et
    casse toutes les tâches suivantes. Un clone de slot n'a pas d'utilisateur à
    protéger : le travail retenu vit sur sa branche.
    """
    (slot / "module.py").write_text("VALEUR = 99" + chr(10), encoding="utf-8")
    _git("stash", "push", "-m", "agent_checkpoint_123", cwd=slot)
    assert _git("stash", "list", cwd=slot).stdout.strip() != ""

    dreamer_agent.assainir_slot(str(slot))

    assert _git("stash", "list", cwd=slot).stdout.strip() == ""


def test_assainir_ne_fait_rien_sur_un_slot_deja_neuf(slot):
    """Pas de bruit dans le journal ni de geste inutile quand tout va bien."""
    assert dreamer_agent.assainir_slot(str(slot)) is False
