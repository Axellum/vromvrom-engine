"""
tests/unit/test_backlog_branche_jamais_perdue.py — #T396.

Depuis #T327, une tâche DreamCoder POUSSE sa branche sur origin puis efface la
copie locale. La porte d'approbation (`PUT /api/backlog/tasks/{id}`) était restée
écrite pour des branches locales :

  * elle effaçait `git_branch` sur « rejected » ET sur « pending », sans regarder
    si la branche existait encore ailleurs → le nom du travail poussé disparaissait
    de la base. Mesuré en prod le 18/08 : cinq branches réelles sur origin,
    ~2,9 M tokens de travail, plus aucune trace côté base ;
  * elle appelait `_run_git` sous `try/except`, or `_run_git` RETOURNE
    `(code, stdout, stderr)` et n'élève jamais d'exception : un merge en échec
    était donc silencieusement pris pour un succès, et la tâche marquée
    « completed » sans que rien n'ait été fusionné.

Ces tests figent les deux propriétés : **aucun travail poussé ne devient
invisible**, et **un échec Git n'est jamais rendu comme un succès**.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.routes.backlog import TaskUpdate, update_task

BRANCHE = "task/20_1787055570"


def _git_factice(reponses):
    """
    Remplace `_run_git` : `reponses` associe le 1er argument git au tuple
    (code, stdout, stderr) à rendre. Par défaut, succès muet.
    """
    appels = []

    def _faux(args, cwd="."):
        appels.append(list(args))
        return reponses.get(args[0], (0, "", ""))

    _faux.appels = appels
    return _faux


def _tache(branche=BRANCHE, statut="completed"):
    return {"id": 20, "title": "tâche", "status": statut, "git_branch": branche}


# ── La référence d'une branche qui existe encore n'est jamais effacée ────────

async def test_rejet_conserve_la_reference_si_la_branche_est_sur_origin():
    git = _git_factice({"ls-remote": (0, f"abc123\trefs/heads/{BRANCHE}", "")})
    maj = AsyncMock(return_value=True)

    with patch("api.routes.backlog._run_git", git), \
         patch("api.routes.backlog.get_task_by_id", AsyncMock(return_value=_tache())), \
         patch("api.routes.backlog.update_task_status", maj):
        reponse = await update_task(20, TaskUpdate(status="rejected"))

    maj.assert_awaited_once_with(20, "abandoned")
    # C'est la régression : `git_branch=None` effaçait le seul nom qui permettait
    # de retrouver le travail poussé.
    assert "git_branch" not in maj.await_args.kwargs
    assert reponse["branche"]["distante_restante"] is True


async def test_rejet_efface_la_reference_si_la_branche_a_disparu():
    git = _git_factice({"ls-remote": (0, "", "")})  # rien sur origin
    maj = AsyncMock(return_value=True)

    with patch("api.routes.backlog._run_git", git), \
         patch("api.routes.backlog.get_task_by_id", AsyncMock(return_value=_tache())), \
         patch("api.routes.backlog.update_task_status", maj):
        await update_task(20, TaskUpdate(status="rejected"))

    maj.assert_awaited_once_with(20, "abandoned", git_branch=None)


async def test_relance_ne_perd_jamais_la_branche_du_tour_precedent():
    """
    « Relancer » (status=pending) effaçait `git_branch`. Les tâches 18 et 20 ont
    été relancées le 18/08 alors que leur travail était DÉJÀ poussé : la relance
    a rejoué le travail et perdu la trace du précédent.
    """
    git = _git_factice({})
    maj = AsyncMock(return_value=True)

    with patch("api.routes.backlog._run_git", git), \
         patch("api.routes.backlog.get_task_by_id", AsyncMock(return_value=_tache())), \
         patch("api.routes.backlog.update_task_status", maj):
        await update_task(20, TaskUpdate(status="pending"))

    maj.assert_awaited_once_with(20, "pending", retries=0, error_message=None)
    assert "git_branch" not in maj.await_args.kwargs
    # Une relance ne touche pas à Git : pas d'appel réseau inutile.
    assert git.appels == []


# ── Un échec Git n'est jamais rendu comme un succès ──────────────────────────

async def test_merge_en_echec_ne_marque_pas_la_tache_terminee():
    git = _git_factice({
        "rev-parse": (0, "", ""),                     # branche locale présente
        "ls-remote": (0, "", ""),
        "checkout": (0, "", ""),
        "merge": (1, "", "CONFLICT (content): Merge conflict in core/engine.py"),
    })
    maj = AsyncMock(return_value=True)

    with patch("api.routes.backlog._run_git", git), \
         patch("api.routes.backlog.get_task_by_id", AsyncMock(return_value=_tache())), \
         patch("api.routes.backlog.update_task_status", maj), \
         pytest.raises(HTTPException) as err:
        await update_task(20, TaskUpdate(status="approved"))

    assert err.value.status_code == 400
    maj.assert_not_awaited()                           # la tâche reste à revoir
    assert ["merge", "--abort"] in git.appels          # la fusion est annulée


async def test_fusion_locale_reussie_marque_la_tache_terminee():
    git = _git_factice({
        "rev-parse": (0, "", ""),
        "ls-remote": (0, "", ""),
        "checkout": (0, "", ""),
        "merge": (0, "", ""),
        "branch": (0, "", ""),
    })
    maj = AsyncMock(return_value=True)

    with patch("api.routes.backlog._run_git", git), \
         patch("api.routes.backlog.get_task_by_id", AsyncMock(return_value=_tache())), \
         patch("api.routes.backlog.update_task_status", maj):
        await update_task(20, TaskUpdate(status="approved"))

    maj.assert_awaited_once_with(20, "completed", git_branch=None)


# ── La récolte d'une branche distante passe par une PR, pas par une fusion ───

async def test_approbation_refusee_si_la_branche_n_est_que_sur_origin():
    """
    Fusionner ici, c'est fusionner dans le dépôt de déploiement du Deck : il est
    en pull seul et `master` est protégée côté GitHub. Le refus est explicite —
    et surtout la tâche n'est PAS marquée terminée, contrairement à avant, où
    l'échec du merge était avalé.
    """
    git = _git_factice({
        "rev-parse": (1, "", ""),                     # pas de branche locale
        "ls-remote": (0, f"abc123\trefs/heads/{BRANCHE}", ""),
    })
    maj = AsyncMock(return_value=True)

    with patch("api.routes.backlog._run_git", git), \
         patch("api.routes.backlog.get_task_by_id", AsyncMock(return_value=_tache())), \
         patch("api.routes.backlog.update_task_status", maj), \
         pytest.raises(HTTPException) as err:
        await update_task(20, TaskUpdate(status="approved"))

    assert err.value.status_code == 409
    assert "pull request" in err.value.detail
    maj.assert_not_awaited()
    assert not any(a[0] == "merge" for a in git.appels)


async def test_approbation_sans_branche_nulle_part_est_un_404():
    git = _git_factice({"rev-parse": (1, "", ""), "ls-remote": (0, "", "")})
    maj = AsyncMock(return_value=True)

    with patch("api.routes.backlog._run_git", git), \
         patch("api.routes.backlog.get_task_by_id", AsyncMock(return_value=_tache())), \
         patch("api.routes.backlog.update_task_status", maj), \
         pytest.raises(HTTPException) as err:
        await update_task(20, TaskUpdate(status="approved"))

    assert err.value.status_code == 404
    maj.assert_not_awaited()
