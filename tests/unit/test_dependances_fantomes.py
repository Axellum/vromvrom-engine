"""
[#T431] Le KeyError qui tuait un pipeline sur quatre.

Mesuré les 20 et 21/08 sur quatre tâches autonomes : `verify_fix`,
`verify_implementation`, `read_backlog_logic`, `ecrire_tests`. Aucun de ces
identifiants n'existe dans le dépôt — le modèle les nomme dans un `depends_on`
sans jamais créer l'étape correspondante. En aval, `dag_runner` indexait
`tasks_by_id[t_id]` : l'arête vers un nœud inexistant levait un `KeyError` qui
emportait TOUT le pipeline, avec pour seul message le nom inventé.

La normalisation des identifiants ne suffisait pas : ces noms sont déjà des
slugs valides. C'est leur CIBLE qui manque, pas leur forme.
"""

from agents.planner import elaguer_dependances_fantomes, normaliser_task_ids


def test_une_dependance_vers_une_etape_inexistante_est_coupee():
    plan = [
        {"task_id": "lire_le_code", "depends_on": []},
        {"task_id": "ecrire_le_correctif", "depends_on": ["lire_le_code", "verify_fix"]},
    ]
    elague = elaguer_dependances_fantomes(plan)
    assert elague[1]["depends_on"] == ["lire_le_code"]


def test_les_dependances_reelles_sont_intactes():
    plan = [
        {"task_id": "a", "depends_on": []},
        {"task_id": "b", "depends_on": ["a"]},
        {"task_id": "c", "depends_on": ["a", "b"]},
    ]
    assert [e["depends_on"] for e in elaguer_dependances_fantomes(plan)] == [[], ["a"], ["a", "b"]]


def test_le_plan_d_origine_n_est_pas_muté():
    """Le plan sert ailleurs : l'élagage rend une copie, il ne détruit rien."""
    plan = [{"task_id": "a", "depends_on": ["fantome"]}]
    elaguer_dependances_fantomes(plan)
    assert plan[0]["depends_on"] == ["fantome"]


def test_une_etape_dont_toutes_les_dependances_sont_fantomes_reste_executable():
    """
    Elle part au stage 1 au lieu d'attendre indéfiniment quelque chose qui
    n'arrivera jamais — c'est tout l'intérêt de couper plutôt que de bloquer.
    """
    plan = [{"task_id": "seule", "depends_on": ["verify_implementation", "ecrire_tests"]}]
    assert elaguer_dependances_fantomes(plan)[0]["depends_on"] == []


def test_depends_on_absent_ou_nul_ne_casse_pas():
    plan = [{"task_id": "a"}, {"task_id": "b", "depends_on": None}]
    assert [e["depends_on"] for e in elaguer_dependances_fantomes(plan)] == [[], []]


def test_les_deux_passes_s_enchainent():
    """
    La normalisation renomme, l'élagage coupe ce qui ne vise rien. Dans cet
    ordre : une dépendance devenue valide par renommage ne doit pas être coupée.
    """
    plan = [
        {"task_id": "Lire Le Code", "depends_on": []},
        {"task_id": "corriger", "depends_on": ["Lire Le Code", "verify_fix"]},
    ]
    resultat = elaguer_dependances_fantomes(normaliser_task_ids(plan))
    assert resultat[0]["task_id"] == "lire_le_code"
    assert resultat[1]["depends_on"] == ["lire_le_code"]
