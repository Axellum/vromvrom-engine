"""
tests/unit/test_file_write_guard.py — Verrous d'écriture et garde
anti-lost-update de tools/system.py (#T230).

Scénarios couverts :
  - création sans lecture préalable autorisée ;
  - lecture → écriture autorisée ;
  - écriture refusée si le fichier a changé depuis la lecture (relecture OK) ;
  - réécriture sans relecture refusée (l'agent n'a pas vu le nouvel état) ;
  - écritures concurrentes sérialisées par le verrou par chemin ;
  - la sécurité du workspace reste inchangée.
"""
import threading

import pytest

from tools import system as system_tools


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """Redirige le workspace autorisé vers tmp_path et repart d'empreintes vierges."""
    monkeypatch.setattr(system_tools, "_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(system_tools, "_GUARD_ENABLED", True)
    system_tools._seen_fingerprints.clear()
    yield tmp_path
    system_tools._seen_fingerprints.clear()


def test_creation_sans_lecture_autorisee(workspace):
    cible = workspace / "nouveau.txt"
    resultat = system_tools.write_file(str(cible), "v1")
    assert resultat.startswith("Succès")
    assert cible.read_text(encoding="utf-8") == "v1"


def test_lecture_puis_ecriture_autorisee(workspace):
    cible = workspace / "f.txt"
    cible.write_text("v1", encoding="utf-8")
    assert not system_tools.read_file(str(cible)).startswith("Erreur")
    assert system_tools.write_file(str(cible), "v2").startswith("Succès")
    assert cible.read_text(encoding="utf-8") == "v2"


def test_ecriture_refusee_si_modification_depuis_lecture(workspace):
    cible = workspace / "f.txt"
    cible.write_text("v1", encoding="utf-8")
    system_tools.read_file(str(cible))
    # Modification externe (autre tâche DAG, autre processus…)
    cible.write_text("modifié ailleurs", encoding="utf-8")
    resultat = system_tools.write_file(str(cible), "v2")
    assert "modifié depuis la dernière lecture" in resultat
    assert cible.read_text(encoding="utf-8") == "modifié ailleurs"
    # La relecture recharge l'empreinte : l'écriture repasse.
    system_tools.read_file(str(cible))
    assert system_tools.write_file(str(cible), "v2").startswith("Succès")
    assert cible.read_text(encoding="utf-8") == "v2"


def test_reecriture_sans_relecture_refusee(workspace):
    """Le lost-update historique : lire, écrire, puis réécraser sans relire."""
    cible = workspace / "f.txt"
    cible.write_text("v1", encoding="utf-8")
    system_tools.read_file(str(cible))
    assert system_tools.write_file(str(cible), "v2").startswith("Succès")
    resultat = system_tools.write_file(str(cible), "v3")
    assert "modifié depuis la dernière lecture" in resultat
    assert cible.read_text(encoding="utf-8") == "v2"


def test_ecritures_concurrentes_serialisees(workspace):
    """Garde désactivée pour isoler le verrou : le contenu final est l'UN des
    deux blocs complets, jamais un entrelacement des deux."""
    cible = workspace / "c.txt"
    bloc_a = "AAAA\n" * 20_000
    bloc_b = "BBBB\n" * 20_000
    cible.write_text("initial", encoding="utf-8")
    monkeypatch_garde = system_tools._GUARD_ENABLED
    system_tools._GUARD_ENABLED = False
    try:
        barriere = threading.Barrier(2)

        def ecrire(contenu):
            barriere.wait()
            system_tools.write_file(str(cible), contenu)

        t1 = threading.Thread(target=ecrire, args=(bloc_a,))
        t2 = threading.Thread(target=ecrire, args=(bloc_b,))
        t1.start(), t2.start()
        t1.join(), t2.join()
    finally:
        system_tools._GUARD_ENABLED = monkeypatch_garde
    final = cible.read_text(encoding="utf-8")
    assert final in (bloc_a, bloc_b), "le verrou par chemin doit sérialiser les écritures"


def test_securite_workspace_inchangee(workspace):
    """Le refus des chemins hors workspace ne doit pas être affecté."""
    hors_workspace = str(workspace.parent / "hors_workspace.txt")
    assert system_tools.write_file(hors_workspace, "x").startswith("Erreur de sécurité")
    assert system_tools.read_file(hors_workspace).startswith("Erreur de sécurité")
