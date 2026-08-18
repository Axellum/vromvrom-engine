"""
tests/unit/test_event_store_isolation_t348.py — l'EventStore ne touche plus la
base réelle du dépôt (#T348).

#T348 : `core/event_store.py` calculait son chemin par défaut indépendamment de
`core.runtime_db` (`DEFAULT_DB` figé à l'import), et le singleton
`get_event_store()` figeait l'instance au premier appel du process. Un test qui
appelait `override_db_path()` croyait être isolé, pendant que l'EventStore
continuait d'écrire dans la vraie base du dépôt (moteur_runtime.db). La fixture
autouse `_isoler_base_runtime` (tests/conftest.py) réoriente désormais la base
pour CHAQUE test ; ces tests vérifient que l'EventStore la respecte.

Les tests :
1. LE TEST CENTRAL : `get_event_store().log(...)` sans aucune isolation locale
   (ni `override_db_path`, ni `tmp_path` pour la base) n'ajoute PAS de ligne au
   fichier `moteur_runtime.db` de la racine du dépôt. Échoue sur master.
2. Le singleton suit un changement de chemin : deux `get_event_store()`
   séparés par un `override_db_path()` n'écrivent pas dans le même fichier.

Le second critère d'acceptation (#T348) — le compte de lignes de `events` dans
la base du dépôt est identique avant/après un `pytest tests/unit -q` complet —
est vérifié à la main (commande de reproduction), pas par un test unitaire.
"""
import asyncio
import sqlite3
from pathlib import Path

from core import runtime_db


def _compter_events(fichier: str) -> int:
    """Compte les lignes de la table `events` d'un fichier SQLite donné."""
    if not Path(fichier).exists():
        return 0
    conn = sqlite3.connect(fichier)
    try:
        # Sur CI le fichier dépôt peut exister (créé par un autre module) sans
        # table `events` — ce n'est pas une écriture EventStore.
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'"
        ).fetchone()
        if row is None:
            return 0
        return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        conn.close()


def test_l_event_store_n_ecrit_pas_dans_la_base_du_depot():
    """LE TEST CENTRAL #T348.

    Ce test n'isole RIEN lui-même : il ne connaît ni `override_db_path`, ni un
    `tmp_path` pour la base — il se repose entièrement sur la fixture autouse
    `_isoler_base_runtime`. Il appelle `get_event_store().log(...)` puis vérifie
    que le fichier `moteur_runtime.db` à la racine du dépôt n'a pas gagné une
    ligne.

    Sur master (avant #T348), ce test ÉCHOUE : l'EventStore écrivait dans la
    vraie base du dépôt.
    """
    from core.event_store import get_event_store

    racine = Path(__file__).resolve().parent.parent.parent
    fichier_depot = str(racine / "moteur_runtime.db")

    avant = _compter_events(fichier_depot)

    async def _ecrire():
        es = get_event_store()
        await es.log("request_received", source="test_t348", payload={"prompt": "bonjour"})

    asyncio.run(_ecrire())

    apres = _compter_events(fichier_depot)

    assert apres == avant, (
        f"l'EventStore a écrit dans la base du dépôt : {avant} → {apres}. "
        "La fixture autouse _isoler_base_runtime n'a pas suffi — vérifie "
        "core/event_store.py (chemin résolu à l'appel + singleton réinitialisé)."
    )


def test_le_singleton_suit_un_changement_de_chemin():
    """Le singleton se réinitialise quand le chemin change (#T348).

    Deux `get_event_store()` séparés par un `override_db_path()` n'écrivent pas
    dans le même fichier : le second suit le nouveau chemin. C'est le garde-fou
    qui interdit de traiter #T348 en ajoutant seulement `override_db_path()`
    dans `test_fast_path_agent_trace.py`.
    """
    from core.event_store import get_event_store

    # Chemin A : celui posé par la fixture autouse pour CE test.
    chemin_a = runtime_db.get_db_path()

    async def _ecrire():
        es = get_event_store()
        await es.log("request_received", source="test_t348_a", payload={})
        return es.db_path

    chemin_utilise_a = asyncio.run(_ecrire())
    assert chemin_utilise_a == chemin_a

    # Réorienter vers un autre fichier, puis réécrire : le singleton doit suivre.
    chemin_b = str(Path(chemin_a).with_name("moteur_runtime_iso_b.db"))
    runtime_db.override_db_path(chemin_b)

    async def _ecrire_b():
        es_b = get_event_store()
        await es_b.log("request_received", source="test_t348_b", payload={})
        return es_b.db_path

    chemin_utilise_b = asyncio.run(_ecrire_b())

    assert chemin_utilise_b == chemin_b, (
        f"le singleton n'a pas suivi le changement de chemin : "
        f"il écrit encore dans {chemin_utilise_b!r} au lieu de {chemin_b!r}."
    )
    assert chemin_utilise_a != chemin_utilise_b
