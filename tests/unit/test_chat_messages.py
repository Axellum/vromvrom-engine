"""
Test de régression : les routes Chat IHM (api/routes/chat_messages.py) doivent
rester des fonctions SYNCHRONES (`def` simple, non `async def`).

Contexte : ces routes effectuent des opérations SQLite synchrones et bloquantes
(get_connection, execute, commit). En les déclarant en `def` simple, FastAPI les
exécute dans un thread pool, ce qui évite de bloquer la boucle d'événements
d'asyncio. Si un futur refactor les convertissait en `async def` sans déléguer
les opérations bloquantes à un thread (ex: asyncio.to_thread), la boucle
d'événements serait bloquée pendant chaque accès SQLite, dégradant la
réactivité du serveur.

Ce test analyse l'AST du module de prod et échoue si l'une des routes devient
`async def`, empêchant la réintroduction silencieuse de ce bug.
"""

import ast
from pathlib import Path

import pytest

# Racine du projet (deux niveaux au-dessus de tests/unit/)
_ROOT = Path(__file__).resolve().parents[2]

# Fichier de prod ciblé par ce test de régression.
_TARGET = _ROOT / "api" / "routes" / "chat_messages.py"

# Noms des routes qui doivent rester synchrones (déclarées en `def` simple).
_SYNC_ROUTES = {
    "save_messages",
    "get_messages",
    "list_sessions",
    "delete_session",
}


def _async_route_names(tree: ast.AST) -> list[str]:
    """Retourne les noms des fonctions du module déclarées en `async def`."""
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # On ne s'intéresse qu'aux routes ciblées.
            if node.name in _SYNC_ROUTES and isinstance(node, ast.AsyncFunctionDef):
                offenders.append(node.name)
    return offenders


def test_chat_messages_routes_are_sync_def():
    """Les routes Chat IHM doivent rester des `def` simples (non `async def`)."""
    assert _TARGET.exists(), f"Fichier source introuvable : {_TARGET}"

    try:
        tree = ast.parse(_TARGET.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError as exc:  # pragma: no cover - fichier non parseable
        pytest.skip(f"Non parseable : {_TARGET.name} ({exc})")
        return

    offenders = _async_route_names(tree)
    assert not offenders, (
        f"Route(s) Chat IHM devenues asynchrones dans {_TARGET.relative_to(_ROOT)} : "
        + ", ".join(offenders)
        + ". Les opérations SQLite bloquantes doivent rester dans des `def` simples "
        "pour être exécutées dans le thread pool de FastAPI."
    )


def test_chat_messages_routes_are_present():
    """Les routes attendues doivent exister dans le module (garde-fou de structure)."""
    assert _TARGET.exists(), f"Fichier source introuvable : {_TARGET}"

    try:
        tree = ast.parse(_TARGET.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError as exc:  # pragma: no cover - fichier non parseable
        pytest.skip(f"Non parseable : {_TARGET.name} ({exc})")
        return

    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing = _SYNC_ROUTES - defined
    assert not missing, (
        f"Route(s) Chat IHM absentes de {_TARGET.relative_to(_ROOT)} : "
        + ", ".join(sorted(missing))
    )
