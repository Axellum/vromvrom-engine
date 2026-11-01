"""
tests/unit/test_agent_steps.py — la trace pas-à-pas des agents (#T422).

La table `agent_steps` existait depuis l'origine — schéma, deux index, purge de
rétention — et comptait **0 ligne** sur la base de production du Deck au 19/08.
Un `grep agent_steps` sur le dépôt ne rendait que le `CREATE TABLE`, les index,
le `DELETE` de la purge, et un test qui vérifie que la table existe.

Conséquence : pour reconstituer la campagne autonome des 18–19/08, il a fallu
croiser les branches Git, les rapports de cycle JSON et le journal systemd. On
voyait le résultat, jamais le chemin.

Ces tests figent les trois propriétés qui font la différence entre une trace et
une table morte : elle s'écrit, elle se relit, et elle ne casse jamais l'agent
qu'elle observe.
"""

import json

import pytest

from core import agent_steps as trace


@pytest.fixture
def base(tmp_path, monkeypatch):
    """Base runtime isolée — jamais la `moteur_runtime.db` de prod dans les tests."""
    from core import runtime_db

    chemin = str(tmp_path / "runtime_test.db")
    monkeypatch.setattr(runtime_db, "_DB_PATH", chemin)
    with runtime_db.get_connection() as conn:
        conn.commit()
    return chemin


# ── Phase déduite du nom de l'agent ─────────────────────────────────────────

@pytest.mark.parametrize(
    ("nom", "phase"),
    [
        ("planner", "planning"),
        ("executor", "execution"),
        ("ha_executor", "execution"),
        ("reviewer", "review"),
        ("auditor", "review"),
        ("healing", "correction"),
        ("dreamer", "autre"),
        ("", "autre"),
    ],
)
def test_la_phase_tient_la_promesse_du_schema(nom, phase):
    """La colonne documente « planning, execution, review, correction »."""
    assert trace.phase_de_l_agent(nom) == phase


# ── Écriture ────────────────────────────────────────────────────────────────

def test_une_etape_est_ecrite_et_relue(base):
    assert trace.enregistrer_etape(
        session_id="s1", agent_name="planner",
        objectif="Découper la tâche en étapes", statut="success",
        task_id="t7", extrait_resultat="Plan en 3 étapes", debut=None)

    etapes = trace.etapes_de_session("s1")

    assert len(etapes) == 1
    assert etapes[0]["agent_name"] == "planner"
    assert etapes[0]["phase"] == "planning"
    assert etapes[0]["task_id"] == "t7"
    assert etapes[0]["thought"] == "Découper la tâche en étapes"
    assert etapes[0]["observations"]["statut"] == "success"
    assert etapes[0]["observations"]["extrait"] == "Plan en 3 étapes"


def test_les_etapes_se_numerotent_dans_l_ordre(base):
    for agent in ("planner", "executor", "reviewer"):
        trace.enregistrer_etape(
            session_id="s2", agent_name=agent, objectif="o", statut="success")

    etapes = trace.etapes_de_session("s2")

    assert [e["step_index"] for e in etapes] == [0, 1, 2]
    assert [e["agent_name"] for e in etapes] == ["planner", "executor", "reviewer"]
    assert [e["phase"] for e in etapes] == ["planning", "execution", "review"]


def test_les_sessions_ne_se_melangent_pas(base):
    trace.enregistrer_etape(session_id="a", agent_name="planner", objectif="o", statut="success")
    trace.enregistrer_etape(session_id="b", agent_name="executor", objectif="o", statut="success")
    trace.enregistrer_etape(session_id="a", agent_name="reviewer", objectif="o", statut="success")

    assert [e["step_index"] for e in trace.etapes_de_session("a")] == [0, 1]
    assert len(trace.etapes_de_session("b")) == 1


def test_une_erreur_d_agent_est_tracee(base):
    trace.enregistrer_etape(
        session_id="s3", agent_name="executor", objectif="Corriger le module",
        statut="error", erreur="SyntaxError ligne 12")

    obs = trace.etapes_de_session("s3")[0]["observations"]

    assert obs["statut"] == "error"
    assert "SyntaxError ligne 12" in obs["erreur"]


def test_sans_session_id_on_n_ecrit_rien(base):
    """Une étape hors session n'est rattachable à rien : elle ne sert personne."""
    assert trace.enregistrer_etape(
        session_id="", agent_name="executor", objectif="o", statut="success") is False


# ── Coût : delta sur token_usage, pas un compteur parallèle ─────────────────

