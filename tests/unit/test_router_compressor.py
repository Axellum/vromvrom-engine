"""
tests/unit/test_router_compressor.py — Test unitaire pour le RouterContextCompressor.
Valide la déduplication sémantique Jaccard et la troncature des contextes.
"""

from core.router_context_compressor import RouterContextCompressor


def test_compressor_deduplication():
    """Valide que les doublons sémantiques entre les sources sont éliminés."""
    compressor = RouterContextCompressor(max_chars=5000)

    contexts = {
        "facts": "- Le capteur sensor.dht22_salon a un offset de température de -1.5°C.",
        "episodes": "- Le capteur sensor.dht22_salon a un offset de température de -1.5°C.\n- Autre fait non redondant.",
        "rag": "sensor.dht22_salon a un offset de -1.5°C"  # Jaccard élevé avec facts
    }

    result = compressor.compress(contexts)

    # facts est le plus prioritaire et doit y être
    assert "MÉMOIRE SÉMANTIQUE" in result
    assert "sensor.dht22_salon a un offset de température de -1.5°C" in result

    # episodes a la ligne redondante rejetée mais la ligne non redondante conservée
    assert "MÉMOIRE ÉPISODIQUE" in result
    assert "Autre fait non redondant" in result
    # Vérifier que le doublon n'a pas été ajouté deux fois dans les blocs formatés
    occurrences = result.count("sensor.dht22_salon")
    assert occurrences == 1, f"Attendu 1 seule occurrence, trouvé {occurrences}"


def test_compressor_budget_and_priority():
    """Valide que les limites de budget respectent la priorité des sources."""
    # Budget très serré de 300 caractères
    compressor = RouterContextCompressor(max_chars=250)

    contexts = {
        "facts": "- Faits prioritaires 1\n- Faits prioritaires 2\n- Faits prioritaires 3",
        "rag": "- RAG technique long qui prend beaucoup de place dans le budget 1\n- RAG 2\n- RAG 3",
        "episodes": "- Épisode de session obsolète ou trop long",
        "context_loader": "- Markdown doc"
    }

    result = compressor.compress(contexts)

    # Le budget total doit être sous les 250 caractères
    assert len(result) <= 250

    # facts est le plus prioritaire et doit être présent en premier
    assert "MÉMOIRE SÉMANTIQUE" in result
    assert "Faits prioritaires 1" in result

    # Les sources de priorité inférieure comme context_loader ont été omises ou tronquées
    assert "CONVERSATION" not in result
    assert "Markdown doc" not in result
    assert "[... Contexte tronqué" in result


def test_compressor_empty_inputs():
    """Valide la robustesse face à des entrées vides ou absentes."""
    compressor = RouterContextCompressor(max_chars=1000)

    assert compressor.compress({}) == ""
    assert compressor.compress({"facts": "", "rag": "   "}) == ""
    
    result = compressor.compress({"rag": "Contexte rag"})
    assert "RAG TECHNIQUE" in result
    assert "Contexte rag" in result
