"""
#46 — ouvertures concurrentes de la base runtime (chemin `generate_async` / DAG).

Les providers déportent `record_usage` via `asyncio.to_thread` : plusieurs
threads ouvrent `runtime_db.get_connection()` au même instant. Chaque ouverture
passe par `_init_schema`, qui migre les colonnes manquantes par `ALTER TABLE`.
Sur une base à l'ancien schéma, deux ouvertures simultanées lisaient toutes
deux la colonne absente puis faisaient chacune leur `ALTER` : la seconde levait
`OperationalError: duplicate column name` (mesuré sur master : 1 à 4 erreurs
pour 30 × 16 ouvertures). Le passage en WAL d'une base neuve casse de la même
façon (« database is locked »).

Ces tests n'appellent aucun LLM (garde-fou #T261). Base isolée via
`override_db_path` (motif #T347), restaurée à la fin.
"""
from __future__ import annotations

import sqlite3
import threading

import pytest

N_THREADS = 16
N_TOURS = 30

# Colonnes ajoutées par migration additive (`_ensure_columns`) : les retirer
# reconstitue une base créée avec un ancien schéma.
COLONNES_MIGREES = (("token_usage", "cache_hit_tokens"),
                    ("hitl_pending_approvals", "criteres_json"))


@pytest.fixture
def base_isolee(tmp_path):
    from core import runtime_db

    original = runtime_db.get_db_path()
    yield tmp_path
    runtime_db.override_db_path(original)


def _ouvrir_en_meme_temps(n: int) -> list[BaseException]:
    """n threads ouvrent une connexion au même instant ; renvoie les erreurs."""
    from core import runtime_db

    depart = threading.Barrier(n)
    erreurs: list[BaseException] = []

    def ouvrir() -> None:
        depart.wait()
        try:
            runtime_db.get_connection().close()
        except BaseException as exc:  # noqa: BLE001 — on veut tout voir
            erreurs.append(exc)

    threads = [threading.Thread(target=ouvrir) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return erreurs


def test_ouvertures_concurrentes_sur_un_ancien_schema(base_isolee):
    """La migration additive ne doit jamais lever « duplicate column name »."""
    from core import runtime_db

    erreurs: list[BaseException] = []
    for tour in range(N_TOURS):
        db = base_isolee / f"ancien_schema_{tour}.db"
        runtime_db.override_db_path(str(db))
        runtime_db.get_connection().close()          # schéma complet…
        conn = sqlite3.connect(db)
        for table, colonne in COLONNES_MIGREES:      # …puis rétrogradé
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {colonne}")
        conn.commit()
        conn.close()

        erreurs += _ouvrir_en_meme_temps(N_THREADS)

    assert erreurs == [], f"{len(erreurs)} erreur(s), ex. : {erreurs[:3]!r}"

    # La migration a bien eu lieu, une seule fois, sur la dernière base.
    conn = sqlite3.connect(db)
    try:
        for table, colonne in COLONNES_MIGREES:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            assert cols.count(colonne) == 1
    finally:
        conn.close()


def test_ouvertures_concurrentes_sur_une_base_neuve(base_isolee):
    """Création + passage en WAL simultanés : aucune ouverture ne doit échouer."""
    from core import runtime_db

    erreurs: list[BaseException] = []
    for tour in range(N_TOURS):
        runtime_db.override_db_path(str(base_isolee / f"neuve_{tour}.db"))
        erreurs += _ouvrir_en_meme_temps(N_THREADS)

    assert erreurs == [], f"{len(erreurs)} erreur(s), ex. : {erreurs[:3]!r}"


def test_record_usage_depuis_plusieurs_threads(base_isolee):
    """Chemin réel : N threads enregistrent, toutes les lignes arrivent."""
    from core import runtime_db
    from core.token_tracker import record_usage

    db = base_isolee / "usage.db"
    runtime_db.override_db_path(str(db))
    depart = threading.Barrier(N_THREADS)

    def ecrire(i: int) -> None:
        depart.wait()
        for j in range(5):
            record_usage("ajean_local", 10 + j, 3, session_id=f"s-{i}")

    threads = [threading.Thread(target=ecrire, args=(i,)) for i in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM token_usage").fetchone()[0] == N_THREADS * 5
    finally:
        conn.close()
