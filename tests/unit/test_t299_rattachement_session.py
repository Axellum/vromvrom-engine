"""
tests/unit/test_t299_rattachement_session.py — Les appels LLM sont rattachés à leur session (#T299).

Mesure du 11/08 (fenêtre 24 h, base moteur_runtime.db) : 30/89 appels (34 %)
partaient orphelins — dont un `claude-sonnet-4-6` de 19 679 tokens facturé
$0,0627 que rien ne rattachait à une session. L'audit des call-sites (étape 1
du ticket) montre que les chemins de session transmettent tous la session,
SAUF deux familles qui en INVENTAIENT une fixe :
- les pipelines MCP (`run_tab5_agent` / `delegate_complex_reasoning`) : la
  valeur fixe "mcp_default_session"/"mcp_delegated_session" mélangeait toutes
  les exécutions dans une pseudo-session — le faux rattachement que le
  garde-fou n°1 interdit (même défaut que #T294) ;
- le ping de vivacité IHM (`/api/models/{id}/ping`) : "hmi_ping".

Ces tests écrivent dans une base créée par le VRAI schéma (`override_db_path`)
et n'appellent AUCUN LLM réel : la couche HTTP du provider est doublée
(`_FausseSessionHTTP`), les chemins MCP sont testés par contrat d'appel, et le
ping l'est avec un provider factice.
"""
import asyncio
import sqlite3

import pytest

from core.openai_compat_provider import OpenAICompatibleProvider, SharedHTTPPool


@pytest.fixture
def base_temporaire(tmp_path):
    """Base neuve au schéma canonique, isolée de la base de développement."""
    from core import runtime_db

    db = tmp_path / "t299.db"
    runtime_db.override_db_path(str(db))
    runtime_db.get_connection().close()  # déclenche _init_schema
    return str(db)


# ── Double de la couche HTTP (aucun réseau) ────────────────────────────────


class _FausseReponseHTTP:
    """Réponse requests factice : son `usage` alimente `record_usage`."""

    def __init__(self, usage: dict):
        self._usage = usage

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "usage": self._usage,
            "choices": [{"message": {"content": "ok"}}],
        }


class _FausseSessionHTTP:
    """Session requests factice : le POST ne quitte jamais la machine."""

    def __init__(self, usage: dict):
        self._usage = usage

    def post(self, *args, **kwargs):
        return _FausseReponseHTTP(self._usage)


def _provider_openai_compat(monkeypatch) -> OpenAICompatibleProvider:
    """Vrai provider OpenAI-compatible, HTTP doublé : aucun appel réel."""
    usage = {"prompt_tokens": 100, "completion_tokens": 50}
    monkeypatch.setattr(
        SharedHTTPPool,
        "get_session",
        classmethod(lambda cls: _FausseSessionHTTP(usage)),
    )
    return OpenAICompatibleProvider(
        provider_name="T299",
        base_url="http://provider-factice.invalid",
        api_key="cle-de-test",
        model="gemma-4-31b",
    )


def _lignes_session(db: str) -> list:
    conn = sqlite3.connect(db)
    lignes = conn.execute("SELECT session_id FROM token_usage").fetchall()
    conn.close()
    return lignes


class TestGardeFouHorsSession:
    """Garde-fou n°1 : sans session, la colonne reste NULL — on n'invente rien."""

    def test_appel_sans_session_laisse_session_id_null(self, base_temporaire, monkeypatch):
        """Un appel hors session (routeur, script, outil MCP) reste NULL."""
        provider = _provider_openai_compat(monkeypatch)

        provider.generate("system", "user")  # aucun session_id dans les kwargs

        assert _lignes_session(base_temporaire) == [(None,)]

    def test_appel_avec_session_ecrit_la_session(self, base_temporaire, monkeypatch):
        """Le même provider, session fournie → la ligne est rattachée."""
        provider = _provider_openai_compat(monkeypatch)

        provider.generate("system", "user", session_id="s_t299")

        assert _lignes_session(base_temporaire) == [("s_t299",)]

    async def test_appels_concurrents_chaque_session_reste_la_sienne(
        self, base_temporaire, monkeypatch
    ):
        """Garde-fou n°2 : deux sessions parallèles ne se contaminent pas."""
        provider = _provider_openai_compat(monkeypatch)

        async def _appeler(sid: str):
            return await asyncio.to_thread(
                provider.generate, "system", "user", session_id=sid
            )

        await asyncio.gather(_appeler("s_a"), _appeler("s_b"))

        sessions = sorted(ligne[0] for ligne in _lignes_session(base_temporaire))
        assert sessions == ["s_a", "s_b"]


