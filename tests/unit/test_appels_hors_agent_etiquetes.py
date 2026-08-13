"""
tests/unit/test_appels_hors_agent_etiquetes.py — Les consommateurs qui ne sont pas
des agents étiquettent quand même leur dépense (#T308).

Inventaire du 11/08 : 7 fichiers, 13 appels LLM hors `BaseAgent`, zéro étiquette.
Deux d'entre eux sont des boucles qui tournent 24h/24 sur le Deck (dreamer,
auditeur) : elles consommaient réellement, et anonymement.

Ces tests vérifient l'outillage et les deux pièges déjà payés :
- la RESTAURATION (une étiquette qui déborde sur l'appelant est un mensonge, #T294) ;
- le PONT DE THREAD (`run_in_executor` ne propage pas les ContextVar, #T301).
"""
import asyncio

import pytest

from core.agent_trace import (
    agent_courant,
    etiqueter_agent,
    lire_agent_courant,
)


class TestContextManager:
    def test_pose_puis_restaure(self):
        assert lire_agent_courant() is None
        with agent_courant("dreamer"):
            assert lire_agent_courant() == "dreamer"
        assert lire_agent_courant() is None

    def test_imbrication_rend_l_etiquette_de_l_appelant(self):
        """Le cas réel : `run_vocal_tool_loop` est appelée depuis le fast-path."""
        with agent_courant("fast_path"):
            with agent_courant("vocal_tools"):
                assert lire_agent_courant() == "vocal_tools"
            assert lire_agent_courant() == "fast_path", (
                "sans restauration, la suite du fast-path serait facturée à vocal_tools"
            )

    def test_restauration_meme_en_cas_d_exception(self):
        with pytest.raises(ValueError):
            with agent_courant("auditor"):
                raise ValueError("échec de l'appel LLM")
        assert lire_agent_courant() is None


class TestDecorateur:
    @pytest.mark.asyncio
    async def test_le_decorateur_etiquette_toute_la_coroutine(self):
        @etiqueter_agent("vocal_jobs")
        async def _travail():
            return lire_agent_courant()

        assert await _travail() == "vocal_jobs"
        assert lire_agent_courant() is None

    @pytest.mark.asyncio
    async def test_le_decorateur_preserve_signature_et_retour(self):
        @etiqueter_agent("coding_front")
        async def _travail(a, b=2):
            """docstring conservée"""
            return a + b

        assert await _travail(1) == 3
        assert await _travail(1, b=10) == 11
        assert _travail.__doc__ == "docstring conservée"
        assert _travail.__name__ == "_travail"


class TestPontDeThread:
    """#T301 : l'étiquette ne franchit que `to_thread`. Vérifié sur les vrais sites."""

    @pytest.mark.asyncio
    async def test_l_etiquette_franchit_to_thread(self):
        @etiqueter_agent("vocal_jobs")
        async def _travail():
            return await asyncio.to_thread(lire_agent_courant)

        assert await _travail() == "vocal_jobs"

    def test_aucun_site_llm_ne_passe_plus_par_run_in_executor(self):
        """Garde-fou : un `run_in_executor` sur un chemin LLM annulerait l'étiquetage.

        Analyse en AST et non par recherche de texte : plusieurs docstrings citent
        `run_in_executor` pour expliquer justement pourquoi on ne l'utilise pas, et
        un grep les compterait comme des fautes.

        `core/models_db.py` est exclu : ses `run_in_executor` servent des LECTURES
        SQLite, n'appellent aucun LLM et ne dépendent d'aucune ContextVar.
        """
        import ast
        import pathlib

        racine = pathlib.Path(__file__).resolve().parents[2]
        fautifs = []
        for chemin in ("services", "core", "api", "agents"):
            for fichier in (racine / chemin).rglob("*.py"):
                if fichier.name == "models_db.py" or "backups_prod" in str(fichier):
                    continue
                try:
                    arbre = ast.parse(fichier.read_text(encoding="utf-8", errors="ignore"))
                except SyntaxError:
                    continue
                for noeud in ast.walk(arbre):
                    if (
                        isinstance(noeud, ast.Call)
                        and isinstance(noeud.func, ast.Attribute)
                        and noeud.func.attr == "run_in_executor"
                    ):
                        fautifs.append(f"{fichier.relative_to(racine)}:{noeud.lineno}")

        assert not fautifs, (
            "appel réel à run_in_executor sur un chemin potentiellement LLM — il ne "
            f"propage pas les ContextVar, l'étiquette d'agent y serait perdue : {fautifs}"
        )
