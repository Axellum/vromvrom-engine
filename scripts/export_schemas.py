"""
scripts/export_schemas.py — Exporte les modèles d'état partagés en JSON Schema.

[HMI v2] Source unique de vérité du contrat de données moteur ↔ IHM. Les modèles
Pydantic de `core/state.py` sont LA référence ; ce script en dérive des JSON
Schemas dans `shared/schemas/`. La future IHM TypeScript génère ses types à
partir de ces schémas (ex: `json-schema-to-typescript`), garantissant que le
front et le back parlent exactement le même langage.

Usage : python scripts/export_schemas.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.state import (  # noqa: E402
    ExecutionPhase,
    GlobalState,
    StateUpdate,
    TaskPayload,
    TaskStatus,
    WorkflowMetadata,
)

# Modèles exposés à l'IHM (le contrat partagé).
EXPORTED_MODELS = [GlobalState, TaskPayload, StateUpdate, WorkflowMetadata]
# Enums exportés en plus (valeurs autorisées côté IHM).
EXPORTED_ENUMS = [ExecutionPhase, TaskStatus]


def main() -> int:
    out_dir = Path(__file__).resolve().parent.parent / "shared" / "schemas"
    out_dir.mkdir(parents=True, exist_ok=True)

    for model in EXPORTED_MODELS:
        schema = model.model_json_schema()
        (out_dir / f"{model.__name__}.json").write_text(
            json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[OK] {model.__name__}.json")

    # Les enums sérialisés à part (liste de valeurs).
    enums = {e.__name__: [m.value for m in e] for e in EXPORTED_ENUMS}
    (out_dir / "_enums.json").write_text(
        json.dumps(enums, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] _enums.json ({', '.join(enums)})")

    print(f"\nSchémas exportés → {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
