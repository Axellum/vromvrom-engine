"""
tests/unit/test_env_example_couvre_les_cles_critiques.py — #T478.

Une variable absente de `.env.example` disparaît à chaque reconstruction du
`.env`. C'est arrivé à `MOTEUR_GITHUB_PUSH_TOKEN` : le Deck l'avait, le PC non,
et le PC porte 7 fichiers `.env.bak_*` — autant de reconstructions faites depuis
un modèle qui ne la mentionnait pas.

Conséquence mesurée le 24/08 : le cycle du PC **poussait** ses branches (le
gestionnaire d'identifiants Windows suffit pour ça) mais n'ouvrait **aucune
PR** — `_ouvrir_pr_tache` rendait `jeton_absent`. 14 branches Tab5 sur origin,
zéro PR en face, et le seul indice était en fin de journal de tâche.

Ce test fige la liste des clés dont l'absence produit un échec SILENCIEUX :
celles où le moteur continue de tourner, dépense, et ne dit rien.
"""

from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[2]
EXEMPLE = RACINE / ".env.example"

# Clés dont l'absence ne casse rien tout de suite — elle dégrade en silence.
# C'est précisément ce qui les rend dangereuses à oublier.
CLES_A_EFFET_SILENCIEUX = {
    "MOTEUR_GITHUB_PUSH_TOKEN":
        "sans elle le cycle pousse ses branches mais n'ouvre aucune PR",
    "MOTEUR_API_KEY":
        "sans elle /api répond 503 (auth fail-closed)",
    "MOTEUR_TOOLMAKER_PAT":
        "sans elle le ToolMaker rejette tout outil candidat",
}


@pytest.fixture(scope="module")
def modele() -> str:
    assert EXEMPLE.is_file(), f"{EXEMPLE} introuvable"
    return EXEMPLE.read_text(encoding="utf-8")


@pytest.mark.parametrize("cle,pourquoi", sorted(CLES_A_EFFET_SILENCIEUX.items()))
def test_la_cle_est_documentee_dans_env_example(modele, cle, pourquoi):
    """
    Chaque clé à effet silencieux doit figurer dans le modèle.

    Sinon elle s'évapore au prochain `.env` reconstruit depuis cet exemple, et
    personne ne s'en aperçoit avant de chercher pourquoi rien ne sort.
    """
    lignes = [
        ligne for ligne in modele.splitlines()
        if ligne.strip().startswith(f"{cle}=")
        or ligne.strip().startswith(f"# {cle}=")
    ]
    assert lignes, (
        f"{cle} absente de .env.example — {pourquoi}. "
        "Une clé non documentée disparaît à la prochaine reconstruction du .env."
    )


@pytest.mark.parametrize("cle", sorted(CLES_A_EFFET_SILENCIEUX))
def test_le_modele_ne_porte_aucune_valeur(modele, cle):
    """
    `.env.example` est suivi par git : il déclare des noms, jamais des secrets.

    Le dépôt a déjà connu des clés en clair commitées ; ce test empêche qu'une
    valeur se glisse ici en croyant « aider ».
    """
    for ligne in modele.splitlines():
        nu = ligne.strip()
        if nu.startswith(f"{cle}="):
            valeur = nu.split("=", 1)[1].strip()
            assert not valeur, (
                f"{cle} porte une valeur dans .env.example — ce fichier est "
                f"suivi par git, il ne doit contenir que des noms."
            )


def test_la_raison_est_ecrite_a_cote_de_la_cle(modele):
    """
    Le modèle doit dire POURQUOI la clé compte, pas seulement la nommer.

    Un nom nu se supprime sans hésiter au prochain ménage ; une ligne qui
    explique qu'on obtient « des branches sans PR » survit.
    """
    position = modele.find("MOTEUR_GITHUB_PUSH_TOKEN=")
    assert position > 0, "MOTEUR_GITHUB_PUSH_TOKEN absente"
    contexte = modele[max(0, position - 1200):position]
    assert "PR" in contexte, (
        "aucun commentaire n'explique l'effet de MOTEUR_GITHUB_PUSH_TOKEN "
        "juste au-dessus de sa déclaration"
    )
