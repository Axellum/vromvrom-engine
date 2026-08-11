"""
tests/unit/test_bulk_model_status.py — Bascule groupée du statut des modèles (#T243).

La route est appelée directement (pas via HTTP) : on teste sa logique, pas la pile
FastAPI ni l'authentification, déjà couvertes ailleurs. Les accès base sont
monkeypatchés — un test ne doit jamais écrire dans `models_registry.db` partagée.

Scénarios couverts :
  - statut invalide refusé (422) ;
  - nominal : seuls les modèles au statut différent sont écrits ;
  - modèle introuvable signalé sans faire échouer le lot ;
  - doublons d'identifiants dédoublonnés ;
  - échec d'écriture → statut « partiel », les autres passent quand même.
"""

import pytest
from fastapi import HTTPException

from api.routes.context import BulkStatusBody, bulk_set_models_status


@pytest.fixture()
def base(monkeypatch):
    """Faux catalogue en mémoire : {id: statut}. Retourne le dict pour vérification."""
    catalogue = {
        "modele-actif": "active",
        "modele-inactif": "inactive",
        "autre-actif": "active",
    }
    ecritures: list[tuple[str, str]] = []

    def faux_get_model(model_id: str):
        if model_id not in catalogue:
            return None
        return {"id": model_id, "status": catalogue[model_id]}

    def faux_set_status(model_id: str, status: str) -> bool:
        if model_id == "modele-en-echec":
            return False
        catalogue[model_id] = status
        ecritures.append((model_id, status))
        return True

    import core.models_db as models_db

    monkeypatch.setattr(models_db, "get_model", faux_get_model)
    monkeypatch.setattr(models_db, "set_model_status", faux_set_status)
    return catalogue, ecritures


def test_statut_invalide_refuse(base):
    with pytest.raises(HTTPException) as exc:
        bulk_set_models_status(BulkStatusBody(ids=["modele-actif"], status="supprime"))
    assert exc.value.status_code == 422


def test_desactivation_nominale(base):
    catalogue, ecritures = base
    res = bulk_set_models_status(
        BulkStatusBody(ids=["modele-actif", "autre-actif"], status="inactive")
    )
    assert res["status"] == "ok"
    assert sorted(res["modifies"]) == ["autre-actif", "modele-actif"]
    assert catalogue["modele-actif"] == "inactive"
    assert catalogue["autre-actif"] == "inactive"
    assert len(ecritures) == 2


def test_deja_dans_l_etat_non_reecrit(base):
    """Un modèle déjà au bon statut ne doit pas générer d'écriture inutile."""
    _, ecritures = base
    res = bulk_set_models_status(BulkStatusBody(ids=["modele-inactif"], status="inactive"))
    assert res["inchanges"] == ["modele-inactif"]
    assert res["modifies"] == []
    assert ecritures == []


def test_introuvable_ne_fait_pas_echouer_le_lot(base):
    """Une sélection issue d'une liste rafraîchie entre-temps ne doit pas être perdue."""
    catalogue, _ = base
    res = bulk_set_models_status(
        BulkStatusBody(ids=["modele-actif", "modele-fantome"], status="inactive")
    )
    assert res["modifies"] == ["modele-actif"]
    assert res["introuvables"] == ["modele-fantome"]
    assert res["status"] == "ok"
    assert catalogue["modele-actif"] == "inactive"


def test_doublons_dedoublonnes(base):
    _, ecritures = base
    res = bulk_set_models_status(
        BulkStatusBody(ids=["modele-actif", "modele-actif"], status="inactive")
    )
    assert res["modifies"] == ["modele-actif"]
    assert len(ecritures) == 1


def test_echec_d_ecriture_signale_en_partiel(base, monkeypatch):
    catalogue, _ = base
    catalogue["modele-en-echec"] = "active"
    res = bulk_set_models_status(
        BulkStatusBody(ids=["modele-en-echec", "modele-actif"], status="inactive")
    )
    assert res["status"] == "partiel"
    assert res["echecs"] == ["modele-en-echec"]
    # L'échec de l'un n'empêche pas l'autre : le lot n'est pas transactionnel, et
    # c'est voulu — on ne veut pas perdre 40 désactivations pour une ligne verrouillée.
    assert res["modifies"] == ["modele-actif"]


def test_liste_vide_refusee():
    """`min_length=1` : un lot vide est une erreur d'appel, pas un no-op silencieux."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        BulkStatusBody(ids=[], status="inactive")
