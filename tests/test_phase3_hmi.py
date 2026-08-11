"""
Tests Phase 3 HMI — contrats backend réels pour l'IHM v2.

Couvre :
- #T188 : core/prompt_loader.py (chargement Markdown, repli, sauvegarde, cache mtime)
- #T194 : apply_workload_override + champs tier/model d'ExecuteRequestBody
- #T159 : CRUD /api/agents (config.json isolé dans un fichier temporaire)
- #T158 : GET /api/models/registry (enrichissement + ordre des routes),
          toggle et routing-tier (UPDATE ciblés en base)
- #T192 : POST /api/prompt/engineer (validation, sans appel LLM réel)
"""

import json
import os
import sys

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from gui_server import app
    auth = {"Authorization": f"Bearer {os.environ.get('MOTEUR_API_KEY', '')}"}
    with TestClient(app, headers=auth) as c:
        yield c


# ──────────────────────────────────────────────────────────────────
# #T188 — prompt_loader
# ──────────────────────────────────────────────────────────────────

class TestPromptLoader:
    def test_fallback_si_fichier_absent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MOTEUR_PROMPTS_DIR", str(tmp_path))
        from core.prompt_loader import load_agent_prompt
        assert load_agent_prompt("agent_inconnu", "DEFAUT") == "DEFAUT"

    def test_sauvegarde_puis_chargement(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MOTEUR_PROMPTS_DIR", str(tmp_path))
        from core.prompt_loader import has_external_prompt, load_agent_prompt, save_agent_prompt

        path = save_agent_prompt("mon_agent", "Contenu du prompt de test.")
        assert os.path.exists(path)
        assert has_external_prompt("mon_agent")
        assert load_agent_prompt("mon_agent", "DEFAUT") == "Contenu du prompt de test."

    def test_fichier_vide_retombe_sur_defaut(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MOTEUR_PROMPTS_DIR", str(tmp_path))
        from core.prompt_loader import load_agent_prompt
        (tmp_path / "vide.md").write_text("", encoding="utf-8")
        assert load_agent_prompt("vide", "DEFAUT") == "DEFAUT"

    def test_nom_agent_sans_traversee_de_chemin(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MOTEUR_PROMPTS_DIR", str(tmp_path))
        from core.prompt_loader import prompt_file_path
        # Les caractères de traversée sont retirés : le fichier reste dans le dossier.
        assert os.path.dirname(prompt_file_path("../evil")) == str(tmp_path)


# ──────────────────────────────────────────────────────────────────
# #T194 — override de force de workload
# ──────────────────────────────────────────────────────────────────

class TestWorkloadOverride:
    def test_tier_valide_surcharge_executor_et_ha(self):
        from services.pipeline_service import apply_workload_override
        config = {"executor_model": "gemini-3.5-flash-free", "ha_model": "moyen", "planner_model": "fort"}
        out = apply_workload_override(config, tier="fort")
        assert out["executor_model"] == "fort"
        assert out["ha_model"] == "fort"
        # Le planner n'est pas touché, la config d'origine non plus (copie).
        assert out["planner_model"] == "fort"
        assert config["executor_model"] == "gemini-3.5-flash-free"

    def test_model_prioritaire_sur_tier(self):
        from services.pipeline_service import apply_workload_override
        out = apply_workload_override({"executor_model": "a", "ha_model": "b"}, tier="leger", model="deepseek-chat")
        assert out["executor_model"] == "deepseek-chat"
        assert out["ha_model"] == "deepseek-chat"

    def test_tier_invalide_ou_absent_ne_change_rien(self):
        from services.pipeline_service import apply_workload_override
        config = {"executor_model": "a", "ha_model": "b"}
        assert apply_workload_override(config, tier="turbo") == config
        assert apply_workload_override(config) == config

    def test_body_execute_accepte_tier_et_model(self):
        from api.routes.agents import ExecuteRequestBody
        body = ExecuteRequestBody(user_prompt="test", tier="fort", model="deepseek-chat")
        assert body.tier == "fort"
        assert body.model == "deepseek-chat"
        # Rétrocompatibilité : champs optionnels.
        assert ExecuteRequestBody(user_prompt="test").tier is None


# ──────────────────────────────────────────────────────────────────
# #T159 — CRUD /api/agents (config.json isolé)
# ──────────────────────────────────────────────────────────────────

@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Copie minimale de config.json dans un fichier temporaire, injectée dans le CRUD."""
    import api.routes.agents_crud as crud

    config = {
        "planner_model": "gemini-3.5-flash-free",
        "executor_model": "gemini-3.5-flash-free",
        "antigravity_model": "fort",
        "ha_model": "moyen",
        "persistent_agents": {
            "daemon_model": "leger",
            "daemon_enabled": False,
            "daemon_interval_minutes": 30,
            "dreamer_model": "moyen",
            "dreamer_enabled": True,
        },
    }
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(config, indent=2), encoding="utf-8")
    monkeypatch.setattr(crud, "CONFIG_FILE", str(cfg_file))
    return cfg_file


class TestAgentsCrud:
    def test_liste_contient_coeur_et_persistants(self, client, isolated_config):
        res = client.get("/api/agents")
        assert res.status_code == 200
        agents = {a["name"]: a for a in res.json()["agents"]}
        for core_name in ("planner", "executor", "reviewer", "ha_agent", "prompt_engineer", "tool_maker"):
            assert core_name in agents, f"{core_name} absent de la liste"
            assert agents[core_name]["kind"] == "core"
            assert agents[core_name]["can_disable"] is False
        assert agents["daemon"]["kind"] == "persistent"
        assert agents["daemon"]["interval_minutes"] == 30
        assert agents["dreamer"]["enabled"] is True
        # Le prompt du reviewer vient du Markdown #T188 (ou du défaut code) : non vide.
        assert agents["reviewer"]["prompt_editable"] is True

    def test_put_model_agent_coeur_ecrit_config(self, client, isolated_config):
        res = client.put("/api/agents/planner", json={"model": "fort"})
        assert res.status_code == 200
        assert res.json()["model"] == "fort"
        saved = json.loads(isolated_config.read_text(encoding="utf-8"))
        assert saved["planner_model"] == "fort"

    def test_toggle_agent_coeur_refuse(self, client, isolated_config):
        res = client.post("/api/agents/executor/toggle")
        assert res.status_code == 422

    def test_toggle_persistant_bascule_config(self, client, isolated_config):
        res = client.post("/api/agents/daemon/toggle")
        assert res.status_code == 200
        assert res.json()["enabled"] is True
        saved = json.loads(isolated_config.read_text(encoding="utf-8"))
        assert saved["persistent_agents"]["daemon_enabled"] is True

    def test_cycle_de_vie_agent_custom(self, client, isolated_config):
        # Création
        res = client.post("/api/agents", json={
            "name": "agent_test_phase3", "label": "Agent de test", "tier": "leger",
            "system_prompt": "Tu es un agent de test.",
        })
        assert res.status_code == 200
        assert res.json()["kind"] == "custom"

        # Doublon refusé
        assert client.post("/api/agents", json={"name": "agent_test_phase3", "tier": "leger"}).status_code == 409

        # Présent dans la liste + réellement chargeable par le moteur
        agents = {a["name"] for a in client.get("/api/agents").json()["agents"]}
        assert "agent_test_phase3" in agents
        from core.custom_agents import load_custom_agent_entries
        saved = json.loads(isolated_config.read_text(encoding="utf-8"))
        assert any(e["name"] == "agent_test_phase3" for e in load_custom_agent_entries(saved, only_enabled=True))

        # Patch + toggle
        assert client.put("/api/agents/agent_test_phase3", json={"tier": "fort"}).json()["model"] == "fort"
        assert client.post("/api/agents/agent_test_phase3/toggle").json()["enabled"] is False

        # Suppression
        assert client.delete("/api/agents/agent_test_phase3").status_code == 200
        agents = {a["name"] for a in client.get("/api/agents").json()["agents"]}
        assert "agent_test_phase3" not in agents

    def test_creation_nom_reserve_ou_invalide(self, client, isolated_config):
        assert client.post("/api/agents", json={"name": "planner", "tier": "leger"}).status_code == 409
        assert client.post("/api/agents", json={"name": "Pas Valide!", "tier": "leger"}).status_code == 422

    def test_put_prompt_agent_coeur_ecrit_markdown(self, client, isolated_config, tmp_path, monkeypatch):
        monkeypatch.setenv("MOTEUR_PROMPTS_DIR", str(tmp_path / "prompts"))
        res = client.put("/api/agents/reviewer", json={"system_prompt": "Prompt reviewer de test."})
        assert res.status_code == 200
        md = tmp_path / "prompts" / "reviewer.md"
        assert md.exists()
        assert "Prompt reviewer de test." in md.read_text(encoding="utf-8")

    def test_put_prompt_agent_non_editable_refuse(self, client, isolated_config):
        res = client.put("/api/agents/executor", json={"system_prompt": "x"})
        assert res.status_code == 422


# ──────────────────────────────────────────────────────────────────
# #T158 — registre modèles enrichi + toggle/routing-tier
# ──────────────────────────────────────────────────────────────────

TEST_MODEL_ID = "test-phase3-model"


@pytest.fixture
def temp_model():
    """Insère un modèle jetable dans models_registry.db, le supprime après."""
    from core.models_db import _get_connection, _write_transaction, upsert_model

    conn = _get_connection()
    provider_row = conn.execute("SELECT id FROM providers LIMIT 1").fetchone()
    if provider_row is None:
        pytest.skip("models_registry.db sans provider (seed absent)")
    provider_id = provider_row[0]

    assert upsert_model(TEST_MODEL_ID, provider_id=provider_id, display_name="Modèle test Phase 3",
                        status="active", tier="free")
    yield TEST_MODEL_ID
    with _write_transaction() as conn:
        conn.execute("DELETE FROM models WHERE id = ?", (TEST_MODEL_ID,))


class TestModelsRegistry:
    def test_registry_route_avant_route_dynamique(self, client):
        # Si l'ordre des routes était mauvais, "registry" serait pris pour un
        # model_id par /api/models/{model_id} → 404.
        res = client.get("/api/models/registry")
        assert res.status_code == 200
        body = res.json()
        assert "models" in body and isinstance(body["models"], list)

    def test_champs_enrichis_presents(self, client):
        models = client.get("/api/models/registry").json()["models"]
        if not models:
            pytest.skip("Catalogue vide (seed absent)")
        sample = models[0]
        for field in ("elo_score", "avg_latency_ms", "cost_per_success",
                      "circuit_breaker_status", "is_wired", "routing_tier",
                      "cost_usd_30d", "calls_30d"):
            assert field in sample, f"champ '{field}' absent du registre"

    def test_registry_inclut_les_inactifs(self, client, temp_model):
        from core.models_db import set_model_status
        assert set_model_status(temp_model, "inactive")
        ids = {m["id"] for m in client.get("/api/models/registry").json()["models"]}
        assert temp_model in ids, "un modèle inactif doit rester visible (toggle réversible)"

    def test_toggle_bascule_le_statut(self, client, temp_model):
        res = client.post(f"/api/models/{temp_model}/toggle")
        assert res.status_code == 200
        assert res.json()["enabled"] is False
        res = client.post(f"/api/models/{temp_model}/toggle")
        assert res.json()["enabled"] is True

    def test_toggle_modele_inconnu_404(self, client):
        assert client.post("/api/models/nexiste-pas-du-tout/toggle").status_code == 404

    def test_routing_tier_update_cible(self, client, temp_model):
        from core.models_db import get_model
        res = client.post(f"/api/models/{temp_model}/routing-tier", json={"routing_tier": "leger"})
        assert res.status_code == 200
        model = get_model(temp_model)
        assert model["routing_tier"] == "leger"
        # L'UPDATE ciblé ne touche pas les autres champs.
        assert model["display_name"] == "Modèle test Phase 3"
        # Valeur invalide refusée.
        assert client.post(f"/api/models/{temp_model}/routing-tier",
                           json={"routing_tier": "turbo"}).status_code == 422

    def test_update_model_merge_patch_preserve_routing_tier(self, client, temp_model):
        """Bug corrigé : POST /api/models/update effaçait routing_tier à chaque édition."""
        from core.models_db import get_model, set_model_routing_tier
        set_model_routing_tier(temp_model, "fort")
        model = get_model(temp_model)
        res = client.post("/api/models/update", json={
            "id": temp_model, "provider_id": model["provider_id"], "notes": "note de test",
        })
        assert res.status_code == 200
        after = get_model(temp_model)
        assert after["routing_tier"] == "fort", "routing_tier perdu par l'édition partielle"
        assert after["notes"] == "note de test"


# ──────────────────────────────────────────────────────────────────
# #T192 — PromptEngineer branché (validation sans appel LLM)
# ──────────────────────────────────────────────────────────────────

class TestPromptTools:
    def test_prompt_vide_refuse(self, client):
        assert client.post("/api/prompt/engineer", json={"prompt": ""}).status_code == 422
        assert client.post("/api/prompt/engineer", json={}).status_code == 422

    def test_agent_importable_et_plus_code_mort(self):
        # #T192 : l'agent est désormais importé par api/routes/prompt_tools.py.
        import inspect

        import api.routes.prompt_tools as pt
        assert "PromptEngineerAgent" in inspect.getsource(pt)