class TestOutilsMCP:
    """Chaque exécution MCP a sa propre session — plus de valeur fixe."""

    def test_session_execution_mcp_unique_et_non_vide(self):
        from core.mcp_tools.orchestrator import _session_execution_mcp

        a = _session_execution_mcp()
        b = _session_execution_mcp()

        assert a.startswith("mcp_") and len(a) == 12
        assert a != b

    async def test_chaque_execution_mcp_a_sa_session(self, monkeypatch):
        """Deux requêtes MCP ne partagent plus la pseudo-session historique."""
        from core.mcp_tools import orchestrator

        sessions_vues: list[str] = []

        class _FauxFinalState:
            history = []

        async def _faux_pipeline(task: str, session_id: str, planner_tier: str | None = None):
            sessions_vues.append(session_id)
            return _FauxFinalState()

        monkeypatch.setattr(orchestrator, "_run_engine_pipeline", _faux_pipeline)

        await orchestrator.run_tab5_agent("tâche 1")
        await orchestrator.run_tab5_agent("tâche 2")
        await orchestrator.delegate_complex_reasoning("tâche 3", model_tier="moyen")

        assert len(sessions_vues) == 3
        assert all(s.startswith("mcp_") for s in sessions_vues)
        assert len(set(sessions_vues)) == 3, "chaque exécution doit avoir SA session"

    def test_setup_engine_sans_session_utilise_la_fabrique(self, monkeypatch):
        """Le défaut de setup_engine n'est plus une valeur fixe partagée."""
        from core.mcp_tools import orchestrator

        sessions_vues: list[str] = []
        monkeypatch.setattr(orchestrator, "_session_execution_mcp", lambda: "mcp_abcdef01")

        class _FauxEngine:
            def __init__(self, session_id, context_manager=None):
                sessions_vues.append(session_id)
                self.agents = {}

            def register_agent(self, agent):
                pass

        monkeypatch.setattr(orchestrator, "Engine", _FauxEngine)
        monkeypatch.setattr(orchestrator, "get_gateway", lambda: object())
        monkeypatch.setattr(orchestrator, "ToolRegistry", lambda: object())
        monkeypatch.setattr(orchestrator, "ContextManager", lambda llm_gateway=None: object())
        for nom in ("ExecutorAgent", "PlannerAgent", "AntigravityAgent"):
            monkeypatch.setattr(orchestrator, nom, lambda *a, **k: object())
        # HACommandAgent est importé LOCALEMENT dans setup_engine : on le
        # remplace dans son module d'origine.
        import agents.ha_agent as module_ha

        monkeypatch.setattr(module_ha, "HACommandAgent", lambda *a, **k: object())
        monkeypatch.setattr(orchestrator, "register_base_tools", lambda registry: None)
        monkeypatch.setattr(orchestrator, "register_extended_tools", lambda registry: None)
        monkeypatch.setattr(orchestrator, "Router", lambda *a, **k: object())

        orchestrator.setup_engine(session_id=None)

        assert sessions_vues == ["mcp_abcdef01"]


class TestPingIHM:
    """Le ping de vivacité n'appartient à aucune session : NULL, pas "hmi_ping"."""

    async def test_le_ping_ne_transmet_plus_de_session_au_provider(self, monkeypatch):
        """Combiné à TestGardeFouHorsSession, la chaîne complète aboutit à NULL."""
        import core.llm_gateway as module_gateway

        kwargs_recus: dict = {}

        class _FauxProvider:
            async def generate_async(self, **kwargs):
                kwargs_recus.update(kwargs)
                return "pong"

        class _FauxGateway:
            def get_provider(self, model_id: str):
                return _FauxProvider()

        monkeypatch.setattr(module_gateway, "LLMGateway", lambda *a, **k: _FauxGateway())

        from api.routes.context import ping_model

        resultat = await ping_model("gemma-4-31b")

        assert resultat["ok"] is True
        assert "session_id" not in kwargs_recus
