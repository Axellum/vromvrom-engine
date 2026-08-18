"""
tests/unit/test_quota_alerte_t343.py — #T343-B : hystérésis CONSÉCUTIVE et
alerte rejouée si la notification échoue.

Deux défauts mesurés dans la machine à états de `core/quota_alert.py` :
  - (b1) un franchissement pendant une sortie en cours ne remettait pas
    `cycles_au_dessus` à zéro : avec `sortie_cycles = 2`, un cycle au-dessus,
    cinq cycles SOUS le seuil, puis un cycle au-dessus levait l'alerte, alors
    qu'à aucun moment le système n'est resté deux cycles CONSÉCUTIFS au-dessus ;
  - (b2) `_ecrire_etat` persistait `en_alerte = 1` AVANT la notification : si
    Home Assistant était injoignable, la notification était perdue et le cycle
    suivant, « déjà en alerte », ne renotifiait JAMAIS.

Garde-fous de #T334 exercés ici : un solde bas dix cycles produit UNE alerte,
pas dix ; la réémission est bornée à l'échec de notification.
Aucun réseau : `get_quota_summary` et `_send_ha_notification` sont mockés.
"""
import asyncio
from unittest.mock import AsyncMock

import pytest

from core import runtime_db


@pytest.fixture
def base_alerte(tmp_path):
    """Base SQLite temporaire au vrai schéma ; restaurée après le test."""
    original = runtime_db.get_db_path()
    db = tmp_path / "quota_alertes.db"
    runtime_db.override_db_path(str(db))
    runtime_db.get_connection().close()  # déclenche _init_schema (schéma réel)
    yield db
    runtime_db.override_db_path(original)


@pytest.fixture
def resume_sequence(monkeypatch):
    """File de résumés : chaque cycle consomme l'élément suivant."""
    etat = {"cycles": []}

    def _resume():
        if etat["cycles"]:
            return etat["cycles"].pop(0)
        return {"keys": []}

    monkeypatch.setattr("core.models_db.get_quota_summary", _resume)
    return etat


def sous_tension():
    """Une clé au solde sous le seuil (0,35 $ <= 2 $)."""
    return {"keys": [{
        "api_key_id": "DEEPSEEK_API_KEY",
        "provider_id": "deepseek",
        "saturation_pct": None,
        "external_balance_usd": 0.35,
        "external_status": "warning",
    }]}


def normale():
    """Aucune clé sous tension."""
    return {"keys": []}


@pytest.fixture
def notifications(monkeypatch):
    """Compte les notifications HA (aucun réseau)."""
    compteur = {"appels": 0, "messages": []}

    async def _fake_send(title, message):
        compteur["appels"] += 1
        compteur["messages"].append(message)
        return True

    monkeypatch.setattr(
        "core.daemon_loop._send_ha_notification", AsyncMock(side_effect=_fake_send)
    )
    return compteur


# ── (b1) La sortie d'alerte exige des cycles CONSÉCUTIFS ─────────────────────

@pytest.mark.asyncio
async def test_franchissement_pendant_la_sortie_annule_la_sortie(
    base_alerte, resume_sequence, notifications, monkeypatch
):
    """Au-dessus / sous / au-dessus avec sortie_cycles = 2 → l'alerte n'est PAS
    levée : la sortie exige deux cycles consécutifs au-dessus du seuil."""
    monkeypatch.setenv("QUOTA_ALERTE_SORTIE_CYCLES", "2")
    from core.quota_alert import executer_cycle_alerte

    resume_sequence["cycles"] = (
        [sous_tension()] * 2    # franchissement : alerte au 2e cycle
        + [normale()]           # 1 cycle au-dessus : sortie en cours
        + [sous_tension()] * 5  # re-franchissement : annule la sortie
        + [normale()]           # 1 cycle au-dessus : toujours PAS 2 consécutifs
    )

    comptes_rendus = []
    for _ in range(9):
        comptes_rendus.append(await executer_cycle_alerte())

    assert comptes_rendus[-1]["en_alerte"] is True, (
        "l'alerte ne doit PAS être levée : aucun moment avec 2 cycles "
        "consécutifs au-dessus du seuil"
    )

    # Deux cycles au-dessus CONSÉCUTIFS lèvent enfin l'alerte.
    resume_sequence["cycles"] = [normale(), normale()]
    cr = await executer_cycle_alerte()
    cr = await executer_cycle_alerte()
    assert cr["en_alerte"] is False


# ── (b2) Une notification échouée est retentée au cycle suivant ──────────────

