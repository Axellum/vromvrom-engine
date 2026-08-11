"""
tests/test_toolmaker_sandbox.py — Sandbox distante ToolMaker (#T207).

Aucun appel réseau réel : le client GitHub est un faux injecté via `_client`.
Vérifie le fail-closed sans PAT, le cycle blobs→tree→commit→ref (multi-
fichiers : outil + test minimal [#T190]), les deux verdicts du runner, le
timeout et le nettoyage de la branche éphémère.
"""

import pytest

from core.toolmaker_sandbox import CANDIDATE_DIR, validate_candidate_remote


class _FakeResponse:
    def __init__(self, data=None, status_code=200):
        self._data = data or {}
        self.status_code = status_code

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeGitHub:
    """Faux client httpx simulant l'API GitHub (Git Data + Actions)."""

    def __init__(self, run_conclusion="success", polls_before_run=0):
        self.calls = []
        self.blob_contents = []
        self.tree_paths = []
        self.created_branch = None
        self.deleted_refs = []
        self.run_conclusion = run_conclusion
        # Nombre de polls Actions renvoyant une liste vide avant le vrai run
        self.polls_before_run = polls_before_run

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url))
        body = kwargs.get("json") or {}

        if method == "GET" and url.endswith("/repos/o/r"):
            return _FakeResponse({"default_branch": "master"})
        if method == "GET" and "/git/ref/heads/master" in url:
            return _FakeResponse({"object": {"sha": "base-sha"}})
        if method == "GET" and "/git/commits/base-sha" in url:
            return _FakeResponse({"tree": {"sha": "tree-0"}})
        if method == "POST" and url.endswith("/git/blobs"):
            self.blob_contents.append(body.get("content"))
            return _FakeResponse({"sha": f"blob-{len(self.blob_contents)}"}, 201)
        if method == "POST" and url.endswith("/git/trees"):
            self.tree_paths = [e["path"] for e in body.get("tree", [])]
            return _FakeResponse({"sha": "tree-1"}, 201)
        if method == "POST" and url.endswith("/git/commits"):
            return _FakeResponse({"sha": "commit-1"}, 201)
        if method == "POST" and url.endswith("/git/refs"):
            self.created_branch = body.get("ref")
            return _FakeResponse({"ref": body.get("ref")}, 201)
        if method == "GET" and "/actions/runs" in url:
            if self.polls_before_run > 0:
                self.polls_before_run -= 1
                return _FakeResponse({"workflow_runs": []})
            if self.run_conclusion is None:
                return _FakeResponse({"workflow_runs": []})
            return _FakeResponse({
                "workflow_runs": [{
                    "status": "completed",
                    "conclusion": self.run_conclusion,
                    "html_url": "https://github.com/o/r/actions/runs/1",
                }]
            })
        if method == "DELETE" and "/git/refs/heads/" in url:
            self.deleted_refs.append(url)
            return _FakeResponse({}, 204)

        raise AssertionError(f"Appel inattendu : {method} {url}")


@pytest.fixture
def _env(monkeypatch):
    monkeypatch.setenv("MOTEUR_TOOLMAKER_PAT", "pat-test")
    monkeypatch.setenv("MOTEUR_TOOLMAKER_REPO", "o/r")


@pytest.mark.asyncio
async def test_fail_closed_sans_pat(monkeypatch):
    """Sans PAT dédié : rejet immédiat, aucun repli local, aucun appel réseau."""
    monkeypatch.delenv("MOTEUR_TOOLMAKER_PAT", raising=False)
    fake = _FakeGitHub()
    result = await validate_candidate_remote(
        files={"outil.py": "print('x')", "test_outil.py": "print('SANDBOX_OK')"},
        _client=fake,
    )
    assert result["passed"] is False
    assert "MOTEUR_TOOLMAKER_PAT" in result["error"]
    assert fake.calls == []


@pytest.mark.asyncio
async def test_aucun_fichier_rejete(_env):
    """files vide : rejet explicite, aucun appel réseau (fail-closed)."""
    fake = _FakeGitHub()
    result = await validate_candidate_remote(files={}, _client=fake)
    assert result["passed"] is False
    assert "Aucun fichier" in result["error"]
    assert fake.calls == []


@pytest.mark.asyncio
async def test_verdict_succes_et_nettoyage(_env):
    """Run vert : passed=True, run_url renseignée, branche éphémère supprimée."""
    fake = _FakeGitHub(run_conclusion="success", polls_before_run=1)
    result = await validate_candidate_remote(
        files={
            "outil.py": "print('SANDBOX_OK')",
            "test_outil.py": "print('SANDBOX_OK')",
        },
        poll_interval_s=0.01,
        _client=fake,
    )
    assert result["passed"] is True
    assert result["run_url"] is not None
    # [#T190] L'outil ET son test sont poussés au chemin attendu par le workflow
    assert fake.blob_contents == ["print('SANDBOX_OK')", "print('SANDBOX_OK')"]
    assert fake.tree_paths == [
        f"{CANDIDATE_DIR}/outil.py",
        f"{CANDIDATE_DIR}/test_outil.py",
    ]
    assert fake.created_branch.startswith("refs/heads/toolmaker/validate-")
    assert len(fake.deleted_refs) == 1


@pytest.mark.asyncio
async def test_verdict_echec(_env):
    """Run rouge : passed=False avec la conclusion, branche supprimée quand même."""
    fake = _FakeGitHub(run_conclusion="failure")
    result = await validate_candidate_remote(
        files={"outil.py": "import os; os.system('rm -rf /')"},
        poll_interval_s=0.01,
        _client=fake,
    )
    assert result["passed"] is False
    assert "failure" in result["error"]
    assert len(fake.deleted_refs) == 1


@pytest.mark.asyncio
async def test_timeout_verdict(_env):
    """Aucun run visible avant le délai : rejet explicite, branche supprimée."""
    fake = _FakeGitHub(run_conclusion=None)
    result = await validate_candidate_remote(
        files={"outil.py": "print('x')"},
        timeout_s=0.05,
        poll_interval_s=0.01,
        _client=fake,
    )
    assert result["passed"] is False
    assert "Timeout" in result["error"]
    assert len(fake.deleted_refs) == 1


@pytest.mark.asyncio
async def test_erreur_api_fail_closed(_env):
    """Une erreur API GitHub (ex: 401) = rejet, jamais d'exécution locale."""

    class _Broken(_FakeGitHub):
        async def request(self, method, url, **kwargs):
            return _FakeResponse({}, status_code=401)

    result = await validate_candidate_remote(files={"outil.py": "print('x')"}, _client=_Broken())
    assert result["passed"] is False
    assert "Erreur API GitHub" in result["error"]
