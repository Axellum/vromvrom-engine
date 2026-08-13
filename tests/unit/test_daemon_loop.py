"""
tests/unit/test_daemon_loop.py — Tests du Démon Sentinelle (#T266).

Vérifie que :
- une dépendance absente (paramiko) rend le contrôle HA « indisponible »
  (statut "skipped") au lieu d'une fausse anomalie de surcharge ;
- un échec SSH réel reste une anomalie (le daemon ne devient pas aveugle) ;
- la cause de l'échec du chemin par clé est visible dans le rapport ;
- la même indisponibilité n'est journalisée qu'une seule fois.
"""

import asyncio
import builtins
import logging
import sys
import types

import pytest

from core import daemon_loop as dl

# ──────────────────────────────────────────────────────────────────
# Mocks partagés
# ──────────────────────────────────────────────────────────────────

class _ProcessFake:
    """Simule asyncio.subprocess.Process pour le chemin ssh par clé."""

    def __init__(self, returncode, stdout=b"", stderr=b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return self._stdout, self._stderr


class _FakeParamikoClient:
    """SSHClient qui échoue à la connexion (échec SSH ordinaire)."""

    def set_missing_host_key_policy(self, policy):
        pass

    def connect(self, **kwargs):
        raise ConnectionRefusedError("Connexion refusée par 192.168.1.x")


class _FakeChannel:
    def recv_exit_status(self):
        return 0


class _FakeParamikoOkClient:
    """SSHClient qui réussit : le fallback Paramiko fonctionne."""

    def set_missing_host_key_policy(self, policy):
        pass

    def connect(self, **kwargs):
        pass

    def exec_command(self, command):
        stdout = types.SimpleNamespace(
            read=lambda: b"ha_docker:2.0%", channel=_FakeChannel()
        )
        stderr = types.SimpleNamespace(read=lambda: b"")
        return None, stdout, stderr


FAUX_PARAMIKO_ECHEC = types.SimpleNamespace(
    SSHClient=_FakeParamikoClient, AutoAddPolicy=types.SimpleNamespace
)
FAUX_PARAMIKO_SUCCES = types.SimpleNamespace(
    SSHClient=_FakeParamikoOkClient, AutoAddPolicy=types.SimpleNamespace
)


@pytest.fixture(autouse=True)
def _daemon_propre(tmp_path, monkeypatch):
    """État global du daemon remis à zéro ; contexte JSON isolé en tmp."""
    dl.daemon_state["last_ha_overload_check_at"] = None
    dl.daemon_state["consecutive_high_cpu_ticks"] = 0
    dl._last_ssh_error_reported = None
    monkeypatch.setattr(dl, "_DAEMON_CONTEXT_FILE", str(tmp_path / "daemon_context.json"))


def _mock_autres_checks(monkeypatch):
    """Remplace les checks annexes par des succès silencieux (pas d'I/O réelle)."""

    def _ok(check):
        return {"check": check, "status": "ok", "details": {}}

    async def _ok_coro(check):
        return _ok(check)

    async def _cpu_bas(_entity_id=None):
        return {"state": "12.5"}

    monkeypatch.setattr(dl, "_check_git_status", lambda: _ok("git_status"))
    monkeypatch.setattr(dl, "_check_ha_health", lambda: _ok_coro("ha_health"))
    monkeypatch.setattr(dl, "_prefetch_calendar", lambda: _ok_coro("calendar_prefetch"))
    monkeypatch.setattr(dl, "_check_memory_health", lambda: _ok("memory_health"))
    monkeypatch.setattr(dl, "_get_ha_state", _cpu_bas)


def _dependance_absente(monkeypatch):
    """Binaire ssh absent + import paramiko en ModuleNotFoundError (scénario prod)."""

    async def _ssh_binaire_absent(*args, **kwargs):
        raise FileNotFoundError("[Errno 2] No such file or directory: 'ssh'")

    real_import = builtins.__import__

    def _sans_paramiko(name, *args, **kwargs):
        if name == "paramiko":
            raise ModuleNotFoundError("No module named 'paramiko'", name="paramiko")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _ssh_binaire_absent)
    monkeypatch.setattr(builtins, "__import__", _sans_paramiko)


