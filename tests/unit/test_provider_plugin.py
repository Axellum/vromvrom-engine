"""
tests/unit/test_provider_plugin.py — Contrat de plugin Provider et enregistrement
(#T231, étape 1).

Scénarios couverts :
  - le descripteur est immuable et se projette sur les colonnes réelles des tables ;
  - `type` reste le vocabulaire du catalogue (piège de `models_db.py:1121`) ;
  - sans clé : `build()` lève et RIEN n'est écrit en base (l'invariant du chantier) ;
  - avec clé : transport conforme, une instance par modèle ;
  - modèle inconnu refusé ;
  - `dry_run` construit sans écrire ;
  - les modèles déclarés correspondent à ceux que le gateway câble réellement.

Aucune écriture dans `models_registry.db` : les upserts sont injectés. Un test qui
écrit dans le catalogue partagé y laisse des lignes fantômes (incident constaté le
03/07 avec `test_sqlite_concurrency.py`).
"""

import dataclasses

import pytest

from core.llm.builtin_providers import MistralPlugin
from core.llm.provider_plugin import (
    CredentialsManquantes,
    TransportInvalide,
    verifier_interface_transport,
)
from core.llm.provider_registration import register_provider_plugins

CLE_FACTICE = {"MISTRAL_API_KEY": "cle-de-test-non-fonctionnelle"}


class _Upserts:
    """Espion d'écriture : mémorise les appels au lieu de toucher la base."""

    def __init__(self, reussite: bool = True):
        self.reussite = reussite
        self.providers: list[tuple[str, dict]] = []
        self.modeles: list[tuple[str, dict]] = []

    def provider(self, provider_id: str, **kwargs) -> bool:
        self.providers.append((provider_id, kwargs))
        return self.reussite

    def model(self, model_id: str, **kwargs) -> bool:
        self.modeles.append((model_id, kwargs))
        return self.reussite


@pytest.fixture()
def espion() -> _Upserts:
    return _Upserts()


def _enregistrer(espion: _Upserts, credentials: dict, **kwargs):
    # Étape 2 : BUILTIN_PROVIDER_PLUGINS porte désormais 17 plugins ; ces tests
    # restent focalisés sur le pilote de l'étape 1 (les 16 autres ont leur propre
    # couverture dans test_builtin_providers_etape2.py).
    return register_provider_plugins(
        (MistralPlugin(),),
        credentials,
        upsert_provider=espion.provider,
        upsert_model=espion.model,
        **kwargs,
    )


# ── Descripteur ───────────────────────────────────────────────────────────────


def test_descripteur_immuable():
    """Un descripteur est une déclaration, jamais un état mutable."""
    descripteur = MistralPlugin().descriptor
    with pytest.raises(dataclasses.FrozenInstanceError):
        descripteur.cascade_priority = 1.0  # type: ignore[misc]


def test_type_reste_le_vocabulaire_du_catalogue():
    """`providers.type` porte le canal commercial, PAS le type de transport.

    `core/models_db.py:1121` filtre sur `type IN ('pay_as_you_go', 'free')` : écrire
    "openai_compat" (comme le proposait l'esquisse de conception) ferait disparaître
    le provider de cette requête. Le transport a son propre champ, non persisté.
    """
    descripteur = MistralPlugin().descriptor
    assert descripteur.type in ("pay_as_you_go", "free")
    assert descripteur.transport == "openai_compat"
    assert "transport" not in descripteur.en_colonnes()


def test_projection_sur_les_colonnes_des_tables():
    """Les projections ne produisent que des noms de colonnes existants."""
    colonnes_providers = {
        "name", "type", "api_endpoint", "auth_method",
        "confidentiality", "cascade_priority", "notes",
    }
    colonnes_models = {
        # [#T254] `status` volontairement absent : le projeter réactiverait à
        # chaque démarrage tout modèle désactivé depuis l'IHM.
        "provider_id", "display_name", "tier", "routing_tier",
        "context_input", "context_output", "cost_input_per_m", "cost_output_per_m",
        "supports_tools", "supports_vision", "supports_json_mode",
        "supports_streaming", "speciality", "recommended_use", "notes",
    }
    descripteur = MistralPlugin().descriptor
    assert set(descripteur.en_colonnes()) == colonnes_providers
    assert descripteur.en_colonnes()["auth_method"] == "api_key"
    for spec in descripteur.models:
        assert set(spec.en_colonnes("mistral")) == colonnes_models


def test_modeles_declares_alignes_sur_le_gateway():
    """Les modèles déclarés sont exactement ceux que le gateway câble aujourd'hui.

    Garde-fou anti-divergence : si `llm_gateway.py` gagne ou perd un modèle Mistral
    sans que le plugin suive, ce test tombe — c'est tout l'objet de #T231.
    """
    declares = {spec.id for spec in MistralPlugin().descriptor.models}
    assert declares == {"mistral-large-latest", "codestral-latest", "open-mistral-nemo"}


# ── build() ───────────────────────────────────────────────────────────────────


def test_build_sans_cle_leve():
    with pytest.raises(CredentialsManquantes, match="MISTRAL_API_KEY"):
        MistralPlugin().build({})


