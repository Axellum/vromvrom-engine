# -*- coding: utf-8 -*-
"""[#T12] Garde-fou de l'espace d'embedding du ML Router.

Le provider d'embedding est re-détecté à l'inférence et peut différer de celui
de l'entraînement. Deux modèles distincts partagent parfois la même dimension
(nomic-embed-text et text-embedding-004 = 768d) : sans contrôle, le classifieur
reçoit des vecteurs d'un espace étranger → prédictions silencieusement fausses.
`_embedding_space_ok` doit rejeter ces cas (predict → fallback LLM).
"""

import types

from core.ml_router import MLRouter


def _router(emb_model=None, emb_dim=None, detected_model=None, n_features=None):
    r = MLRouter()
    r._embedding_model = emb_model
    r._embedding_dim = emb_dim
    r._detected_model = detected_model
    r._model = types.SimpleNamespace(n_features_in_=n_features) if n_features else None
    return r


def test_accepte_espace_identique():
    r = _router("nomic-embed-text", 768, "nomic-embed-text")
    assert r._embedding_space_ok(768) is True


def test_rejette_meme_dimension_modele_different():
    # Cœur du fix : 768d des deux côtés mais espaces distincts → doit être rejeté.
    r = _router("nomic-embed-text", 768, "text-embedding-004")
    assert r._embedding_space_ok(768) is False


def test_rejette_dimension_differente():
    r = _router("nomic-embed-text", 768, "nomic-embed-text")
    assert r._embedding_space_ok(3072) is False


def test_legacy_sans_identite_accepte_meme_dimension():
    # Pickle legacy (pas d'identité) → garde-fou réduit à la dimension via n_features_in_.
    r = _router(emb_model=None, emb_dim=None, detected_model="text-embedding-004", n_features=768)
    assert r._embedding_space_ok(768) is True


def test_legacy_rejette_dimension_differente_via_n_features():
    r = _router(emb_model=None, emb_dim=None, detected_model="nomic-embed-text", n_features=768)
    assert r._embedding_space_ok(3072) is False


def test_round_trip_identite_persistee(tmp_path, monkeypatch):
    import core.ml_router as M
    monkeypatch.setattr(M, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(M, "MODEL_PATH", tmp_path / "ml_router.pkl")
    monkeypatch.setattr(M, "META_PATH", tmp_path / "ml_router_meta.json")
    monkeypatch.setattr(M, "_get_models_dir", lambda: tmp_path)

    r = MLRouter()
    r._model = types.SimpleNamespace(n_features_in_=768)
    r._use_embeddings = True
    r._classes = ["home_assistant", "casual_chat"]
    r._embedding_provider = "ollama"
    r._embedding_model = "nomic-embed-text"
    r._embedding_dim = 768
    r._save_model()

    r2 = MLRouter()
    r2._load_model()
    assert r2._embedding_provider == "ollama"
    assert r2._embedding_model == "nomic-embed-text"
    assert r2._embedding_dim == 768