def _cle_refusee_et_paramiko_ko(monkeypatch):
    """Clé SSH refusée puis connexion Paramiko refusée : panne réelle."""

    async def _ssh_cle_refusee(*args, **kwargs):
        return _ProcessFake(1, b"", b"Permission denied (publickey).")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _ssh_cle_refusee)
    monkeypatch.setitem(sys.modules, "paramiko", FAUX_PARAMIKO_ECHEC)


def _lancer_cycle():
    return asyncio.run(dl.run_tick_cycle({}))


# ──────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────

def test_dependance_absente_controle_indisponible_pas_anomalie(monkeypatch):
    """[#T266] Paramiko absent : le contrôle est « indisponible », PAS une anomalie.

    Le rapport doit le dire explicitement et exposer la cause de l'échec du
    chemin par clé (défaut b : le message visible désignait la mauvaise cause).
    """
    _mock_autres_checks(monkeypatch)
    _dependance_absente(monkeypatch)

    report = _lancer_cycle()
    check = report["checks"]["ha_overload"]

    assert check["status"] == "skipped"
    assert check["status"] not in ("warning", "error")
    reason = check["details"]["reason"].lower()
    assert "contrôle indisponible" in reason
    assert "paramiko" in reason
    # La vraie cause du chemin primaire est visible (défaut b corrigé)
    assert "ssh" in reason

    assert report["anomalies_count"] == 0
    assert all(a["check"] != "ha_overload" for a in report["anomalies"])


def test_echec_ssh_ordinaire_reste_anomalie(monkeypatch):
    """[#T266] Panne SSH réelle : l'anomalie doit rester visible.

    C'est le test le plus important : la PR ne doit pas rendre le daemon
    aveugle aux vraies pannes.
    """
    _mock_autres_checks(monkeypatch)
    _cle_refusee_et_paramiko_ko(monkeypatch)

    report = _lancer_cycle()
    check = report["checks"]["ha_overload"]

    assert check["status"] == "error"
    assert report["anomalies_count"] == 1
    assert report["anomalies"][0]["check"] == "ha_overload"
    # La cause de l'échec du chemin par clé est visible dans le message
    assert "Permission denied" in check["details"]["error"]


def test_deux_cycles_dependance_absente_une_seule_journalisation(monkeypatch, caplog):
    """[#T266] Deux cycles consécutifs sans la dépendance : un seul warning."""
    _mock_autres_checks(monkeypatch)
    _dependance_absente(monkeypatch)
    caplog.set_level(logging.WARNING)

    _lancer_cycle()
    # On force le check SSH au second cycle (sinon le quota 6h ne le rejoue pas)
    dl.daemon_state["last_ha_overload_check_at"] = None
    _lancer_cycle()

    ssh_warnings = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and "DAEMON-SSH" in r.getMessage()
    ]
    assert len(ssh_warnings) == 1


def test_succes_ssh_par_cle_statut_ok(monkeypatch, caplog):
    """Cas nominal : le chemin par clé réussit, aucun warning SSH émis."""
    _mock_autres_checks(monkeypatch)

    async def _ssh_ok(*args, **kwargs):
        return _ProcessFake(0, b"addon_core_samba:3.1%\nnginx:1.2%", b"")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _ssh_ok)
    caplog.set_level(logging.WARNING)

    report = _lancer_cycle()
    check = report["checks"]["ha_overload"]

    assert check["status"] == "ok"
    assert check["details"]["containers_cpu"] == {"addon_core_samba": 3.1, "nginx": 1.2}
    assert report["anomalies_count"] == 0
    assert not [r for r in caplog.records if "DAEMON-SSH" in r.getMessage()]


def test_fallback_paramiko_apres_echec_cle_reste_ok(monkeypatch, caplog):
    """Clé refusée mais Paramiko opérationnel : pas d'anomalie, signature réarmée."""
    _mock_autres_checks(monkeypatch)

    async def _ssh_cle_refusee(*args, **kwargs):
        return _ProcessFake(1, b"", b"Permission denied (publickey).")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _ssh_cle_refusee)
    monkeypatch.setitem(sys.modules, "paramiko", FAUX_PARAMIKO_SUCCES)
    caplog.set_level(logging.WARNING)

    report = _lancer_cycle()
    check = report["checks"]["ha_overload"]

    assert check["status"] == "ok"
    assert check["details"]["containers_cpu"] == {"ha_docker": 2.0}
    assert report["anomalies_count"] == 0
    assert dl._last_ssh_error_reported is None
    assert not [r for r in caplog.records if "DAEMON-SSH" in r.getMessage()]
