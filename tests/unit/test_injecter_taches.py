"""
tests/unit/test_injecter_taches.py — Chargement d'un lot de tâches (#T387).

Le script prépare le gros prompt qui alimentera les nuits autonomes. Ce qui
compte ici, c'est ce qui BLOQUE l'envoi : une description trop maigre ou deux
tâches homonymes coûtent une nuit de travail et une PR à jeter, et le défaut
n'est visible qu'au matin.
"""

import importlib.util
from pathlib import Path

_CHEMIN = Path(__file__).resolve().parents[2] / "scripts" / "injecter_taches.py"
_spec = importlib.util.spec_from_file_location("injecter_taches", _CHEMIN)
injecter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(injecter)


DESCRIPTION = ("Une description assez longue pour passer le seuil de validation "
               "et décrire un vrai travail exécutable seul.")


# ── Lecture du Markdown ──────────────────────────────────────────────────────

def test_decoupe_une_section_par_tache():
    md = f"""# Lot du soir

## [P1] Première tâche
{DESCRIPTION}

## [P3] Seconde tâche
{DESCRIPTION}
"""
    taches = injecter.taches_depuis_markdown(md)

    assert [t["title"] for t in taches] == ["Première tâche", "Seconde tâche"]
    assert [t["priority"] for t in taches] == [1, 3]


def test_priorite_par_defaut():
    taches = injecter.taches_depuis_markdown(f"## Sans priorité\n{DESCRIPTION}\n")
    assert taches[0]["priority"] == 2


def test_section_vide_ignoree():
    """Un titre sans corps n'est pas une tâche — l'agent n'aurait rien à faire."""
    md = f"## Titre seul\n\n## Vraie tâche\n{DESCRIPTION}\n"
    taches = injecter.taches_depuis_markdown(md)
    assert len(taches) == 1
    assert taches[0]["title"] == "Vraie tâche"


def test_le_titre_de_niveau_1_n_est_pas_une_tache():
    md = f"# Titre du document\nBlabla d'introduction.\n\n## Vraie tâche\n{DESCRIPTION}\n"
    assert len(injecter.taches_depuis_markdown(md)) == 1


def test_markdown_vide():
    assert injecter.taches_depuis_markdown("") == []


def test_bom_utf8_ne_saute_pas_la_premiere_section():
    """Notepad / PowerShell Set-Content posent un BOM : ^## rate la 1re tâche."""
    md = f"\ufeff## [P1] Première tâche\n{DESCRIPTION}\n\n## [P2] Seconde tâche\n{DESCRIPTION}\n"
    taches = injecter.taches_depuis_markdown(md)
    assert [t["title"] for t in taches] == ["Première tâche", "Seconde tâche"]


# ── Ce qui bloque l'envoi ────────────────────────────────────────────────────

def test_description_trop_maigre_bloque():
    soucis = injecter.problemes([{"title": "T", "description": "fais le truc", "priority": 2}])
    assert any("trop maigre" in s for s in soucis)


def test_titre_en_double_bloque():
    """
    Deux tâches homonymes produisent deux branches quasi identiques : le
    parallélisme les joue en même temps, et l'une des deux PR part à la poubelle.
    """
    lot = [{"title": "Corriger le routeur", "description": DESCRIPTION, "priority": 1},
           {"title": "corriger le routeur", "description": DESCRIPTION, "priority": 2}]
    assert any("double" in s for s in injecter.problemes(lot))


def test_priorite_hors_bornes_bloque():
    soucis = injecter.problemes([{"title": "T", "description": DESCRIPTION, "priority": 9}])
    assert any("hors de 0-3" in s for s in soucis)


def test_lot_correct_ne_bloque_pas():
    lot = [{"title": "Tâche A", "description": DESCRIPTION, "priority": 1},
           {"title": "Tâche B", "description": DESCRIPTION, "priority": 3}]
    assert injecter.problemes(lot) == []


def test_cle_api_moteur_lit_le_env_local(tmp_path, monkeypatch):
    """Sur le Deck, pas besoin de SSH : le .env du CWD suffit."""
    env = tmp_path / ".env"
    env.write_text("MOTEUR_API_KEY=cle-locale-test\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert injecter.cle_api_moteur() == "cle-locale-test"


def test_cle_api_moteur_url_locale_lit_env(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("MOTEUR_API_KEY=cle-pc\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert injecter.cle_api_moteur("http://127.0.0.1:8000") == "cle-pc"


class _Proc:
    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode


def test_cle_api_moteur_deck_ignore_la_cle_pc(tmp_path, monkeypatch):
    """POST Deck avec la clé PC = 401. L'URL impose le SSH."""
    env = tmp_path / ".env"
    env.write_text("MOTEUR_API_KEY=cle-pc-ne-pas-utiliser\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        injecter.subprocess, "run",
        lambda *a, **k: _Proc("cle-deck-ssh\n"),
    )
    assert injecter.cle_api_moteur("http://192.168.1.10:8000") == "cle-deck-ssh"


def test_cle_api_moteur_deck_sans_ssh_ne_retombe_pas_sur_pc(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("MOTEUR_API_KEY=cle-pc\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        injecter.subprocess, "run",
        lambda *a, **k: _Proc("", returncode=1),
    )
    assert injecter.cle_api_moteur("http://192.168.1.10:8000") == ""


# ── Le rappel de contexte ────────────────────────────────────────────────────

def test_le_preambule_porte_les_regles_qui_ont_deja_coute_du_travail():
    """
    L'agent de nuit n'a ni le board ni l'historique : ce qui n'est pas dans la
    description n'existe pas pour lui. Trois rappels sont non négociables.
    """
    p = injecter.PREAMBULE
    assert "ABSOLUS" in p                       # chemins hors du clone sinon
    assert "a pu être faite" in p               # 3 tâches périmées sur 6 le 18/08
    assert "vérification réelle" in p           # « c'est fait » à l'aveugle
    # Une copie de fichier qui passe par la fenêtre du modèle en ressort
    # tronquée : `AUDIT_IHM_V2.md` est revenu à 83 lignes sur 264, contenu
    # authentique mais amputé, sans qu'aucune erreur ne soit levée.
    assert "git checkout" in p
    assert "tronqué" in p


def test_le_preambule_distingue_les_commandes_du_code_produit():
    """
    [#T390] Le préambule réclamait des « chemins ABSOLUS » sans distinguer les
    commandes de l'agent du code qu'il écrit. Il a appliqué la consigne jusque
    dans un test livré, qui embarquait
    `/opt/vromvrom-engine_dc3/scripts/…` : le test passait dans
    le slot où il avait été écrit, et nulle part ailleurs.

    Une consigne trop large produit son propre défaut — ici, du code non
    portable qui a l'air vérifié.
    """
    p = injecter.PREAMBULE
    assert "ABSOLUS pour tes propres commandes" in p
    assert "JAMAIS contenir de chemin absolu" in p
    assert "tmp_path" in p
