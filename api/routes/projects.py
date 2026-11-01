"""
api/routes/projects.py — Registre des projets (git + dossier local).

Lecture seule : les chemins se règlent par `projects.json` (gitignoré) ou
`MOTEUR_PROJECT_<ID>_PATH`. L'IHM affiche l'état effectif (dossier trouvé,
remote Git aligné) pour qu'un livrable Tab5 n'atterrisse plus dans le git moteur.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/projects", tags=["Projets"])


@router.get("")
async def lister_projets():
    from core.projects import snapshot_projets
    return snapshot_projets()
