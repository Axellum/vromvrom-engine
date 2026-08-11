import subprocess
from pathlib import Path

import pytest
from unittest.mock import patch, MagicMock
from agents.reviewer import ReviewerAgent, GitDetectionError
from core.state import TaskPayload
from core.llm_gateway import LLMGateway


def _init_repo(path: Path, commits: int = 1, with_remote: bool = False) -> None:
    """Construit un vrai dépôt Git temporaire pour tester l'heuristique #T209."""
    def _git(*args):
        subprocess.run(
            ["git", "-c", "user.email=test@test.local", "-c", "user.name=test", *args],
            cwd=path, check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    _git("init")
    for i in range(commits):
        (path / f"fichier_{i}.txt").write_text("contenu", encoding="utf-8")
        _git("add", "-A")
        _git("commit", "-m", f"commit {i}")
    if with_remote:
        _git("remote", "add", "origin", "https://example.invalid/repo.git")


def _make_agent() -> ReviewerAgent:
    return ReviewerAgent(llm_gateway=MagicMock(spec=LLMGateway), provider_name="leger")


def test_repo_parasite_sans_remote_commit_unique_non_informatif(tmp_path):
    """Cas réel du Deck (#T209) : overlay cp -rf, aucun remote, un seul commit."""
    _init_repo(tmp_path, commits=1, with_remote=False)
    assert _make_agent()._is_git_repo_informative(str(tmp_path)) is False


def test_repo_avec_remote_informatif(tmp_path):
    _init_repo(tmp_path, commits=1, with_remote=True)
    assert _make_agent()._is_git_repo_informative(str(tmp_path)) is True


def test_repo_local_avec_historique_informatif(tmp_path):
    _init_repo(tmp_path, commits=2, with_remote=False)
    assert _make_agent()._is_git_repo_informative(str(tmp_path)) is True


def test_repo_sans_aucun_commit_non_informatif(tmp_path):
    _init_repo(tmp_path, commits=0, with_remote=False)
    assert _make_agent()._is_git_repo_informative(str(tmp_path)) is False


def test_has_modified_files_leve_sur_depot_non_informatif():
    """Un dépôt non informatif dans le workspace doit forcer la revue LLM."""
    agent = _make_agent()
    with patch.object(agent, "_is_git_repo_informative", return_value=False):
        # [#T276] message agrégé : on n'élève plus « non informatif » par dépôt,
        # seulement quand AUCUN dépôt exploitable ne reste.
        with pytest.raises(GitDetectionError, match="Aucun dépôt Git informatif"):
            agent._has_modified_files()

@pytest.mark.asyncio
async def test_reviewer_git_detection_error_forces_llm():
    # Arrange
    gateway = MagicMock(spec=LLMGateway)
    mock_provider = MagicMock()
    # Mock de generate_structured_async pour renvoyer une approbation
    async def mock_gen(*args, **kwargs):
        return {
            "code_approved": True,
            "severity": "info",
            "review_feedback": "Validé par le mock LLM",
            "target_corrections": [],
            "quality_score": 9.5
        }
    mock_provider.generate_structured_async = mock_gen
    gateway.get_provider_for_tier.return_value = ("mock-model", mock_provider)
    
    agent = ReviewerAgent(llm_gateway=gateway, provider_name="leger")
    payload = TaskPayload(
        task_objective="Test",
        relevant_context="code",
        metadata={"session_id": "test_session", "model_tier": "leger"}
    )
    
    # Simuler une détection Git impossible (ex: pas de dépôt)
    with patch.object(agent, "_has_modified_files", side_effect=GitDetectionError("Dépôt introuvable")):
        with patch('core.llm_gateway.load_config', return_value={}):
            update = await agent.invoke(payload)
            
    # Assert
    assert update.status == "success"
    assert "Code validé et approuvé par le Reviewer" in update.result_data
    # S'assurer que le LLM a bien été appelé (pas d'auto-approbation à 10.0 de court-circuit)
    assert update.metadata.get("quality_score") == 9.5

@pytest.mark.asyncio
async def test_reviewer_git_no_changes_auto_approves():
    gateway = MagicMock(spec=LLMGateway)
    agent = ReviewerAgent(llm_gateway=gateway, provider_name="leger")
    payload = TaskPayload(
        task_objective="Test",
        relevant_context="code",
        metadata={"session_id": "test_session", "model_tier": "leger"}
    )
    
    # Simuler qu'aucun fichier n'est modifié
    with patch.object(agent, "_has_modified_files", return_value=False):
        with patch('core.llm_gateway.load_config', return_value={}):
            update = await agent.invoke(payload)
            
    # Assert
    assert update.status == "success"
    assert "Auto-approuvé" in update.result_data
    assert update.metadata.get("quality_score") == 10.0
