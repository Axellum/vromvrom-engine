"""
tests/unit/test_executor_exigence_outil.py — « Aucun outil appelé » (#T274).

Le contrôle de l'ExecutorAgent qui refuse une tâche terminée sans appel d'outil
existe pour une bonne raison : une MUTATION sans outil, c'est un agent qui
prétend avoir travaillé. Mais il listait aussi des SUBSTANTIFS (« fichier »,
« file », « dossier », « git »), si bien que toute tâche mentionnant un fichier
devait appeler un outil — y compris une simple vérification.

Mesuré en production le 11/08 sur `bg_4eace58a` : `write_preuve` **success**,
`read_preuve` **success**, `verify_preuve` **error** — « L'agent exécuteur n'a
appelé aucun outil » — alors que le fichier était écrit avec le contenu exact
attendu et que la donnée était dans le contexte de la tâche. Comme
`_finalize_git` exige que TOUTES les tâches soient en succès, le travail réussi
a fini en `Rollback effectué (branche éphémère détruite)`.

Pire : le prompt système demande explicitement « Utilise DIRECTEMENT ces
contenus du contexte […] Ne fais aucun appel à 'read_file' dans ce cas ». Le
contrôle punissait le comportement que l'agent avait reçu l'ordre d'adopter.
"""

import pytest

from agents.executor import ExecutorAgent
from core.state import TaskPayload

exige = ExecutorAgent.exige_un_appel_d_outil

CONTEXTE = "Résultat 'write_file' : fichier écrit, contenu = OK-T273"


# ── Le cas de production ─────────────────────────────────────────────────────

def test_verification_avec_contexte_ne_reclame_plus_d_outil():
    """Le cas exact de `verify_preuve` (11/08) : la donnée est déjà là."""
    assert exige("Vérifier que le fichier /tmp/preuve.txt contient OK-T273", CONTEXTE) is False


def test_verification_sans_contexte_reclame_un_outil():
    """Sans donnée en contexte, répondre sans lire reviendrait à inventer."""
    assert exige("Vérifier que le fichier /tmp/preuve.txt contient OK-T273", "") is True


def test_verbe_de_tete_prioritaire():
    """
    « Vérifier que le fichier a bien été créé » est une vérification.

    Sans cette règle, le « créé » de la subordonnée la classerait en mutation et
    on reproduirait le bug qu'on corrige.
    """
    assert exige("Vérifier que le fichier a bien été créé", CONTEXTE) is False


# ── Ce que le contrôle doit continuer d'attraper ─────────────────────────────

@pytest.mark.parametrize("objectif", [
    "Créer le fichier /tmp/a.txt contenant A",
    "Écris la configuration dans config.yaml",
    "Supprime les fichiers temporaires",
    "Exécute le script de déploiement",
    "Installe la dépendance manquante",
])
def test_mutation_exige_toujours_un_outil(objectif):
    """Une mutation sans appel d'outil reste une faute, contexte ou pas."""
    assert exige(objectif, CONTEXTE) is True


def test_mutation_puis_verification_reste_une_mutation():
    assert exige("Créer le fichier puis vérifier son contenu", CONTEXTE) is True


# ── Robustesse ───────────────────────────────────────────────────────────────

def test_accents_indifferents():
    """Les plans générés et le vocal écrivent tantôt « vérifier », tantôt « verifier »."""
    assert exige("Verifier le contenu du fichier", CONTEXTE) is exige(
        "Vérifier le contenu du fichier", CONTEXTE
    )


def test_objectif_neutre_n_exige_rien():
    assert exige("Raconte une blague sur les volets roulants", "") is False


def test_contexte_none_traite_comme_vide():
    assert exige("Vérifier le contenu du fichier", None) is True


def test_contexte_symbolique_ne_compte_pas():
    """Un « None » ou un mot isolé n'est pas une donnée exploitable."""
    assert exige("Vérifier le contenu du fichier", "None") is True


# ── Comportement de bout en bout de l'agent ──────────────────────────────────

class _FauxProvider:
    pass


def _agent_avec_boucle_sans_outil(monkeypatch, texte_final: str):
    """Agent réel dont la boucle ReAct rend « aucun outil exécuté »."""
    from core.llm_gateway import LLMGateway
    from tools.tool_registry import ToolRegistry

    agent = ExecutorAgent(llm_gateway=LLMGateway(), tool_registry=ToolRegistry(), provider_name="leger")
    monkeypatch.setattr(
        agent.gateway, "get_provider_for_tier", lambda tier, config: ("faux-modele", _FauxProvider())
    )

    async def _boucle(**kwargs):
        # (last_results, final_text_response, tool_executed, last_tool_error)
        return [], texte_final, False, None

    monkeypatch.setattr(agent, "_execute_react_loop", _boucle)
    return agent


@pytest.mark.asyncio
async def test_verification_sans_outil_reussit_avec_contexte(monkeypatch):
    """Bout en bout : la tâche de vérification n'échoue plus à tort."""
    agent = _agent_avec_boucle_sans_outil(monkeypatch, "Le fichier contient bien OK-T273.")
    update = await agent.invoke(TaskPayload(
        task_objective="Vérifier que le fichier /tmp/preuve.txt contient OK-T273",
        relevant_context=CONTEXTE,
    ))
    assert update.status == "success"


@pytest.mark.asyncio
async def test_mutation_sans_outil_echoue_toujours(monkeypatch):
    """Contre-épreuve : le garde-fou n'a pas été désarmé."""
    agent = _agent_avec_boucle_sans_outil(monkeypatch, "J'ai créé le fichier.")
    update = await agent.invoke(TaskPayload(
        task_objective="Créer le fichier /tmp/preuve.txt contenant OK-T273",
        relevant_context=CONTEXTE,
    ))
    assert update.status == "error"
    assert "aucun outil" in (update.error_message or "").lower()
