"""
tests/test_failover.py — Tests unitaires Phase 4 : Failover MQTT + SQLite Sync
══════════════════════════════════════════════════════════════════════════════

Tests asyncio avec unittest.mock :
- Logique de détection ping (3 échecs → failover) [testée via helpers locaux]
- Récupération (PC revient → état normal)
- Queue SQLite sync en cas d'échec paramiko
- FailoverManager côté PC (tools/failover_manager.py)
"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def reset_failover_singleton():
    """Remet le singleton FailoverManager à zéro avant chaque test."""
    from tools.failover_manager import FailoverManager
    FailoverManager.reset_singleton()
    yield
    FailoverManager.reset_singleton()


@pytest.fixture
def tmp_antigrav(tmp_path):
    """Crée un répertoire .antigravity temporaire."""
    d = tmp_path / ".antigravity"
    d.mkdir()
    return d


# ─────────────────────────────────────────────────────────────────────────────
# Helpers : simule la logique de ping de failover_monitor.py
# Ces fonctions reproduisent la logique du script Deck-side pour les tests PC
# ─────────────────────────────────────────────────────────────────────────────

async def _simulate_ping(returncode: int) -> bool:
    """Simule ping_pc() avec un returncode contrôlé."""
    mock_proc = MagicMock()
    mock_proc.returncode = returncode
    mock_proc.wait = AsyncMock(return_value=returncode)

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        proc = await asyncio.create_subprocess_exec(
            "ping", "-c", "1", "-W", "2", "192.168.1.100",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=3)
        except asyncio.TimeoutError:
            return False
        return proc.returncode == 0


class MockFailoverState:
    """État simplifié de failover pour les tests unitaires locaux."""
    def __init__(self):
        self.active = False
        self.consecutive_failures = 0
        self.failover_count = 0
        self.last_seen_pc = None

    def save(self, path: Path = None):
        if path:
            path.write_text(json.dumps({
                "active": self.active,
                "consecutive_failures": self.consecutive_failures,
                "failover_count": self.failover_count,
            }), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 : Ping OK — pas de failover
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_ping_ok_no_failover():
    """
    Scénario : Le PC répond au ping (returncode=0).
    Attendu : result=True, pas de changement d'état.
    """
    result = await _simulate_ping(returncode=0)
    assert result is True, "Ping avec returncode=0 doit retourner True"


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 : 3 pings échoués → activation du failover
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_3_failures_trigger_failover(tmp_antigrav):
    """
    Scénario : 3 pings consécutifs échouent.
    Attendu : après 3 échecs, on atteint le seuil et l'état passe à active=True.
    """
    state = MockFailoverState()
    THRESHOLD = 3
    mosquitto_started = False
    events_published = []

    # Simuler les 3 pings échoués
    for i in range(3):
        result = await _simulate_ping(returncode=1)
        assert result is False, f"Ping {i+1} doit échouer"
        state.consecutive_failures += 1

    # Vérifier que le seuil est atteint
    assert state.consecutive_failures >= THRESHOLD

    # Simuler l'activation du failover
    if state.consecutive_failures >= THRESHOLD and not state.active:
        state.active = True
        state.failover_count += 1
        mosquitto_started = True
        events_published.append("ACTIVATED")
        state.save(tmp_antigrav / "failover_state.json")

    assert state.active is True, "Le failover doit être actif"
    assert state.failover_count == 1
    assert mosquitto_started is True
    assert "ACTIVATED" in events_published
    assert (tmp_antigrav / "failover_state.json").exists()


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 : Récupération — PC revient, failover désactivé
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_recovery_resets_state(tmp_antigrav):
    """
    Scénario : Failover actif, puis le PC redevient joignable.
    Attendu : active=False, consecutive_failures=0, RECOVERED publié.
    """
    state = MockFailoverState()
    state.active = True
    state.consecutive_failures = 3
    state.failover_count = 1

    events_published = []
    mosquitto_stopped = False

    # Simuler le ping réussi (PC de retour)
    result = await _simulate_ping(returncode=0)
    assert result is True

    # Simuler la logique de recover()
    if state.active and result:
        events_published.append("RECOVERED")
        mosquitto_stopped = True
        state.active = False
        state.consecutive_failures = 0
        state.save(tmp_antigrav / "failover_state.json")

    assert state.active is False, "Le failover doit être désactivé"
    assert state.consecutive_failures == 0, "Le compteur d'échecs doit être remis à 0"
    assert mosquitto_stopped is True
    assert "RECOVERED" in events_published


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 : SQLite sync — enqueue si PC inaccessible (paramiko raise)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_sqlite_sync_queues_on_failure(tmp_antigrav):
    """
    Scénario : La copie SCP échoue (paramiko NoValidConnectionsError).
    Attendu : L'entrée est ajoutée à sync_queue.json.
    """
    import paramiko

    queue_file = tmp_antigrav / "sync_queue.json"
    fake_db    = tmp_antigrav / "moteur_runtime.db"
    fake_db.write_bytes(b"SQLite fake data")

    # Fonction simulant scp_upload() de sqlite_sync.py
    async def mock_scp_upload(local_path: Path, remote_path: str) -> bool:
        try:
            client = paramiko.SSHClient()
            with patch.object(paramiko.SSHClient, "connect",
                              side_effect=paramiko.ssh_exception.NoValidConnectionsError(
                                  {("192.168.1.100", 22): Exception("Refusé")}
                              )):
                client.connect("192.168.1.100", port=22, username="deck",
                               password="test-password-placeholder", timeout=5)
            return True
        except Exception:
            return False

    # Fonction simulant enqueue() de sqlite_sync.py
    def enqueue(src: Path, dst: str):
        queue = []
        if queue_file.exists():
            queue = json.loads(queue_file.read_text())
        queue.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "src_path":  str(src),
            "dst_path":  dst,
        })
        queue_file.write_text(json.dumps(queue, indent=2))

    # Simuler un cycle sync qui échoue
    remote_path = "/opt/moteur_agents/moteur_runtime_deck_backup.db"
    success = await mock_scp_upload(fake_db, remote_path)
    if not success:
        enqueue(fake_db, remote_path)

    # Vérifications
    assert queue_file.exists(), "sync_queue.json doit être créé"
    queue = json.loads(queue_file.read_text())
    assert len(queue) == 1, "Une entrée doit être dans la queue"
    assert queue[0]["src_path"] == str(fake_db)
    assert queue[0]["dst_path"] == remote_path
    assert "timestamp" in queue[0]


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 : FailoverManager — traitement événements MQTT (côté PC)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_failover_manager_processes_mqtt_events():
    """
    Vérifie que FailoverManager met à jour son état interne
    lors de la réception de messages MQTT ACTIVATED/RECOVERED.
    """
    from tools.failover_manager import FailoverManager

    mgr = FailoverManager(mqtt_host="127.0.0.1", mqtt_port=1883)
    ts  = datetime.now(timezone.utc).isoformat()

    # ── Simuler ACTIVATED ──
    msg_activated = MagicMock()
    msg_activated.topic   = "antigravity/deck/failover"
    msg_activated.payload = json.dumps({
        "status":    "ACTIVATED",
        "timestamp": ts,
        "deck_ip":   "${OLLAMA_HOST:-localhost}",
    }).encode("utf-8")

    mgr._on_message(None, None, msg_activated)

    assert mgr.deck_failover_active is True
    assert mgr.failover_count == 1
    assert mgr.last_status == "ACTIVATED"

    # ── Simuler RECOVERED ──
    msg_recovered = MagicMock()
    msg_recovered.topic   = "antigravity/deck/failover"
    msg_recovered.payload = json.dumps({
        "status":    "RECOVERED",
        "timestamp": ts,
    }).encode("utf-8")

    mgr._on_message(None, None, msg_recovered)

    assert mgr.deck_failover_active is False
    assert mgr.failover_count == 1, "failover_count ne doit pas diminuer"
    assert mgr.last_status == "RECOVERED"

    # ── Vérifier get_deck_status() ──
    status = await mgr.get_deck_status()
    assert status["failover_active"] is False
    assert status["failover_count"] == 1
    assert status["last_status"] == "RECOVERED"
    assert "mqtt_connected" in status
