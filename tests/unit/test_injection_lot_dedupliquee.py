"""
tests/unit/test_injection_lot_dedupliquee.py — un lot réinjecté ne double plus.

Le backlog refuse depuis #T397 une `discovery_key` déjà vue, quel que soit le
statut de la tâche d'origine (`completed` compris). Mais `POST /api/backlog/tasks`
ne transmettait jamais ce champ, et `scripts/injecter_taches.py` n'en calculait
aucun : **toute injection de lot passait avec `NULL`**, donc sans protection.

Mesuré le 24/08 sur la prod : `LOT_SEMAINE_2026-08-23_DECK.md` injecté deux
fois → 13 paires de tâches identiques (ids 94-107 et 109-121), chacune payée
deux fois. La plus chère du lot a brûlé 3,77 M de jetons pour finir abandonnée.

Ces tests figent la chaîne complète : l'injecteur produit une clé stable, et
la route la transmet à `add_task`.
"""

from unittest.mock import AsyncMock, patch

import pytest

from scripts.injecter_taches import cle_de_lot

LOT = "LOT_SEMAINE_2026-08-23_DECK.md"
TITRE = "À ANALYSER — Cluster des failed des 48 h"


# --- l'empreinte ----------------------------------------------------------


def test_la_cle_est_stable_pour_un_meme_lot():
    """Réinjecter le même fichier doit produire exactement la même clé."""
    assert cle_de_lot(LOT, TITRE) == cle_de_lot(LOT, TITRE)


def test_les_espaces_de_bord_ne_changent_pas_la_cle():
    """Un titre recopié avec un espace en trop reste la même tâche."""
    assert cle_de_lot(LOT, TITRE) == cle_de_lot(LOT, f"  {TITRE}\n")


def test_deux_titres_du_meme_lot_ont_des_cles_distinctes():
    """Sinon la 2e tâche d'un lot écraserait la 1re."""
    autre = "À ANALYSER — D-19 en prod Deck"
    assert cle_de_lot(LOT, TITRE) != cle_de_lot(LOT, autre)


def test_un_autre_lot_peut_reprendre_le_meme_titre():
    """
    L'empreinte porte le lot ET le titre.

    On veut bloquer la réinjection accidentelle du même fichier, pas interdire
    à jamais de reposer une tâche portant le même intitulé.
    """
    assert cle_de_lot(LOT, TITRE) != cle_de_lot("LOT_SEMAINE_2026-08-30_DECK.md", TITRE)


def test_la_cle_porte_le_prefixe_de_source():
    """Même convention que `board:T396` / `todo:chemin#empreinte`."""
    cle = cle_de_lot(LOT, TITRE)
    assert cle.startswith("lot:")
    assert LOT in cle
    assert "#" in cle


# --- la route -------------------------------------------------------------


@pytest.mark.asyncio
async def test_la_route_transmet_la_cle_a_add_task():
    """
    C'est LE maillon qui manquait : la route ignorait le champ.

    Si `create_task` cesse de le passer, la contrainte d'unicité redevient
    inopérante et le lot double à nouveau.
    """
    from api.routes.backlog import TaskCreate, create_task

    payload = TaskCreate(
        title=TITRE,
        description="Grouper error_message des 48 h.",
        priority=2,
        discovery_key=cle_de_lot(LOT, TITRE),
    )
    with patch("api.routes.backlog.add_task", new=AsyncMock(return_value=42)) as faux:
        reponse = await create_task(payload)

    assert reponse["task_id"] == 42
    assert faux.await_args.kwargs["discovery_key"] == cle_de_lot(LOT, TITRE)


@pytest.mark.asyncio
async def test_une_tache_sans_cle_reste_acceptee():
    """
    Les tâches créées à la main restent libres de se répéter (#T397).

    Le champ est optionnel : on ajoute une protection, on n'impose pas un
    régime nouveau à ce qui marchait.
    """
    from api.routes.backlog import TaskCreate, create_task

    payload = TaskCreate(title="Tâche manuelle", description="…", priority=2)
    with patch("api.routes.backlog.add_task", new=AsyncMock(return_value=7)) as faux:
        reponse = await create_task(payload)

    assert reponse["task_id"] == 7
    assert faux.await_args.kwargs["discovery_key"] is None
