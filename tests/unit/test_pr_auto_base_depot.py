"""
tests/unit/test_pr_auto_base_depot.py — la PR auto vise la bonne base.

Mesuré le 24/08 : `_ouvrir_pr_tache` envoyait `"base": "master"` en dur. Le
moteur est bien sur `master`, mais `Axellum/M5-Tab5-ESPHome-LVGL` est sur
`main`. Résultat : 14 branches Tab5 poussées sur origin, **zéro PR** — GitHub
répondait 422 sur chacune, et le détail de l'erreur ne disait même pas quelle
base avait été tentée.

`scripts/recolte_dreamcoder.py` portait le même défaut (`--base` par défaut à
`master`), donc la récolte manuelle ne rattrapait rien.

Ces tests figent trois propriétés :
  * la base vient du dépôt VISÉ, jamais d'une constante ;
  * quand l'API ne répond pas, on se rabat sur `origin/HEAD` puis sur `master`
    — un réseau coupé ne doit pas empêcher d'ouvrir une PR sur le moteur ;
  * un refus HTTP nomme la base tentée (sinon le 422 est indébogable).
"""

import json
import urllib.error
from unittest.mock import patch

from agents.dreamer_agent import _branche_par_defaut_depot, _ouvrir_pr_tache

BRANCHE = "task/50_1787526304"


class _ReponseFactice:
    """Contexte minimal imitant `urllib.request.urlopen`."""

    def __init__(self, charge: dict):
        self._corps = json.dumps(charge).encode("utf-8")

    def read(self):
        return self._corps

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _git_factice(reponses):
    """`_run_git` factice : 1er argument git → (code, stdout, stderr)."""

    def _faux(args, cwd="."):
        return reponses.get(args[0], (0, "", ""))

    return _faux


# --- _branche_par_defaut_depot -------------------------------------------


def test_api_fait_autorite():
    """Le dépôt Tab5 répond `main` : c'est `main` qu'on retient."""
    with patch("urllib.request.urlopen",
               return_value=_ReponseFactice({"default_branch": "main"})):
        base = _branche_par_defaut_depot(
            "Axellum", "M5-Tab5-ESPHome-LVGL", "jeton", ".")
    assert base == "main"


def test_repli_sur_origin_head_quand_l_api_est_muette():
    """
    Réseau coupé : `origin/HEAD` du clone reste une source valable.

    Sans ce repli, une panne GitHub transformerait toutes les PR en 422 sur
    un dépôt en `main` — exactement le défaut qu'on corrige.
    """
    erreur = urllib.error.URLError("réseau injoignable")
    faux_git = _git_factice({"symbolic-ref": (0, "origin/main", "")})
    with patch("urllib.request.urlopen", side_effect=erreur), \
            patch("tools.git_safety._run_git", faux_git):
        base = _branche_par_defaut_depot("Axellum", "un-depot", "jeton", ".")
    assert base == "main"


def test_repli_final_sur_master():
    """Rien ne répond : on garde le comportement historique, pas une erreur."""
    erreur = urllib.error.URLError("réseau injoignable")
    faux_git = _git_factice({"symbolic-ref": (1, "", "pas de HEAD distant")})
    with patch("urllib.request.urlopen", side_effect=erreur), \
            patch("tools.git_safety._run_git", faux_git):
        base = _branche_par_defaut_depot("Axellum", "un-depot", "jeton", ".")
    assert base == "master"


def test_sans_jeton_on_n_interroge_pas_l_api():
    """Pas de jeton : inutile d'appeler GitHub, `origin/HEAD` suffit."""
    faux_git = _git_factice({"symbolic-ref": (0, "origin/main", "")})
    with patch("urllib.request.urlopen") as urlopen, \
            patch("tools.git_safety._run_git", faux_git):
        base = _branche_par_defaut_depot("Axellum", "un-depot", "", ".")
    urlopen.assert_not_called()
    assert base == "main"


# --- _ouvrir_pr_tache -----------------------------------------------------


