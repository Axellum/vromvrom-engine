"""
test_tableau_de_bord.py — Analyse du journal par le tableau de bord (#T245/#T253/#T240).

La partie fragile du tableau de bord n'est pas SQL, c'est la lecture du journal :
les verdicts de contrat et les échecs de cascade ne sont écrits NULLE PART en base,
seulement journalisés. Si ces motifs se désynchronisent des `logger.*` du moteur,
le tableau affichera des zéros rassurants — le pire des résultats, puisqu'il serait
lu comme « aucun problème » au lieu de « je ne sais pas ».

D'où ces tests : les lignes ci-dessous sont recopiées des vrais appels `logger`
(`core/review_loop.py`, `core/llm/providers/deepseek.py`, `core/circuit_breaker`),
et un test dédié vérifie qu'aucun compteur ne s'invente de valeur sur un journal vide.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.tableau_de_bord import analyser_journal, rendre


# Lignes recopiées telles quelles depuis les logger.* du moteur.
JOURNAL = [
    "2026-08-10 12:00:01 [INFO] core.review_loop - [REVIEW] [T253] ✅ Contrat d'acceptation satisfait au round 1 (3 critère(s)) — revue LLM inutile.",
    "2026-08-10 12:05:02 [WARNING] core.review_loop - [REVIEW] [T253] ❌ Contrat d'acceptation en échec au round 1 : 2 critère(s) non satisfait(s).",
    "2026-08-10 12:06:00 [INFO] core.review_loop - [REVIEW] [T253] Contrat non déterminable (aucun critère vérifiable) — repli sur la revue LLM.",
    "2026-08-10 12:06:30 [INFO] core.review_loop - [REVIEW] ✅ Round 1 : code validé.",
    "2026-08-10 12:07:00 [INFO] agents.planner - [PLANNER] [T245] Tiers normalisés : 3 map(s) en tier léger, reduce = synthese, 1 tâche(s) inchangée(s).",
    "2026-08-10 12:07:05 [INFO] core.dag_runner - [DAG] 🗺️ MapReduce dynamique démarré pour t1 : 3 chunks → agent 'executor' (tier maps 'leger' → tier reduce 'fort')",
    "2026-08-10 12:07:04 [INFO] tools.dag_map_reduce - [MAP_REDUCE] Outil déclenché : 3 fragments → tool_mapreduce_1786",
    "2026-08-10 12:08:00 [WARNING] core.llm - [FALLBACK GATEWAY] Circuit ouvert pour le modèle 'mistral-large-latest'. Modèle court-circuité.",
    "2026-08-10 12:08:01 [WARNING] core.llm - [FALLBACK GATEWAY] Échec du modèle cohere-command-r : timeout. Bascule...",
    "2026-08-10 12:08:02 [WARNING] core.llm - [CIRCUIT BREAKER] Rate limit (429) détecté sur gemini-3.5-flash. Disjoncteur déclenché.",
]


class TestAnalyseJournal:
    def test_verdicts_de_contrat(self):
        r = analyser_journal(JOURNAL)
        assert r["contrat_satisfait"] == 1
        assert r["contrat_echec"] == 1
        assert r["contrat_indeterminable"] == 1
        assert r["revues_llm"] == 1

    def test_part_tranchee_par_preuve(self):
        # 2 verdicts de contrat (satisfait + échec) sur 3 revues au total → 67 %.
        # Le « non déterminable » ne compte pas : il n'a rien tranché, il s'est replié.
        assert analyser_journal(JOURNAL)["part_tranchee_par_contrat"] == 67

    def test_signaux_fan_out(self):
        r = analyser_journal(JOURNAL)
        assert r["plans_normalises"] == 1
        assert r["mapreduce_demarres"] == 1
        assert r["outil_map_reduce_appele"] == 1

    def test_sante_cascade_par_modele(self):
        r = analyser_journal(JOURNAL)
        assert r["circuits_ouverts"]["mistral-large-latest"] == 1
        assert r["circuits_ouverts"]["gemini-3.5-flash"] == 1   # 429 → disjoncteur
        assert r["echecs_par_modele"]["cohere-command-r"] == 1

    def test_journal_vide_ne_conclut_rien(self):
        r = analyser_journal([])
        assert r["lignes_analysees"] == 0
        assert r["contrat_satisfait"] == 0
        # Aucune revue → pas de pourcentage inventé (None, jamais 0 ni 100).
        assert r["part_tranchee_par_contrat"] is None
        assert r["circuits_ouverts"] == {}

    def test_lignes_hors_sujet_ignorees(self):
        bruit = [
            "2026-08-10 12:00:00 [INFO] uvicorn - GET /healthz 200",
            "2026-08-10 12:00:01 [INFO] core.models_db - catalogue chargé",
        ]
        r = analyser_journal(bruit)
        assert r["lignes_analysees"] == 2
        assert sum(v for k, v in r.items() if isinstance(v, int) and k != "lignes_analysees") == 0

    def test_variantes_de_circuit_ouvert(self):
        r = analyser_journal([
            "[FALLBACK GATEWAY] Circuit ouvert pour le modèle structuré 'deepseek-chat'. Modèle court-circuité.",
            "[FALLBACK GATEWAY] Circuit ouvert pour le stream 'deepseek-chat'. Modèle court-circuité.",
        ])
        assert r["circuits_ouverts"]["deepseek-chat"] == 2

    def test_messages_contrat_seul_sur_dag_echoue(self):
        """Les logs de `contrat_seul` doivent compter comme verdicts #T253."""
        lignes = [
            "[REVIEW] [T253] ✅ Le DAG a signalé une erreur, mais le contrat d'acceptation "
            "est SATISFAIT (2 critère(s) vérifiés) : l'objectif est prouvé atteint malgré "
            "l'échec d'une tâche.",
            "[REVIEW] [T253] ❌ DAG en échec ET contrat non satisfait : 1 critère(s) en échec.",
            "[REVIEW] [T253] DAG en échec et contrat non déterminable "
            "(1 critère(s) non vérifiable(s)) — verdict inchangé.",
        ]
        r = analyser_journal(lignes)
        assert r["contrat_satisfait"] == 1
        assert r["contrat_echec"] == 1
        assert r["contrat_indeterminable"] == 1
        assert r["contrat_sur_dag_echoue"] == 1
        # satisfait+échec = 2, pas de revue LLM → 100 % tranché par contrat
        assert r["part_tranchee_par_contrat"] == 100



class TestRendu:
    def test_journal_vide_affiche_en_attente(self):
        """journalctl OK mais 0 ligne → analyser_journal([]), pas « ⚠️ None »."""
        texte = rendre({}, {}, analyser_journal([]), None, jours=3)
        assert "EN ATTENTE" in texte
        assert "⚠️  None" not in texte
        assert "aucun contrat évalué" in texte

    def test_revues_llm_sans_contrat_ne_disent_pas_aucune_revue(self):
        jrn = analyser_journal([
            "2026-08-10 12:06:30 [INFO] core.review_loop - [REVIEW] ✅ Round 1 : code validé.",
            "2026-08-10 12:07:00 [INFO] core.review_loop - [REVIEW] ❌ Round 2 : corrections requises.",
        ])
        assert jrn["revues_llm"] == 2
        assert jrn["contrat_satisfait"] == 0
        texte = rendre({}, {}, jrn, None, jours=2)
        assert "2 revue(s) LLM" in texte
        assert "aucune revue n'a tourné" not in texte
        assert "EN ATTENTE" in texte