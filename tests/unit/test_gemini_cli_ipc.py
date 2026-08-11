"""
tests/unit/test_gemini_cli_ipc.py — IPC fichier avec la CLI Antigravity.

`GeminiCLIProvider` ne parle pas à la CLI par stdout mais par un fichier
d'échange : le prompt dicte un nom de fichier, la CLI y écrit sa réponse, le
provider le lit puis le supprime. Ce nom était FIXE et relatif au CWD
(`antigravity_exchange_temp.txt`), alors que les providers tournent en parallèle
(`generate_async` → `asyncio.to_thread`) : deux appels concurrents se
supprimaient et se relisaient mutuellement (réponses croisées), et un appel parti
en timeout laissait sa réponse être lue par l'appel suivant.

Ces tests verrouillent le nom unique par appel et le nettoyage des fichiers
temporaires. La CLI est simulée : le faux `run_cli_command` relit le nom de
fichier DANS le prompt qu'on lui passe — exactement comme le ferait la vraie CLI
— ce qui vérifie au passage que le contrat d'intégration est inchangé.
"""

import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.llm.providers import gemini as gemini_module
from core.llm.providers.gemini import _LEGACY_EXCHANGE_FILE, GeminiCLIProvider


class _ResultatCLI:
    """Sosie de subprocess.CompletedProcess."""

    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _nom_fichier_demande(cmd: list) -> str:
    """Extrait le nom du fichier d'échange dicté par le prompt (comme la CLI)."""
    dernier = cmd[-1]
    if dernier.startswith("@"):  # prompt long : passé par fichier
        with open(dernier[1:], encoding="utf-8") as f:
            dernier = f.read()
    correspondance = re.search(r"fichier texte nommé '([^']+)'", dernier)
    assert correspondance, "le prompt ne dicte aucun nom de fichier d'échange"
    return correspondance.group(1)


@pytest.fixture(autouse=True)
def _espace_de_travail_isole(tmp_path, monkeypatch):
    """Le fichier d'échange est relatif au CWD : on isole chaque test."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "core.token_tracker.record_usage",
        lambda *a, **kw: None,
    )
    return tmp_path


@pytest.fixture
def provider():
    return GeminiCLIProvider()


def test_nom_de_fichier_unique_par_appel(provider, monkeypatch):
    noms_vus = []

    def _faux_cli(cmd, **kwargs):
        nom = _nom_fichier_demande(cmd)
        noms_vus.append(nom)
        with open(nom, "w", encoding="utf-8") as f:
            f.write("réponse")
        return _ResultatCLI()

    monkeypatch.setattr(gemini_module, "run_cli_command", _faux_cli)

    provider.generate("sys", "premier")
    provider.generate("sys", "second")

    assert len(set(noms_vus)) == 2, f"nom d'échange réutilisé entre deux appels : {noms_vus}"
    assert all(n != _LEGACY_EXCHANGE_FILE for n in noms_vus)


def test_appels_concurrents_ne_croisent_pas_les_reponses(provider, monkeypatch):
    """Régression centrale : les deux appels écrivent AVANT que l'un ne lise.

    Avec un nom de fichier partagé, la seconde écriture écrase la première et les
    deux appels retournent la même réponse. Le barrier rend l'échec déterministe.
    """
    barriere = threading.Barrier(2, timeout=10)

    def _faux_cli(cmd, **kwargs):
        nom = _nom_fichier_demande(cmd)
        # Le marqueur identifie l'appel d'origine.
        marqueur = re.search(r"MARQUEUR-(\w+)", cmd[-1]).group(1)
        with open(nom, "w", encoding="utf-8") as f:
            f.write(f"réponse pour {marqueur}")
        barriere.wait()  # personne ne lit tant que les deux n'ont pas écrit
        return _ResultatCLI()

    monkeypatch.setattr(gemini_module, "run_cli_command", _faux_cli)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futurs = [
            executor.submit(provider.generate, "sys", f"MARQUEUR-{marqueur}")
            for marqueur in ("alpha", "beta")
        ]
        resultats = [f.result(timeout=15) for f in futurs]

    assert sorted(resultats) == ["réponse pour alpha", "réponse pour beta"]


def test_fichier_dechange_supprime_apres_lecture(provider, monkeypatch, _espace_de_travail_isole):
    def _faux_cli(cmd, **kwargs):
        with open(_nom_fichier_demande(cmd), "w", encoding="utf-8") as f:
            f.write("réponse")
        return _ResultatCLI()

    monkeypatch.setattr(gemini_module, "run_cli_command", _faux_cli)
    assert provider.generate("sys", "user") == "réponse"

    restants = list(_espace_de_travail_isole.glob("antigravity_exchange_temp*"))
    assert restants == [], f"fichiers d'échange laissés derrière : {restants}"


def test_fichier_dechange_supprime_meme_si_la_cli_echoue(provider, monkeypatch, _espace_de_travail_isole):
    """Un timeout laissait le fichier en place ; il empoisonnait l'appel suivant."""

    def _faux_cli_ko(cmd, **kwargs):
        with open(_nom_fichier_demande(cmd), "w", encoding="utf-8") as f:
            f.write("réponse partielle")
        raise TimeoutError("CLI expirée")

    monkeypatch.setattr(gemini_module, "run_cli_command", _faux_cli_ko)
    with pytest.raises(TimeoutError):
        provider.generate("sys", "user")

    restants = list(_espace_de_travail_isole.glob("antigravity_exchange_temp*"))
    assert restants == [], f"fichiers d'échange laissés derrière : {restants}"