def test_la_pr_tab5_part_sur_main_pas_sur_master(monkeypatch):
    """
    Le cas réel du 24/08 : dépôt Tab5, remote `main`.

    C'est LE test de non-régression : si `base` redevient une constante,
    il tombe.
    """
    monkeypatch.setenv("MOTEUR_GITHUB_PUSH_TOKEN", "jeton-factice")
    remote = "https://github.com/Axellum/M5-Tab5-ESPHome-LVGL.git"
    faux_git = _git_factice({"remote": (0, remote, "")})
    envoyes = {}

    def _faux_urlopen(req, timeout=None):
        if req.get_method() == "POST":
            envoyes.update(json.loads(req.data.decode("utf-8")))
            return _ReponseFactice({"html_url": "https://github.com/pr/1"})
        return _ReponseFactice({"default_branch": "main"})

    with patch("tools.git_safety._run_git", faux_git), \
            patch("urllib.request.urlopen", side_effect=_faux_urlopen):
        info = _ouvrir_pr_tache(".", BRANCHE, "titre", "corps")

    assert info["ouverte"] is True
    assert envoyes["base"] == "main", "la base doit venir du dépôt, pas d'une constante"
    assert envoyes["head"] == BRANCHE


def test_la_pr_moteur_reste_sur_master(monkeypatch):
    """Le moteur ne doit pas régresser : son dépôt est bien sur `master`."""
    monkeypatch.setenv("MOTEUR_GITHUB_PUSH_TOKEN", "jeton-factice")
    remote = "https://github.com/Axellum/moteur_agents.git"
    faux_git = _git_factice({"remote": (0, remote, "")})
    envoyes = {}

    def _faux_urlopen(req, timeout=None):
        if req.get_method() == "POST":
            envoyes.update(json.loads(req.data.decode("utf-8")))
            return _ReponseFactice({"html_url": "https://github.com/pr/2"})
        return _ReponseFactice({"default_branch": "master"})

    with patch("tools.git_safety._run_git", faux_git), \
            patch("urllib.request.urlopen", side_effect=_faux_urlopen):
        info = _ouvrir_pr_tache(".", BRANCHE, "titre", "corps")

    assert info["ouverte"] is True
    assert envoyes["base"] == "master"


def test_un_refus_http_nomme_la_base_tentee(monkeypatch):
    """
    Un 422 muet a caché le défaut pendant des jours.

    Le détail doit porter la base ET la tête, sinon le journal ne permet pas
    de distinguer « base inexistante » de « branche déjà fusionnée ».
    """
    monkeypatch.setenv("MOTEUR_GITHUB_PUSH_TOKEN", "jeton-factice")
    remote = "https://github.com/Axellum/M5-Tab5-ESPHome-LVGL.git"
    faux_git = _git_factice({"remote": (0, remote, "")})

    def _faux_urlopen(req, timeout=None):
        if req.get_method() == "POST":
            raise urllib.error.HTTPError(
                req.full_url, 422, "Unprocessable Entity", {},
                _Corps(b'{"message":"Validation Failed"}'))
        return _ReponseFactice({"default_branch": "main"})

    with patch("tools.git_safety._run_git", faux_git), \
            patch("urllib.request.urlopen", side_effect=_faux_urlopen):
        info = _ouvrir_pr_tache(".", BRANCHE, "titre", "corps")

    assert info["ouverte"] is False
    assert info["raison"] == "http_422"
    assert "base=main" in info["detail"]
    assert BRANCHE in info["detail"]


class _Corps:
    """Flux minimal pour le corps d'une `HTTPError`."""

    def __init__(self, donnees: bytes):
        self._donnees = donnees

    def read(self):
        return self._donnees

    def close(self):
        """`HTTPError` referme son flux : sans ça, pytest lève un warning."""


# --- scripts/recolte_dreamcoder.py ---------------------------------------


def test_la_recolte_deduit_la_base_du_depot():
    """`gh repo view` fait autorité pour la récolte aussi."""
    from scripts.recolte_dreamcoder import _base_du_depot

    with patch("scripts.recolte_dreamcoder._run",
               return_value=(0, "main", "")):
        assert _base_du_depot(".") == "main"


def test_la_recolte_se_rabat_sur_master():
    """Ni `gh` ni `origin/HEAD` : on ne casse pas, on garde `master`."""
    from scripts.recolte_dreamcoder import _base_du_depot

    with patch("scripts.recolte_dreamcoder._run",
               return_value=(1, "", "gh absent")):
        assert _base_du_depot(".") == "master"
