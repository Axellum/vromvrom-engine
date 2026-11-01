"""
tests/unit/test_rendement_dreamcoder.py — Compteur de rendement DreamCoder.

Vérifie l'entonnoir tenté → poussé → récolté → mergé, le ratio tokens par
PR mergée (None quand le dénominateur est 0, jamais une division par zéro),
la déduplication des stades rejouables, et la reconstruction depuis les
rapports de cycle.
"""

import json

import pytest

from core import rendement_dreamcoder as rd


@pytest.fixture
def etat_isole(tmp_path, monkeypatch):
    """Isole l'état et les rapports dans un répertoire temporaire."""
    monkeypatch.setattr(rd, "_STATE_FILE", tmp_path / "rendement.json")
    monkeypatch.setattr(rd, "_RAPPORTS_DIR", tmp_path / "rapports")
    return tmp_path


def _resultats_cycle(tentees=3, succes=2, poussees=2, tokens=1000, cout=0.5):
    return {
        "processed": tentees,
        "success": succes,
        "branches_pushed": [f"task/{i}_1" for i in range(poussees)],
        "total_tokens": tokens,
        "total_cost_usd": cout,
        "stopped_reason": "backlog_empty",
    }


def test_entonnoir_agrege_les_cycles(etat_isole):
    rd.enregistrer_cycle(_resultats_cycle())
    rd.enregistrer_cycle(_resultats_cycle(tentees=1, succes=1, poussees=0, tokens=200, cout=0.1))

    vue = rd.get_rendement()
    assert vue["entonnoir"] == {"tentees": 4, "reussies": 3, "poussees": 2,
                                "recoltees": 0, "mergees": 0}
    assert vue["tokens_total"] == 1200
    assert vue["cost_usd_total"] == pytest.approx(0.6)
    # Aucune PR mergée : le ratio doit être None, pas 0 ni une exception.
    assert vue["tokens_par_pr_mergee"] is None
    assert vue["cost_usd_par_pr_mergee"] is None


def test_cycle_vide_nalimente_pas(etat_isole):
    rd.enregistrer_cycle({"processed": 0, "success": 0})
    assert rd.get_rendement()["entonnoir"]["tentees"] == 0


def test_recolte_dedupliquee_par_branche(etat_isole):
    rd.enregistrer_recolte([{"branche": "task/7_1", "pr_url": "https://pr/1"}])
    # Rejeu : la même branche ne doit pas être recomptée.
    rd.enregistrer_recolte([{"branche": "task/7_1", "pr_url": "https://pr/1"}])

    vue = rd.get_rendement()
    assert vue["entonnoir"]["recoltees"] == 1
    assert vue["branches_recoltees"]["task/7_1"]["pr_url"] == "https://pr/1"


def test_merges_deduplies_par_numero(etat_isole):
    rd.enregistrer_cycle(_resultats_cycle(tokens=1500))
    rd.enregistrer_merges([{"number": 380, "branche": "task/7_1"}])
    rd.enregistrer_merges([{"number": 380, "branche": "task/7_1"},
                           {"number": 381, "branche": "task/8_1"}])

    vue = rd.get_rendement()
    assert vue["entonnoir"]["mergees"] == 2
    # Le chiffre central : tokens PAR PR mergée.
    assert vue["tokens_par_pr_mergee"] == pytest.approx(750.0)
    assert vue["taux"]["merge_sur_recolte"] is None  # aucune récolte déclarée


def test_reconstruction_depuis_rapports_sans_double_compte(etat_isole):
    rapports = etat_isole / "rapports"
    rapports.mkdir()
    (rapports / "cycle_2026-08-18_020000.json").write_text(
        json.dumps({"processed": 5, "success": 4, "branches_pushed": ["task/1_1"],
                    "total_tokens": 5000, "total_cost_usd": 1.25}),
        encoding="utf-8")

    premiere = rd.get_rendement()
    assert premiere["entonnoir"]["tentees"] == 5
    assert premiere["tokens_total"] == 5000

    seconde = rd.get_rendement()
    # Le rapport est marqué comptabilisé : pas de double agrégation.
    assert seconde["entonnoir"]["tentees"] == 5


def test_taux_push_sur_reussite_avec_zero_reussies(etat_isole):
    """Vérifie que push_sur_reussite est None quand reussies = 0.
    
    Ce test échouera si le code ne protège pas correctement contre la
    division par zéro quand aucune tâche n'a réussi.
    """
    # Cycle avec succes=0 mais poussees > 0
    rd.enregistrer_cycle(_resultats_cycle(tentees=5, succes=0, poussees=3))

    vue = rd.get_rendement()
    assert vue["entonnoir"]["reussies"] == 0
    assert vue["entonnoir"]["poussees"] == 3
    # Le taux doit être None, pas une exception ZeroDivisionError
    assert vue["taux"]["push_sur_reussite"] is None


def test_taux_avec_denominateurs_nuls(etat_isole):
    """Vérifie que les taux avec dénominateurs nuls sont None.
    
    Cas: tentatives sans succès, sans push, sans récolte, sans merge.
    - reussite = 0% (légitime: 0 succès sur 10 tentatives)
    - push_sur_reussite = None (pas de succès donc pas de push possible)
    - recolte_sur_pousse = None (pas de push donc pas de récolte)
    - merge_sur_recolte = None (pas de récolte donc pas de merge)
    """
    rd.enregistrer_cycle(_resultats_cycle(tentees=10, succes=0, poussees=0))

    vue = rd.get_rendement()
    
    # Le taux de réussite est 0% (légitime) car on a fait des tentatives
    assert vue["entonnoir"]["tentees"] == 10
    assert vue["entonnoir"]["reussies"] == 0
    assert vue["taux"]["reussite"] == 0.0
    
    # Les autres taux doivent être None (pas de dénominateur)
    assert vue["taux"]["push_sur_reussite"] is None
    assert vue["taux"]["recolte_sur_pousse"] is None
    assert vue["taux"]["merge_sur_recolte"] is None


