# -*- coding: utf-8 -*-
r"""
Garde-fou de convention : les regex de validation de sécurité doivent ancrer la
fin de chaîne avec `\Z`, pas avec `$`.

En regex Python, `$` matche aussi juste avant un `\n` terminal — un `"valeur\n"`
hors charset peut alors passer un filtre censé être strict. Deux bypass réels ont
été corrigés ainsi (core/validation.py:validate_service_data et
api/routes/backlog.py:_VALID_BRANCH_RE). Ce test empêche la réintroduction du
motif dans les modules de validation listés.
"""

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

# Modules dont les regex servent à valider des entrées externes (charset strict).
_VALIDATION_FILES = [
    "core/validation.py",
    "api/routes/backlog.py",
]

# Repère un littéral de pattern se terminant par un `$` ancrant la fin de chaîne,
# c.-à-d. `...$"` ou `...$'` — en excluant un `$` échappé (`\$`, littéral).
_BARE_DOLLAR_END = re.compile(r"""(?<!\\)\$["']""")


@pytest.mark.parametrize("rel", _VALIDATION_FILES)
def test_validation_regex_use_Z_not_dollar(rel):
    path = _ROOT / rel
    if not path.exists():
        pytest.skip(f"absent : {rel}")

    offenders = []
    for i, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # On ne s'intéresse qu'aux lignes construisant un pattern regex.
        if "re.compile" not in line and "re.match" not in line and "re.fullmatch" not in line:
            continue
        if _BARE_DOLLAR_END.search(line):
            offenders.append((i, line.strip()))

    assert not offenders, (
        f"{rel} : regex de validation ancré avec `$` (utiliser `\\Z`) — "
        + " | ".join(f"L{ln}: {txt}" for ln, txt in offenders)
    )