def test_le_cout_est_le_delta_de_token_usage_sur_la_fenetre(base):
    import time

    from core.runtime_db import get_connection

    debut = time.time()
    with get_connection() as conn:
        # Avant la fenêtre : ne doit PAS être imputé à l'étape.
        conn.execute(
            "INSERT INTO token_usage (session_id, timestamp, model, prompt_tokens, "
            "completion_tokens, total_tokens, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("s4", debut - 60, "m", 999, 999, 1998, 9.99))
        # Dans la fenêtre.
        conn.execute(
            "INSERT INTO token_usage (session_id, timestamp, model, prompt_tokens, "
            "completion_tokens, total_tokens, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("s4", debut + 1, "m", 120, 30, 150, 0.0042))
        conn.commit()

    trace.enregistrer_etape(
        session_id="s4", agent_name="executor", objectif="o",
        statut="success", debut=debut)

    etape = trace.etapes_de_session("s4")[0]

    assert etape["prompt_tokens"] == 120
    assert etape["completion_tokens"] == 30
    assert etape["cost_usd"] == pytest.approx(0.0042)


def test_sans_debut_le_cout_reste_a_zero(base):
    """Pas de fenêtre = pas d'imputation. On n'invente pas un coût."""
    trace.enregistrer_etape(
        session_id="s5", agent_name="executor", objectif="o", statut="success")

    etape = trace.etapes_de_session("s5")[0]

    assert (etape["prompt_tokens"], etape["completion_tokens"], etape["cost_usd"]) == (0, 0, 0.0)


# ── Robustesse : la trace ne casse jamais l'agent qu'elle observe ───────────

def test_une_base_inaccessible_ne_leve_pas(monkeypatch):
    """Une trace qui ferait échouer l'agent serait pire que pas de trace."""
    def explose():
        raise RuntimeError("base verrouillée")

    monkeypatch.setattr("core.runtime_db.get_connection", explose)

    assert trace.enregistrer_etape(
        session_id="s6", agent_name="executor", objectif="o", statut="success") is False
    assert trace.etapes_de_session("s6") == []


def test_les_textes_trop_longs_sont_bornes(base):
    """Une trace reste lisible : ce n'est pas une archive du transcript."""
    trace.enregistrer_etape(
        session_id="s7", agent_name="executor", objectif="x" * 9000,
        statut="success", extrait_resultat="y" * 9000)

    etape = trace.etapes_de_session("s7")[0]

    assert len(etape["thought"]) < 2100
    assert "caractères)" in etape["thought"]
    assert len(etape["observations"]["extrait"]) < 2100


def test_un_resultat_non_textuel_ne_casse_pas(base):
    """`result_data` est un `Any` : dict, liste, objet — tout doit passer."""
    trace.enregistrer_etape(
        session_id="s8", agent_name="executor", objectif="o", statut="success",
        extrait_resultat={"fichiers": ["a.py"], "ok": True})

    assert "fichiers" in trace.etapes_de_session("s8")[0]["observations"]["extrait"]


# ── Ce que la trace n'enregistre PAS, et pourquoi ───────────────────────────

def test_tool_calls_reste_nul_par_honnetete(base):
    """
    À ce niveau, la boucle ReAct interne de l'exécuteur n'est pas visible.
    Mieux vaut une colonne vide qu'une colonne remplie d'« outils autorisés »
    qu'un lecteur prendrait pour « outils appelés ». La limite est ici, écrite.
    """
    from core.runtime_db import get_connection

    trace.enregistrer_etape(
        session_id="s9", agent_name="executor", objectif="o", statut="success")

    with get_connection() as conn:
        valeur = conn.execute(
            "SELECT tool_calls_json FROM agent_steps WHERE session_id = 's9'").fetchone()[0]

    assert valeur is None


# ── Le câblage : aucun agent ne peut oublier de tracer ──────────────────────

def _faux_agents():
    """Deux agents jetables : un qui rend un résultat, un qui lève."""
    from agents.base_agent import BaseAgent
    from core.state import StateUpdate

    class AgentQuiReussit(BaseAgent):
        async def invoke(self, payload):
            return StateUpdate(agent_name=self.name, status="success",
                               result_data="ligne 12 corrigée")

    class AgentQuiExplose(BaseAgent):
        async def invoke(self, payload):
            raise ValueError("boum")

    return AgentQuiReussit, AgentQuiExplose


@pytest.mark.asyncio
async def test_l_enveloppe_de_base_agent_trace_chaque_invocation(base):
    """
    La trace est posée dans `BaseAgent.__init_subclass__`, comme #T296 et #T298 :
    les quinze call-sites de `.invoke()` y passent, aucun ne peut l'oublier.
    """
    from core.state import TaskPayload

    Reussit, _ = _faux_agents()
    payload = TaskPayload(task_objective="Corriger le module X",
                          metadata={"session_id": "cablage"}, task_id="t1")

    await Reussit("executor", "sp").invoke(payload)
    await Reussit("reviewer", "sp").invoke(payload)

    etapes = trace.etapes_de_session("cablage")

    assert [e["agent_name"] for e in etapes] == ["executor", "reviewer"]
    assert [e["phase"] for e in etapes] == ["execution", "review"]
    assert etapes[0]["thought"] == "Corriger le module X"
    assert etapes[0]["observations"]["extrait"] == "ligne 12 corrigée"
    assert etapes[0]["observations"]["duree_ms"] is not None


@pytest.mark.asyncio
async def test_un_agent_qui_leve_laisse_quand_meme_sa_trace(base):
    """
    C'est le cas qui compte le plus : une tâche qui échoue, et on veut savoir
    où. L'exception continue de remonter — la trace ne l'avale pas.
    """
    from core.state import TaskPayload

    _, Explose = _faux_agents()
    payload = TaskPayload(task_objective="Tâche condamnée",
                          metadata={"session_id": "echec"})

    with pytest.raises(ValueError, match="boum"):
        await Explose("planner", "sp").invoke(payload)

    etapes = trace.etapes_de_session("echec")

    assert len(etapes) == 1
    assert etapes[0]["observations"]["statut"] == "interrompu"


@pytest.mark.asyncio
async def test_sans_session_id_l_agent_tourne_sans_trace(base):
    """Un appel hors session reste légal : il ne trace rien, il ne casse rien."""
    from core.state import TaskPayload

    Reussit, _ = _faux_agents()
    resultat = await Reussit("executor", "sp").invoke(
        TaskPayload(task_objective="Hors session"))

    assert resultat.status == "success"
    assert trace.etapes_de_session("") == []


def test_les_observations_sont_du_json_valide(base):
    from core.runtime_db import get_connection

    trace.enregistrer_etape(
        session_id="s10", agent_name="reviewer", objectif="o", statut="success")

    with get_connection() as conn:
        brut = conn.execute(
            "SELECT observations_json FROM agent_steps WHERE session_id = 's10'").fetchone()[0]

    assert set(json.loads(brut)) >= {"statut", "erreur", "extrait", "duree_ms"}
