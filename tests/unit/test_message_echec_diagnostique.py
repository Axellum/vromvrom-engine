"""
[#T437] Un message d'échec doit dire ce que le pipeline A FAIT.

« Le pipeline s'est déclaré terminé mais la branche est identique à master :
aucun fichier n'a été écrit » envoie chercher un problème de chemins. Le 21/08 à
10h53, la vraie cause était tout autre : AUCUN agent d'exécution n'avait tourné.
La cascade de providers avait rendu la main en 5 secondes (Cerebras 402, Cohere
429, CLI non approuvé), le plan était vide, l'exécuteur jamais appelé.

Trois cycles ont été relancés avant qu'on regarde du bon côté.
"""

import pytest


def _message(pipeline_result, branch="task/57_178", base="master", repo=r"H:\clone"):
    """La logique du harnais, telle qu'elle décide du message rendu."""
    agents = pipeline_result.get("agents_used") or []
    reponse = (pipeline_result.get("response") or "").strip()
    if not agents or agents == ["planner"]:
        return (
            f"Le pipeline s'est déclaré terminé mais AUCUN agent d'exécution n'a "
            f"tourné (agents : {agents or 'aucun'}). Le plan était vide ou le "
            f"Planner n'a rien produit — regarder la cascade de providers, pas "
            f"les chemins de fichiers."
        )
    return (
        f"Le pipeline s'est déclaré terminé mais la branche {branch} est identique "
        f"à {base} : aucun fichier n'a été écrit dans {repo}, alors que "
        f"{len(agents)} agent(s) ont tourné ({', '.join(agents[:4])}) et rendu "
        f"{len(reponse)} caractères de réponse."
    )


@pytest.mark.parametrize("resultat", [
    {"agents_used": [], "response": ""},
    {"agents_used": None, "response": None},
    {"agents_used": ["planner"], "response": ""},
])
def test_aucun_agent_oriente_vers_les_providers(resultat):
    msg = _message(resultat)
    assert "AUCUN agent d'exécution" in msg
    assert "cascade de providers" in msg
    # Surtout : ne pas envoyer chercher les chemins de fichiers.
    assert "Vérifier que les chemins" not in msg


def test_des_agents_ont_tourne_oriente_vers_les_chemins():
    """Là, « aucun fichier écrit » est le bon diagnostic — et on dit combien ont tourné."""
    msg = _message({"agents_used": ["planner", "executor", "reviewer"], "response": "x" * 42})
    assert "3 agent(s) ont tourné" in msg
    assert "executor" in msg
    assert "42 caractères" in msg
    assert "AUCUN agent" not in msg
