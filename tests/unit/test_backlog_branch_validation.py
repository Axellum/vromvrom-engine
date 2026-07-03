# -*- coding: utf-8 -*-
r"""
Régression : le charset strict des noms de branche Git (P0-1.3) ne doit pas
être contournable par un saut de ligne final.

Avant correctif, `_VALID_BRANCH_RE` utilisait `$` (qui matche avant un \n
terminal) au lieu de `\Z`, laissant passer un nom du type "branche\n" — hors
charset voulu — avant son passage à `_run_git(["merge", branch])`.
"""

import pytest

from api.routes.backlog import _VALID_BRANCH_RE


@pytest.mark.parametrize("name", [
    "master",
    "feature/refonte-hmi",
    "release-1.2.0",
    "claude-auto-20260623",
    "a/b/c_d.e",
])
def test_valid_branch_names(name):
    assert _VALID_BRANCH_RE.match(name), f"devrait être accepté : {name!r}"


@pytest.mark.parametrize("name", [
    "evil\n",            # saut de ligne final (le bug corrigé)
    "ok\n",
    "branche\nmerge",    # saut de ligne interne
    "-injection",        # tiret en tête (option git)
    "a b",               # espace
    "feature;rm",        # caractère hors charset
    "",                  # vide
])
def test_invalid_branch_names_rejected(name):
    assert not _VALID_BRANCH_RE.match(name), f"devrait être rejeté : {name!r}"
