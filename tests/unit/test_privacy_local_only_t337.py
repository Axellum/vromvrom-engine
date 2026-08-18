"""
tests/unit/test_privacy_local_only_t337.py — Mode confidentialité « local_only » (FAIL-CLOSED).

[T337] Quand `MOTEUR_PRIVACY_LEVEL=local_only` (ou le paramètre d'appel
`privacy_level="local_only"`) est actif, AUCUNE requête ne doit partir vers un
provider cloud : seuls les modèles servis par un provider LOCAL sont retenus, et
si aucun n'est disponible dans le tier demandé, l'appel ÉCHOUE explicitement —
jamais de repli silencieux vers le cloud.

Le point central vérifié ici (test_privacy_local_only_aucun_local_echoue) est le
FAIL-CLOSED : mode actif + aucun modèle local → l'appel lève une erreur explicite,
et le test prouve qu'AUCUN provider cloud n'a été instancié ni appelé.
"""

import pytest

from core.llm.providers.base import LLMProvider
from core.llm.providers.deepseek import FallbackProvider
from core.llm_gateway import (
    MOTEUR_PRIVACY_LEVEL_ENV,
    MOTEUR_PRIVACY_LOCAL_ONLY,
    LLMGateway,
)

# Endpoints types : un provider local (loopback / LAN privé) et un provider cloud (domaine public).
BASE_URL_LOCAL = "http://127.0.0.1:11434/v1/chat/completions"
BASE_URL_CLOUD = "https://api.deepseek.com/chat/completions"


class _FakeProvider(LLMProvider):
    """Provider factice avec un endpoint et un compteur d'appels.

    Le `base_url` sert de marqueur de localité : `_is_local_provider` le lit pour
    décider si le provider appartient à la famille locale (cas réel en prod, où
    tous les providers locaux — Ollama, LM Studio, Deck Edge AI — ont un endpoint
    local, et tous les clouds un endpoint public).
    """

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.calls = 0

    def generate(self, system_prompt, user_prompt, **kwargs):
        self.calls += 1
        return "réponse factice suffisamment longue pour l'heuristique de qualité"

    def generate_structured(self, system_prompt, user_prompt, schema, **kwargs):
        self.calls += 1
        return {"ok": True}


@pytest.fixture
def gateway():
    return LLMGateway()


def _config_tier(model_names: list) -> dict:
    """Config minimale : le tier 'leger' ne contient QUE les modèles donnés."""
    return {"tiers": {"leger": model_names}, "routing_policy": {"excluded_models": []}}


@pytest.fixture(autouse=True)
def _isoler_db_tiers(monkeypatch):
    """Force la résolution de tier à repasser par config.json (la DB est ignorée)."""
    import core.models_db as models_db

    monkeypatch.setattr(models_db, "get_models_for_tier", lambda _tier: [])
    yield


@pytest.fixture(autouse=True)
def _env_privacy_vide(monkeypatch):
    """Isolation : variable d'environnement vide par défaut (comportement historique)."""
    monkeypatch.delenv(MOTEUR_PRIVACY_LEVEL_ENV, raising=False)
    yield


def test_privacy_local_only_aucun_local_echoue_fail_closed(gateway):
    """POINT CENTRAL — FAIL-CLOSED.

    Mode actif + AUCUN modèle local dans le tier demandé → l'appel échoue
    explicitement (ValueError) et le test prouve qu'AUCUN provider cloud n'a été
    instancié ni appelé : le seul modèle candidat est cloud, il est écarté, et
    l'appel lève AVANT de construire un FallbackProvider.
    """
    cloud_provider = _FakeProvider(BASE_URL_CLOUD)
    gateway.providers["modele-cloud-unique"] = cloud_provider
    config = _config_tier(["modele-cloud-unique"])

    with pytest.raises(ValueError, match="aucun modèle local"):
        gateway.get_provider_for_tier("leger", config, privacy_level=MOTEUR_PRIVACY_LOCAL_ONLY)

    # Aucun provider cloud appelé.
    assert cloud_provider.calls == 0
    # Le modèle cloud est bien identifié comme NON local (donc écarté) : la preuve
    # que le cloud n'a pas été retenu dans une liste de repli.
    assert gateway._is_local_provider("modele-cloud-unique") is False


