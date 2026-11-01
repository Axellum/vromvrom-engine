"""
tests/unit/test_veille_scheduler.py — Planification de la veille (Phase 3).

Couvre les fonctions pures de `core/veille_scheduler.py` : échéance,
construction du message de notification, commutateurs d'environnement.
Aucun appel réseau, aucune boucle asyncio démarrée.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from core.veille_scheduler import (
    _auto_appliquer,
    _enabled,
    _intervalle_secondes,
    construire_message,
    est_du,
)

# ── Échéance ──────────────────────────────────────────────────────────────────


def test_est_du_jamais_passe():
    assert est_du(None, maintenant_ts=1000.0, intervalle_s=3600.0) is True


def test_est_du_periode_ecoulee_ou_non():
    assert est_du(1000.0, maintenant_ts=1000.0 + 86400.0, intervalle_s=86400.0) is True
    assert est_du(1000.0, maintenant_ts=1000.0 + 3599.0, intervalle_s=3600.0) is False


# ── Commutateurs d'environnement ─────────────────────────────────────────────


def test_commutateurs_defaut_et_opt_out(monkeypatch):
    for var in ("MOTEUR_VEILLE_ENABLED", "MOTEUR_VEILLE_APPLIQUER", "MOTEUR_VEILLE_INTERVAL_H"):
        monkeypatch.delenv(var, raising=False)
    assert _enabled() is True
    assert _auto_appliquer() is True
    assert _intervalle_secondes() == 24 * 3600.0

    monkeypatch.setenv("MOTEUR_VEILLE_ENABLED", "0")
    monkeypatch.setenv("MOTEUR_VEILLE_APPLIQUER", "false")
    monkeypatch.setenv("MOTEUR_VEILLE_INTERVAL_H", "6")
    assert _enabled() is False
    assert _auto_appliquer() is False
    assert _intervalle_secondes() == 6 * 3600.0

    # Valeur invalide → repli 24 h ; période plancher 1 h
    monkeypatch.setenv("MOTEUR_VEILLE_INTERVAL_H", "pas-un-nombre")
    assert _intervalle_secondes() == 24 * 3600.0
    monkeypatch.setenv("MOTEUR_VEILLE_INTERVAL_H", "0.1")
    assert _intervalle_secondes() == 1 * 3600.0


# ── Message de notification ──────────────────────────────────────────────────

RAPPORT_APPLIQUE = {
    "genere_le": "2026-08-18T12:00:00+00:00",
    "ecarts_tarifs": [
        {"modele": "glm-5", "input": {"local": 1e-6, "externe": 0.6e-6,
                                       "variation_pct": 66.7}},
    ],
    "application": {
        "journal": [
            {"modele": "glm-5", "statut": "applique", "axes": {"input": {}}},
            {"modele": "modele-x", "statut": "absent_du_json"},
        ],
        "vérification": "ok",
    },
    "nouveaux_par_vendor": {"deepseek": ["deepseek-v3.2"], "xai": ["grok-4.5"]},
}


def test_message_signale_application_et_nouveautes():
    message = construire_message(RAPPORT_APPLIQUE)
    assert message is not None
    assert "1 tarif(s) mis à jour" in message
    assert "2 nouveau(x) modèle(s)" in message
    assert "ÉCHEC" not in message


def test_message_silencieux_si_rien_a_signaler():
    rapport = {
        "genere_le": "2026-08-18T12:00:00+00:00",
        "ecarts_tarifs": [],
        "application": {"journal": [], "vérification": "aucun écart à appliquer"},
        "nouveaux_par_vendor": {},
    }
    assert construire_message(rapport) is None


def test_message_signale_echec_de_verification_et_non_chiffrables():
    rapport = {
        "genere_le": "2026-08-18T12:00:00+00:00",
        "ecarts_tarifs": [
            # Non chiffrable : local payant, externe à zéro → arbitrage humain
            {"modele": "modele-y", "input": {"local": 1e-6, "externe": 0.0,
                                              "variation_pct": None}},
        ],
        "application": {
            "journal": [{"modele": "glm-5", "statut": "applique", "axes": {"input": {}}}],
            "vérification": "ÉCHEC",
            "résiduels": [{"modele": "glm-5"}],
        },
        "nouveaux_par_vendor": {},
    }
    message = construire_message(rapport)
    assert "ÉCHEC" in message and "glm-5" in message
    assert "non chiffrable" in message and "modele-y" in message
