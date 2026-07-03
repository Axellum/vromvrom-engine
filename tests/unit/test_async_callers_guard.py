# -*- coding: utf-8 -*-
"""
Garde-fou de régression : tout appel à `Router.analyze_request` doit être `await`-é.

Contexte : le run de nuit du 22/06 a rendu `Router.analyze_request` asynchrone mais
n'avait pas migré tous les appelants de prod, laissant des appels synchrones qui
récupéraient une coroutine jamais awaitée (-> crash runtime « cannot unpack
non-iterable coroutine »). Les tests unitaires classiques ne l'ont pas détecté car
ils mockaient ou adaptaient le routeur. Ce test analyse l'AST des modules de prod
et échoue si un `.analyze_request(...)` n'est pas directement enveloppé dans un
`await`, empêchant la réintroduction silencieuse de ce bug.
"""

import ast
from pathlib import Path

import pytest

# Racine du projet (deux niveaux au-dessus de tests/unit/)
_ROOT = Path(__file__).resolve().parents[2]

# Méthodes asynchrones dont tout appel DOIT être awaité dans le code de prod.
_MUST_AWAIT = {"analyze_request"}

# Modules de prod scannés (on exclut tests/, les .bak et les outils ponctuels).
_PROD_GLOBS = ["core/**/*.py", "api/**/*.py", "services/**/*.py", "agents/**/*.py"]
_PROD_FILES = ["main.py", "gui_server.py", "mcp_server.py"]


def _iter_prod_files():
    seen = set()
    for pattern in _PROD_GLOBS:
        for p in _ROOT.glob(pattern):
            if p.suffix == ".py" and ".bak" not in p.name and p not in seen:
                seen.add(p)
                yield p
    for name in _PROD_FILES:
        p = _ROOT / name
        if p.exists():
            yield p


def _unawaited_calls(tree: ast.AST) -> list[tuple[str, int]]:
    """Retourne les (méthode, ligne) des appels ciblés NON enveloppés dans un await."""
    awaited_calls: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            awaited_calls.add(id(node.value))

    offenders: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr in _MUST_AWAIT and id(node) not in awaited_calls:
                offenders.append((attr, node.lineno))
    return offenders


@pytest.mark.parametrize("path", list(_iter_prod_files()), ids=lambda p: str(p.relative_to(_ROOT)))
def test_targeted_async_methods_are_awaited(path):
    """Chaque appel de `analyze_request` (et consorts) doit être awaité."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError as exc:  # pragma: no cover - fichier non parseable
        pytest.skip(f"Non parseable : {path.name} ({exc})")
        return

    offenders = _unawaited_calls(tree)
    assert not offenders, (
        f"Appel(s) non-awaité(s) dans {path.relative_to(_ROOT)} : "
        + ", ".join(f"{m}() ligne {ln}" for m, ln in offenders)
    )
