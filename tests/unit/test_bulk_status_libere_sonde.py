from unittest.mock import MagicMock, patch

from api.routes.context import bulk_set_models_status


def test_bulk_status_libere_sonde():
    # get_model / set_model_status sont importés dans la fonction,
    # pas au niveau du module : patcher core.models_db.
    with patch("core.models_db.get_model") as mock_get, patch(
        "core.models_db.set_model_status"
    ) as mock_set:
        mock_get.return_value = {
            "id": "m1",
            "status": "inactive",
            "desactive_par_sonde": 1,
        }
        mock_set.return_value = True

        body = MagicMock()
        body.ids = ["m1"]
        body.status = "inactive"

        result = bulk_set_models_status(body)

        # Même statut : on appelle quand même set_model_status pour
        # relâcher la revendication de la sonde (#T380).
        assert mock_set.called
        assert "m1" in result["modifies"]
        assert "m1" not in result["inchanges"]


def test_bulk_status_introuvable():
    with patch("core.models_db.get_model") as mock_get:
        mock_get.return_value = None

        body = MagicMock()
        body.ids = ["unknown"]
        body.status = "active"

        result = bulk_set_models_status(body)
        assert "unknown" in result["introuvables"]
