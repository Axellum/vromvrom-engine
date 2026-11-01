"""
[#T422] Une erreur de pipeline doit dire de QUOI elle est faite.

`str(KeyError("verify_fix"))` rend `'verify_fix'` : un mot entre guillemets,
sans type, sans origine. Mesuré le 21/08 au matin — deux tâches autonomes sur
quatre mortes sur `❌ 'verify_fix'` et `❌ 'verify_implementation'`, des noms
d'étapes inventés par le planner et introuvables dans tout le dépôt. Le journal
ne disait même pas qu'il s'agissait d'une clé absente.
"""

import pytest

from services.pipeline_service import detail_exception


@pytest.mark.parametrize(
    "exception, attendu",
    [
        # Le cas réel : sans le type, `'verify_fix'` ne veut rien dire.
        (KeyError("verify_fix"), "KeyError: 'verify_fix'"),
        (ValueError("plan vide"), "ValueError: plan vide"),
        (AttributeError("'NoneType' object has no attribute 'id'"),
         "AttributeError: 'NoneType' object has no attribute 'id'"),
    ],
)
def test_le_type_precede_toujours_le_message(exception, attendu):
    assert detail_exception(exception) == attendu


@pytest.mark.parametrize("exception", [RuntimeError(), TimeoutError(), KeyError()])
def test_sans_message_le_type_suffit(exception):
    """Un `str()` vide ne doit pas produire « ❌  » — le type porte l'information."""
    detail = detail_exception(exception)
    assert detail == type(exception).__name__
    assert detail.strip()


def test_un_message_blanc_est_traite_comme_absent():
    assert detail_exception(ValueError("   ")) == "ValueError"