def test_taux_merge_sur_recolte_avec_zero_recoltees(etat_isole):
    """Vérifie que merge_sur_recolte est None quand recoltees = 0.
    
    Ce test vérifie le cas: des merges existent mais aucune récolte déclarée.
    """
    rd.enregistrer_cycle(_resultats_cycle(tentees=3, succes=2, poussees=2))
    rd.enregistrer_merges([{"number": 1, "branche": "task/1_1"}])

    vue = rd.get_rendement()
    
    # mergees > 0 mais recoltees = 0 → le taux doit être None
    assert vue["entonnoir"]["mergees"] == 1
    assert vue["entonnoir"]["recoltees"] == 0
    assert vue["taux"]["merge_sur_recolte"] is None


def test_taux_recolte_sur_pousse_avec_zero_poussees(etat_isole):
    """Vérifie que recolte_sur_pousse est None quand poussees = 0."""
    rd.enregistrer_cycle(_resultats_cycle(tentees=5, succes=3, poussees=0))
    # Pas de récolte car pas de branches poussées

    vue = rd.get_rendement()
    
    assert vue["entonnoir"]["poussees"] == 0
    assert vue["entonnoir"]["recoltees"] == 0
    assert vue["taux"]["recolte_sur_pousse"] is None


# ──────────────────────────────────────────────────────────────────────
# [#T421] Double comptage : le compte live et la reconstruction lisaient le
# même cycle sans se le dire. Mesuré le 20/08 sur la vraie base : le premier
# appel à /api/dreamcoder/rendement a fait passer le cumul de 31 à 62 tentées
# et de 35,4 M à 70,8 M de tokens — le KPI qui sert à juger si l'architecture
# autonome coûte plus qu'elle ne rapporte, faux du simple au double.
# ──────────────────────────────────────────────────────────────────────


def _ecrire_rapport(dossier, nom, resultats):
    """Écrit un `cycle_*.json` comme le fait `_save_dreamcoder_cycle_report`."""
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / nom).write_text(json.dumps(resultats), encoding="utf-8")
    return nom


def test_un_cycle_compte_live_n_est_pas_recompte_par_la_reconstruction(etat_isole):
    """Le nom du rapport inscrit le cycle au registre : la reconstruction passe son tour."""
    resultats = _resultats_cycle(tentees=3, succes=2, poussees=2, tokens=1000, cout=0.5)
    nom = _ecrire_rapport(etat_isole / "rapports", "cycle_2026-08-20_120000.json", resultats)

    rd.enregistrer_cycle(resultats, nom)
    vue = rd.get_rendement()  # déclenche reconstruire_depuis_rapports()

    assert vue["entonnoir"]["tentees"] == 3
    assert vue["entonnoir"]["poussees"] == 2
    assert vue["tokens_total"] == 1000


def test_sans_nom_de_rapport_le_cycle_est_recompte(etat_isole):
    """
    Le défaut d'origine, gardé sous test : c'est le nom du rapport — et rien
    d'autre — qui empêche le doublement. Si ce test se met à échouer parce que
    le doublement a disparu, c'est qu'une autre protection a été ajoutée ; il
    faudra alors vérifier laquelle avant de supprimer ce garde.
    """
    resultats = _resultats_cycle(tentees=3, succes=2, poussees=2, tokens=1000, cout=0.5)
    _ecrire_rapport(etat_isole / "rapports", "cycle_2026-08-20_120000.json", resultats)

    rd.enregistrer_cycle(resultats)  # sans nom : le registre reste vide
    vue = rd.get_rendement()

    assert vue["entonnoir"]["tentees"] == 6
    assert vue["tokens_total"] == 2000


def test_recalculer_repare_un_etat_double(etat_isole):
    """
    La réparation repart des rapports, seule source durable.

    Une simple division par deux ne suffirait pas : sur une machine où la liste
    des cycles détaillés est tronquée (200 max), le facteur n'est pas uniforme.
    """
    resultats = _resultats_cycle(tentees=3, succes=2, poussees=2, tokens=1000, cout=0.5)
    _ecrire_rapport(etat_isole / "rapports", "cycle_2026-08-20_120000.json", resultats)

    rd.enregistrer_cycle(resultats)      # compté live…
    rd.reconstruire_depuis_rapports()    # …puis recompté : état faussé
    assert rd.get_rendement()["entonnoir"]["tentees"] == 6

    # Les stades aval ne viennent pas des rapports : ils doivent survivre.
    rd.enregistrer_recolte([{"branche": "task/1_1", "pr_url": "https://github.com/x/y/pull/1"}])

    etat = rd.recalculer_totaux_depuis_rapports()

    assert etat["tentees"] == 3
    assert etat["reussies"] == 2
    assert etat["poussees"] == 2
    assert etat["tokens_total"] == 1000
    assert etat["cost_usd_total"] == pytest.approx(0.5)
    assert "task/1_1" in etat["branches_recoltees"]
