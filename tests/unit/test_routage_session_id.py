"""
tests/unit/test_routage_session_id.py — Une décision de routage est rattachable
à une session, donc à une consommation réelle (#T296, volet B).

`routing_decisions.session_id` était vide sur 68/68 lignes : le Router ne
recevait tout simplement pas la session (aucune occurrence de `session_id` dans
`core/router.py`). Une décision de routage ne pouvait donc être rapprochée
d'aucun coût, d'aucun modèle, d'aucun agent.

Ces tests écrivent dans une base créée par le VRAI schéma (`runtime_db._init_schema`
via `override_db_path`), et n'appellent aucun LLM : le Router est instancié sans
gateway, donc le slow-path de classification sémantique est structurellement
hors d'atteinte.
"""
import sqlite3

import pytest


@pytest.fixture
def base_temporaire(tmp_path, monkeypatch):
    """Base neuve au schéma canonique, isolée de la base de développement."""
    from core import runtime_db

    db = tmp_path / "routage.db"
    runtime_db.override_db_path(str(db))
    runtime_db.get_connection().close()  # déclenche _init_schema
    return str(db)


def _lignes(db: str, requete: str) -> list:
    conn = sqlite3.connect(db)
    lignes = conn.execute(requete).fetchall()
    conn.close()
    return lignes


class TestSessionIdEcrite:
    """Le maillon manquant : la clé de rattachement."""

    @pytest.mark.asyncio
    async def test_analyze_request_ecrit_la_session_dans_la_decision(self, base_temporaire):
        from core.router import Router

        routeur = Router()  # sans gateway : aucun appel LLM possible
        await routeur.analyze_request("bonjour", session_id="s_t296b")

        lignes = _lignes(
            base_temporaire, "SELECT session_id FROM routing_decisions"
        )
        assert lignes, "aucune décision de routage enregistrée"
        assert all(ligne[0] == "s_t296b" for ligne in lignes), lignes

    @pytest.mark.asyncio
    async def test_sans_session_la_colonne_reste_vide_et_ne_ment_pas(self, base_temporaire):
        """Les outils MCP (recommandation, délégation) n'ont pas de session d'exécution.

        Ils ne doivent pas en inventer une : une session fictive polluerait toute
        jointure ultérieure avec des rattachements faux.
        """
        from core.router import Router

        await Router().analyze_request("bonjour")

        lignes = _lignes(base_temporaire, "SELECT session_id FROM routing_decisions")
        assert lignes, "aucune décision de routage enregistrée"
        assert all(ligne[0] in ("", None) for ligne in lignes), lignes


class TestJointureRoutageConsommation:
    """Ce que la clé permet : relier une décision à son coût réel."""

    @pytest.mark.asyncio
    async def test_jointure_routage_token_usage(self, base_temporaire):
        """Le lien routage → modèle réellement appelé, sans dupliquer la colonne.

        C'est la raison pour laquelle `resolved_model` reste volontairement vide :
        le routage résout un tier, pas un modèle (#T288). La vérité vient de
        `token_usage`, rattaché par la session.
        """
        from core.router import Router
        from core.token_tracker import record_usage

        await Router().analyze_request("bonjour", session_id="s_jointure")
        record_usage("gemma-4-31b", 100, 50, session_id="s_jointure")

        lignes = _lignes(
            base_temporaire,
            "SELECT r.routing_type, t.model, t.total_tokens "
            "FROM routing_decisions r "
            "JOIN token_usage t ON t.session_id = r.session_id "
            "WHERE r.session_id = 's_jointure'",
        )

        assert lignes, "la jointure ne rend rien : le rattachement est cassé"
        assert lignes[0][1] == "gemma-4-31b", lignes
        assert lignes[0][2] == 150, lignes
