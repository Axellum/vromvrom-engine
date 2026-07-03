# -*- coding: utf-8 -*-
"""Tests du pipeline d'entraînement du ML Router (jointure texte↔label par hash)."""

from core.ml_router import build_training_pairs, _prompt_hash, ROUTING_CLASSES


def _session(objective, status="success"):
    return {"objective": objective, "status": status}


def test_pairs_joined_by_hash_and_rare_classes_dropped():
    # 3 home_assistant + 2 casual_chat + 1 code_generation (rare → retiré).
    ha = ["allume la lumière du salon", "ferme les volets", "quelle température"]
    cc = ["bonjour ça va", "merci beaucoup"]
    cg = ["écris une fonction python"]
    sessions = [_session(o) for o in ha + cc + cg]

    hash_to_cat = {}
    for o in ha:
        hash_to_cat[_prompt_hash(o)] = "home_assistant"
    for o in cc:
        hash_to_cat[_prompt_hash(o)] = "casual_chat"
    for o in cg:
        hash_to_cat[_prompt_hash(o)] = "code_generation"

    texts, labels = build_training_pairs(sessions, hash_to_cat, min_per_class=2)

    assert labels.count("home_assistant") == 3
    assert labels.count("casual_chat") == 2
    assert "code_generation" not in labels  # 1 seul échantillon → retiré
    assert len(texts) == len(labels) == 5


def test_excludes_non_success_short_and_unknown():
    sessions = [
        _session("allume la lumière du salon"),                 # ok
        _session("ferme les volets de la chambre"),             # ok
        _session("éteins tout", status="error"),                # exclu : pas success
        _session("ok"),                                         # exclu : objectif trop court
        _session("une requête sans label connu dans la map"),  # exclu : hash absent
        _session("prompt mappé sur une catégorie inconnue xyz"),# exclu : catégorie hors ROUTING_CLASSES
    ]
    hash_to_cat = {
        _prompt_hash("allume la lumière du salon"): "home_assistant",
        _prompt_hash("ferme les volets de la chambre"): "home_assistant",
        _prompt_hash("éteins tout"): "home_assistant",        # mais session error
        _prompt_hash("prompt mappé sur une catégorie inconnue xyz"): "inconnue_xyz",
    }
    texts, labels = build_training_pairs(sessions, hash_to_cat, min_per_class=1)
    assert labels == ["home_assistant", "home_assistant"]
    assert all(c in ROUTING_CLASSES for c in labels)


def test_prompt_hash_matches_routing_metrics_formula():
    import hashlib
    p = "allume la lumière"
    assert _prompt_hash(p) == hashlib.sha256(p.encode("utf-8")).hexdigest()[:16]
