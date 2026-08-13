"""
tests/unit/test_quota_alerte_t334.py — alerte SOLDES et QUOTAS avant la coupure.

Mesuré le 12/08 à 21h : 35 % des tentatives de cascade partaient dans le vide
(51 tentatives, 18 échecs providers, 6 modèles disjonctés) — clé DashScope
morte (`invalid_api_key`), modèle gratuit OpenRouter retiré. **Personne ne
l'a su avant la mesure.** Le cycle complet décidé par Axel : signaler →
désactiver → réactiver. Le volet VALIDITÉ (désactivation) est #T333 ; ce lot
est le volet SOLDES ET QUOTAS : des seuils, une hystérésis, une alerte.

Garde-fous exercés ici :
  - AUCUN appel aux API de facturation : la boucle lit `get_quota_summary()`
    (mocké dans ces tests), c'est-à-dire ce que le collecteur a déjà écrit ;
  - UNE alerte par franchissement, jamais une par cycle ;
  - JAMAIS de secret dans le texte de l'alerte.
Aucun réseau : `get_quota_summary` et `_send_ha_notification` sont mockés.
"""
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
def etat_sous_tension(monkeypatch):
    """Agrégat collecté : une clé au solde sous le seuil (0,35 $ <= 2 $)."""
    etat = {"sous_tension": True}

    def _resume():
        if not etat["sous_tension"]:
            return {"keys": []}
        return {"keys": [{
            "api_key_id": "DEEPSEEK_API_KEY",
            "provider_id": "deepseek",
            "saturation_pct": None,
            "external_balance_usd": 0.35,
            "external_status": "warning",
        }]}

    monkeypatch.setattr("core.models_db.get_quota_summary", _resume)
    return etat


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


# ── Critère d'acceptation : une alerte par franchissement ────────────────────

@pytest.mark.asyncio
async def test_dix_cycles_sous_le_seuil_une_seule_alerte(base_alerte, etat_sous_tension, notifications):
    """Un solde qui reste bas dix cycles produit UNE alerte, pas dix."""
    from core.quota_alert import executer_cycle_alerte

    comptes_rendus = []
    for _ in range(10):
        comptes_rendus.append(await executer_cycle_alerte())

    assert notifications["appels"] == 1, (
        f"{notifications['appels']} notifications pour 10 cycles sous le seuil"
    )
    # Confirmation : l'alerte part au 2e cycle (CONFIRMATION_CYCLES=2), puis
    # le mécanisme reste muet tant que le seuil n'est pas quitté.
    assert [c["nouvelle_alerte"] for c in comptes_rendus] == \
        [False, True] + [False] * 8
    assert comptes_rendus[-1]["en_alerte"] is True
    assert comptes_rendus[-1]["nb_alertes"] == 1


# ── Sortie d'alerte et réarmement ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sortie_puis_nouvelle_chute_rearment(base_alerte, etat_sous_tension, notifications):
    """Deux cycles au-dessus du seuil lèvent l'alerte ; une chute re-alerte."""
    from core.quota_alert import executer_cycle_alerte

    for _ in range(3):  # franchissement initial (alerte au cycle 2)
        await executer_cycle_alerte()
    assert notifications["appels"] == 1

    # Retour à la normale : deux cycles au-dessus du seuil.
    etat_sous_tension["sous_tension"] = False
    for _ in range(2):
        cr = await executer_cycle_alerte()
    assert cr["en_alerte"] is False, "l'état d'alerte doit être levé après 2 cycles au-dessus"

    # Nouvelle chute : l'alerte redevient possible (2e franchissement).
    etat_sous_tension["sous_tension"] = True
    for _ in range(2):
        await executer_cycle_alerte()
    assert notifications["appels"] == 2, (
        "une nouvelle chute après sortie d'alerte doit produire une NOUVELLE alerte"
    )


# ── Jamais de secret dans le texte de l'alerte ───────────────────────────────

@pytest.mark.asyncio
async def test_aucune_cle_dans_le_message_d_alerte(base_alerte, monkeypatch, notifications):
    """Ni clé, ni token, même tronqué : le provider et le chiffre suffisent."""
    from core.quota_alert import executer_cycle_alerte

    valeur_interdite = "sk-" + "VALEUR-QUI-NE-DOIT-JAMAIS-FUIR"
    monkeypatch.setattr("core.models_db.get_quota_summary", lambda: {"keys": [{
        "api_key_id": "DEEPSEEK_API_KEY",
        "provider_id": "deepseek",
        "saturation_pct": None,
        "external_balance_usd": 0.10,
        "external_status": "warning",
    }]})
    # La valeur de la clé ne circule nulle part dans les données lues.
    monkeypatch.setenv("DEEPSEEK_API_KEY", valeur_interdite)

    for _ in range(2):  # confirmation = 2
        await executer_cycle_alerte()

    assert notifications["appels"] == 1
    message = notifications["messages"][0]
    assert valeur_interdite not in message
    assert "sk-" not in message
    # Le message nomme l'id (nom d'environnement), le provider et le chiffre.
    assert "DEEPSEEK_API_KEY" in message
    assert "deepseek" in message
    assert "0.10 $" in message
    assert "seuil 2.00 $" in message


# ── Mode observation : on mesure, on ne notifie pas ──────────────────────────

@pytest.mark.asyncio
async def test_mode_observation_journalise_sans_notifier(base_alerte, etat_sous_tension, notifications, monkeypatch):
    """QUOTA_ALERTE_OBSERVATION=true : le cycle tourne, aucune notification."""
    monkeypatch.setenv("QUOTA_ALERTE_OBSERVATION", "true")
    from core.quota_alert import executer_cycle_alerte

    for _ in range(3):
        cr = await executer_cycle_alerte()

    assert notifications["appels"] == 0
    assert cr["observation"] is True
    # Le franchissement est bien détecté (une fois, par hystérésis) sans notifier.
    assert cr["en_alerte"] is True
    assert cr["nb_alertes"] == 1
    assert cr["notifiee"] is False