def test_privacy_local_only_retenu_local_aucun_cloud_en_repli(gateway):
    """Mode actif + un local disponible → le local est retenu et AUCUN cloud ne
    reste dans la liste de repli du FallbackProvider (on vérifie le CONTENU de la
    liste, pas seulement le premier élément — sinon le repli cloud resterait armé)."""
    local_provider = _FakeProvider(BASE_URL_LOCAL)
    cloud_provider = _FakeProvider(BASE_URL_CLOUD)
    gateway.providers["mon-modele-local"] = local_provider
    gateway.providers["modele-cloud"] = cloud_provider
    config = _config_tier(["mon-modele-local", "modele-cloud"])

    _tier, provider = gateway.get_provider_for_tier(
        "leger", config, privacy_level=MOTEUR_PRIVACY_LOCAL_ONLY
    )

    assert isinstance(provider, FallbackProvider)
    # Le contenu complet de la liste ne contient QUE le local — aucun cloud armé
    # derrière en repli.
    modeles_retenus = [m for m, _ in provider.providers]
    assert modeles_retenus == ["mon-modele-local"]
    assert "modele-cloud" not in modeles_retenus
    # Le provider local est bien celui qui a été retenu.
    assert provider.providers[0][1].provider is local_provider
    # Jamais d'appel réseau pendant la résolution.
    assert local_provider.calls == 0
    assert cloud_provider.calls == 0


def test_privacy_inactif_liste_identique_avant_changement(gateway):
    """Mode inactif (défaut) → la liste retenue est IDENTIQUE à celle d'avant le
    changement : le filtrage local ne s'applique PAS, tous les modèles candidats
    (locaux ET cloud) du tier sont conservés."""
    local_provider = _FakeProvider(BASE_URL_LOCAL)
    cloud_provider = _FakeProvider(BASE_URL_CLOUD)
    gateway.providers["mon-modele-local"] = local_provider
    gateway.providers["modele-cloud"] = cloud_provider
    config = _config_tier(["mon-modele-local", "modele-cloud"])

    _tier, provider = gateway.get_provider_for_tier("leger", config)

    modeles_retenus = set(m for m, _ in provider.providers)
    # Les deux modèles (local + cloud) sont conservés : comportement historique.
    assert modeles_retenus == {"mon-modele-local", "modele-cloud"}


def test_privacy_inactif_par_env_vide_comme_parametre_absent(gateway):
    """Vide via la variable d'environnement = comportement historique, identique à
    un appel sans le paramètre privacy_level."""
    local_provider = _FakeProvider(BASE_URL_LOCAL)
    cloud_provider = _FakeProvider(BASE_URL_CLOUD)
    gateway.providers["mon-modele-local"] = local_provider
    gateway.providers["modele-cloud"] = cloud_provider
    config = _config_tier(["mon-modele-local", "modele-cloud"])

    _tier, provider = gateway.get_provider_for_tier("leger", config)

    assert set(m for m, _ in provider.providers) == {"mon-modele-local", "modele-cloud"}


def test_privacy_parametre_appel_prime_sur_env(gateway, monkeypatch):
    """Le paramètre d'appel prime sur la variable d'environnement : env à local_only
    mais appel sans le paramètre → le mode s'applique (l'environnement est lu)."""
    monkeypatch.setenv(MOTEUR_PRIVACY_LEVEL_ENV, MOTEUR_PRIVACY_LOCAL_ONLY)
    cloud_provider = _FakeProvider(BASE_URL_CLOUD)
    gateway.providers["modele-cloud-unique"] = cloud_provider
    config = _config_tier(["modele-cloud-unique"])

    # Sans paramètre explicite, l'env local_only est bien pris en compte → fail-closed.
    with pytest.raises(ValueError, match="aucun modèle local"):
        gateway.get_provider_for_tier("leger", config)
    assert cloud_provider.calls == 0


def test_privacy_parametre_explicite_prime_sur_env_actif(gateway, monkeypatch):
    """Le paramètre d'appel prime sur l'env : env vide mais appel avec
    privacy_level="local_only" → le mode s'applique (fail-closed)."""
    monkeypatch.delenv(MOTEUR_PRIVACY_LEVEL_ENV, raising=False)
    cloud_provider = _FakeProvider(BASE_URL_CLOUD)
    gateway.providers["modele-cloud-unique"] = cloud_provider
    config = _config_tier(["modele-cloud-unique"])

    with pytest.raises(ValueError, match="aucun modèle local"):
        gateway.get_provider_for_tier(
            "leger", config, privacy_level=MOTEUR_PRIVACY_LOCAL_ONLY
        )
    assert cloud_provider.calls == 0


def test_privacy_local_only_avec_parametre_non_local_only_inchange(gateway):
    """Une valeur de privacy_level autre que 'local_only' (ex: vide) préserve le
    comportement historique : le cloud reste retenu."""
    local_provider = _FakeProvider(BASE_URL_LOCAL)
    cloud_provider = _FakeProvider(BASE_URL_CLOUD)
    gateway.providers["mon-modele-local"] = local_provider
    gateway.providers["modele-cloud"] = cloud_provider
    config = _config_tier(["mon-modele-local", "modele-cloud"])

    _tier, provider = gateway.get_provider_for_tier("leger", config, privacy_level="")

    assert set(m for m, _ in provider.providers) == {"mon-modele-local", "modele-cloud"}