@pytest.mark.asyncio
async def test_notification_echouee_retentee_jusqu_au_succes(
    base_alerte, resume_sequence, monkeypatch
):
    """Test central : première notification en échec → le cycle suivant retente ;
    notification en succès → le cycle suivant ne renotifie pas."""
    compteur = {"appels": 0, "echecs_restants": 1}

    async def _fake_send(title, message):
        compteur["appels"] += 1
        if compteur["echecs_restants"] > 0:
            compteur["echecs_restants"] -= 1
            return False
        return True

    monkeypatch.setattr(
        "core.daemon_loop._send_ha_notification", AsyncMock(side_effect=_fake_send)
    )
    from core.quota_alert import executer_cycle_alerte

    resume_sequence["cycles"] = [sous_tension()] * 4

    comptes_rendus = []
    for _ in range(4):
        comptes_rendus.append(await executer_cycle_alerte())

    # Cycle 2 : franchissement confirmé, notification en échec → l'état
    # « en alerte » n'est PAS persisté, la machine doit rester prête à retenter.
    assert comptes_rendus[1]["nouvelle_alerte"] is True
    assert comptes_rendus[1]["notifiee"] is False
    assert comptes_rendus[1]["en_alerte"] is False
    # Cycle 3 : retentée → succès → l'alerte est posée.
    assert comptes_rendus[2]["nouvelle_alerte"] is True
    assert comptes_rendus[2]["notifiee"] is True
    assert comptes_rendus[2]["en_alerte"] is True
    # Cycle 4 : déjà notifiée → plus rien.
    assert comptes_rendus[3]["nouvelle_alerte"] is False
    assert comptes_rendus[3]["notifiee"] is False
    assert comptes_rendus[3]["en_alerte"] is True
    assert compteur["appels"] == 2, (
        f"{compteur['appels']} notifications : une seule retentée après l'échec"
    )


# ── Concurrence : deux cycles en parallèle ne renotifient pas ───────────────

@pytest.mark.asyncio
async def test_cycles_concurrents_une_seule_notification(
    base_alerte, resume_sequence, monkeypatch
):
    """Boucle de fond + bouton IHM en parallèle → UNE seule notification :
    sans verrou, le second cycle lirait un état pas encore persisté pendant
    l'aller-retour de notification et renotifierait (revue Bugbot PR #302)."""
    compteur = {"appels": 0}

    async def _fake_send_lent(title, message):
        compteur["appels"] += 1
        await asyncio.sleep(0.1)  # round-trip HA simulé : la fenêtre de concurrence
        return True

    monkeypatch.setattr(
        "core.daemon_loop._send_ha_notification",
        AsyncMock(side_effect=_fake_send_lent),
    )
    from core.quota_alert import executer_cycle_alerte

    # Pré-condition : un premier cycle a posé cycles_sous_seuil=1 (pas encore
    # en alerte, confirmation_cycles=2).
    resume_sequence["cycles"] = [sous_tension()]
    await executer_cycle_alerte()

    # Deux cycles partent ENSEMBLE : sans verrou, les deux liraient l'état
    # périmé (en_alerte=0) et notifieraient chacun de leur côté.
    resume_sequence["cycles"] = [sous_tension(), sous_tension()]
    comptes_rendus = await asyncio.gather(
        executer_cycle_alerte(), executer_cycle_alerte()
    )

    assert compteur["appels"] == 1, (
        f"{compteur['appels']} notifications pour un seul franchissement — "
        "la garantie « une alerte par franchissement » de #T334 est rompue"
    )
    assert sum(1 for c in comptes_rendus if c["nouvelle_alerte"]) == 1
    assert all(c["en_alerte"] for c in comptes_rendus)


# ── Non-régression #T334 : UNE alerte, pas une par cycle ─────────────────────

@pytest.mark.asyncio
async def test_dix_cycles_sous_le_seuil_une_seule_notification(
    base_alerte, resume_sequence, notifications
):
    """Un solde qui reste bas dix cycles (notification réussie) → UNE seule
    notification : la réémission est bornée à l'échec, jamais une par cycle."""
    from core.quota_alert import executer_cycle_alerte

    resume_sequence["cycles"] = [sous_tension()] * 10

    comptes_rendus = []
    for _ in range(10):
        comptes_rendus.append(await executer_cycle_alerte())

    assert notifications["appels"] == 1
    assert [c["nouvelle_alerte"] for c in comptes_rendus] == \
        [False, True] + [False] * 8
    assert comptes_rendus[-1]["en_alerte"] is True
