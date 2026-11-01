"""
tests/unit/test_backlog_expurgation.py — Aucune cle en clair dans le backlog (#T402).

Le masquage des secrets existe depuis #T262 (une cle Gemini retrouvee en clair
dans le journal systemd le 10/08). Il est installe sur les HANDLERS DE
JOURNALISATION : ce qui est ecrit en base ne passe jamais par la.

Mesure le 18/08 a 22h sur la production : `backlog_tasks.error_message` portait
la cle Gemini en clair sur **5 lignes**. Chemin identique a celui de 2026-08-10
-- `raise_for_status()` leve une HTTPError dont le message contient l'URL
complete, et le drainage DreamCoder ecrit cette chaine telle quelle dans la
tache.
"""

import pytest

from core.backlog_db import (
    _expurger,
    add_task,
    get_task_by_id,
    init_backlog_db,
    update_task_status,
)

# Forme exacte du message releve en base le 18/08 (cle remplacee par un leurre).
MESSAGE_REEL = (
    "429 Client Error: Too Many Requests for url: "
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-3.5-flash:generateContent?key=AQ.Ab8RN6IFAUSSECLEDETEST123456"
)


def test_la_cle_dune_url_est_masquee():
    propre = _expurger(MESSAGE_REEL)
    assert "AQ.Ab8RN6IFAUSSECLEDETEST123456" not in propre
    # Le contexte doit rester lisible : on veut savoir QUEL parametre a saute.
    assert "key=" in propre
    assert "429 Client Error" in propre
    assert "generativelanguage.googleapis.com" in propre


@pytest.mark.parametrize("valeur", [None, 0, 42, 3.5, True])
def test_les_valeurs_non_texte_passent_intactes(valeur):
    """tokens_used, retries... ne doivent surtout pas etre transformes."""
    assert _expurger(valeur) is valeur


def test_un_message_ordinaire_nest_pas_abime():
    message = "Le pipeline s'est termine sur un echec : la branche est identique a master."
    assert _expurger(message) == message


@pytest.mark.asyncio
async def test_rien_narrive_en_base_avec_une_cle(tmp_path, monkeypatch):
    """Bout en bout : ce que le drainage ecrit vraiment dans la tache."""
    import core.backlog_db as bdb

    base = tmp_path / "runtime_test.db"
    monkeypatch.setattr(bdb, "get_db_path", lambda: str(base))
    await init_backlog_db()

    tid = await add_task("Tache de test", "description", priority=2)
    await update_task_status(tid, "failed", error_message=MESSAGE_REEL, retries=1)

    tache = await get_task_by_id(tid)
    assert "AQ.Ab8RN6IFAUSSECLEDETEST123456" not in (tache["error_message"] or "")
    assert "key=" in tache["error_message"]
    # Les champs numeriques survivent au passage.
    assert tache["retries"] == 1