def test_build_cle_vide_ou_espaces_leve():
    """Une clé présente mais vide est un provider non configuré, pas configuré."""
    with pytest.raises(CredentialsManquantes):
        MistralPlugin().build({"MISTRAL_API_KEY": "   "})


def test_build_avec_cle_rend_un_transport_conforme():
    transport = MistralPlugin().build(CLE_FACTICE)
    verifier_interface_transport(transport)  # ne doit pas lever
    assert transport.model == "mistral-large-latest"
    assert transport.base_url == "https://api.mistral.ai/v1/chat/completions"
    assert transport.api_key == CLE_FACTICE["MISTRAL_API_KEY"]


def test_build_pour_un_modele_precis():
    transport = MistralPlugin().build(CLE_FACTICE, model="codestral-latest")
    assert transport.model == "codestral-latest"


def test_alias_de_catalogue_resolu_vers_le_nom_api():
    """Un `id` de catalogue peut être un alias ; c'est `api_model_id` qui part à l'API.

    Vérifié en direct le 10/08 : appeler Cerebras avec `gemma-4-31b-cerebras` (l'alias
    du catalogue, choisi parce que `gemma-4-31b` désignait déjà le modèle LM Studio
    local) renvoie HTTP 404 ; le vrai nom est `gemma-4-31b`.
    """
    from core.llm.provider_plugin import ModelSpec

    alias = ModelSpec(id="gemma-4-31b-cerebras", api_model_id="gemma-4-31b")
    direct = ModelSpec(id="mistral-large-latest")
    assert alias.nom_api == "gemma-4-31b"
    assert direct.nom_api == "mistral-large-latest"
    # L'alias reste l'identifiant en base : c'est lui qui doit être unique.
    assert alias.en_colonnes("cerebras")["display_name"] == "gemma-4-31b-cerebras"


def test_build_modele_inconnu_refuse():
    with pytest.raises(CredentialsManquantes, match="non déclaré"):
        MistralPlugin().build(CLE_FACTICE, model="mistral-inexistant")


def test_transport_incomplet_rejete():
    class _Partiel:
        def generate(self, *a, **k):  # pragma: no cover - jamais appelé
            return ""

    with pytest.raises(TransportInvalide, match="generate_structured"):
        verifier_interface_transport(_Partiel())


# ── Enregistrement ────────────────────────────────────────────────────────────


def test_sans_cle_rien_n_est_ecrit(espion):
    """L'invariant du chantier : pas de transport → pas de ligne au catalogue."""
    rapport = _enregistrer(espion, {})

    assert espion.providers == []
    assert espion.modeles == []
    assert rapport.providers_enregistres == []
    assert "mistral" in rapport.ecartes
    assert "MISTRAL_API_KEY" in rapport.ecartes["mistral"]
    assert not rapport.ok


def test_avec_cle_provider_et_modeles_ecrits(espion):
    rapport = _enregistrer(espion, CLE_FACTICE)

    assert rapport.providers_enregistres == ["mistral"]
    assert sorted(rapport.modeles_enregistres) == [
        "codestral-latest", "mistral-large-latest", "open-mistral-nemo",
    ]
    assert rapport.ecartes == {}
    assert rapport.ok

    (pid, colonnes), = espion.providers
    assert pid == "mistral"
    assert colonnes["type"] == "pay_as_you_go"
    assert colonnes["cascade_priority"] == 3.8
    assert colonnes["confidentiality"] == "training"

    assert {mid for mid, _ in espion.modeles} == set(rapport.modeles_enregistres)
    for _, colonnes_modele in espion.modeles:
        assert colonnes_modele["provider_id"] == "mistral"
        # [#T254] Le statut n'est plus imposé : à la création le défaut SQL vaut
        # 'active', et une désactivation manuelle survit aux redémarrages.
        assert "status" not in colonnes_modele


def test_un_transport_par_modele(espion):
    """Le gateway range une instance PAR modèle : l'enregistrement doit les fournir."""
    rapport = _enregistrer(espion, CLE_FACTICE)

    assert set(rapport.transports) == {
        "mistral:mistral-large-latest",
        "mistral:codestral-latest",
        "mistral:open-mistral-nemo",
    }
    assert rapport.transports["mistral:codestral-latest"].model == "codestral-latest"


def test_dry_run_construit_sans_ecrire(espion):
    rapport = _enregistrer(espion, CLE_FACTICE, dry_run=True)

    assert espion.providers == []
    assert espion.modeles == []
    assert len(rapport.transports) == 3
    assert rapport.providers_enregistres == []


def test_echec_d_ecriture_du_provider_n_ecrit_aucun_modele():
    """Si la ligne provider ne passe pas, on n'écrit pas de modèles orphelins
    (`models.provider_id` est une clé étrangère vers `providers.id`)."""
    espion = _Upserts(reussite=False)
    rapport = _enregistrer(espion, CLE_FACTICE)

    assert len(espion.providers) == 1
    assert espion.modeles == []
    assert "mistral" in rapport.ecartes