def test_fichier_de_prompt_long_supprime(provider, monkeypatch):
    """Le fichier temp du prompt >8000 chars était créé avec delete=False et fuyait."""
    chemins_prompt = []

    def _faux_cli(cmd, **kwargs):
        assert cmd[-1].startswith("@"), "un prompt long doit passer par un fichier"
        chemins_prompt.append(cmd[-1][1:])
        with open(_nom_fichier_demande(cmd), "w", encoding="utf-8") as f:
            f.write("réponse")
        return _ResultatCLI()

    monkeypatch.setattr(gemini_module, "run_cli_command", _faux_cli)
    provider.generate("sys", "x" * 9000)

    assert chemins_prompt, "le chemin du prompt long n'a pas été capturé"
    assert not os.path.exists(chemins_prompt[0])


def test_repli_sur_le_nom_historique(provider, monkeypatch):
    """Si la CLI ignore le nom demandé, on lit quand même sa réponse."""

    def _faux_cli_legacy(cmd, **kwargs):
        with open(_LEGACY_EXCHANGE_FILE, "w", encoding="utf-8") as f:
            f.write("réponse via nom historique")
        return _ResultatCLI()

    monkeypatch.setattr(gemini_module, "run_cli_command", _faux_cli_legacy)
    assert provider.generate("sys", "user") == "réponse via nom historique"
    assert not os.path.exists(_LEGACY_EXCHANGE_FILE)


def test_residu_historique_anterieur_non_lu_comme_reponse(provider, monkeypatch):
    """Un fichier historique laissé par un appel précédent ne doit pas être servi."""
    with open(_LEGACY_EXCHANGE_FILE, "w", encoding="utf-8") as f:
        f.write("vieille réponse d'un autre appel")

    def _faux_cli_stdout(cmd, **kwargs):
        return _ResultatCLI(stdout="réponse fraîche par stdout")

    monkeypatch.setattr(gemini_module, "run_cli_command", _faux_cli_stdout)
    assert provider.generate("sys", "user") == "réponse fraîche par stdout"


def test_repli_stdout_puis_erreur_si_rien(provider, monkeypatch):
    """Comportement historique conservé : ni fichier ni stdout ⇒ RuntimeError."""

    def _faux_cli_muet(cmd, **kwargs):
        return _ResultatCLI(stdout="", stderr="boom", returncode=1)

    monkeypatch.setattr(gemini_module, "run_cli_command", _faux_cli_muet)
    with pytest.raises(RuntimeError, match="Antigravity CLI error"):
        provider.generate("sys", "user")
