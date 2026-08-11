"""
test_github_models_retire.py — Retrait du provider GitHub Models (#T280).

GitHub Models a été entièrement retiré le 30/07/2026 (docs.github.com/github-models) :
l'endpoint models.inference.ai.azure.com répond 404 et le successeur models.github.ai
répond 410 « github_models_retirement_brownout » (vérifié par appels réels le
11/08/2026, voir la PR #T280). Le provider ne doit donc plus exister dans le
registre : la cascade ne doit plus pouvoir le sélectionner — un provider mort
ouvrirait un Circuit Breaker et ferait perdre un tour à la cascade à chaque appel.

Aucun réseau : vérifications sur le registre en mémoire et sur les sources
versionnées uniquement (le garde-fou socket de conftest.py reste actif).
"""

import os
import sys

# Ajout du répertoire parent au PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest

from core.openai_compat_provider import OPENAI_COMPAT_PROVIDERS, create_provider

_END_POINT_RETIRE = "models.inference.ai.azure.com"
_RACINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestGithubModelsRetire:
    """Le provider github (service retiré le 30/07/2026) n'existe plus."""

    def test_provider_github_absent_du_registre(self):
        """Le registre des providers ne contient plus la clé 'github'."""
        assert "github" not in OPENAI_COMPAT_PROVIDERS

    def test_create_provider_github_leve_value_error(self):
        """create_provider('github') échoue : aucun provider à instancier."""
        with pytest.raises(ValueError):
            create_provider("github")

    def test_endpoint_retire_absent_des_sources(self):
        """L'URL morte ne doit plus apparaître dans les sources du provider."""
        for chemin in (
            os.path.join(_RACINE, "core", "openai_compat_provider.py"),
            os.path.join(_RACINE, "core", "llm_gateway.py"),
        ):
            with open(chemin, encoding="utf-8") as f:
                contenu = f.read()
            assert _END_POINT_RETIRE not in contenu, (
                f"URL de l'endpoint retiré encore présente dans {chemin}"
            )
