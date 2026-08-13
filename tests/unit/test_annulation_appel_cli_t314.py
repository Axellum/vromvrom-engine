"""
test_annulation_appel_cli_t314.py — [#T314] Un appel LLM abandonné ne doit plus
continuer à facturer. Tests sur de VRAIS process, sans mocks.

Mesuré le 12/08 sur `chat_56cb9d0316` : le watchdog DAG tue la session à
`00:04:57`, la CLI Claude répond quand même à `00:05:38` — 289 966 tokens
d'entrée, **$0,6665**, pour une sortie que plus personne n'attend. C'était
**100 % du coût de la campagne** (les 7 autres demandes : $0,0000).

Cause : `ClaudeCLIProvider` n'avait pas de `generate_async` et héritait donc de
`asyncio.to_thread(self.generate, …)`. L'annulation rend la main à la coroutine
mais ne peut ni arrêter le thread, ni tuer le subprocess qu'il attend.

Le fichier témoin joue ici le rôle de la facture : écrit par le faux binaire à la
toute fin de son travail, il ne doit PAS apparaître si l'appel a été tué.
"""

import asyncio
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from core.llm.providers.base import run_cli_command, tuer_arbre_process
from core.llm.providers.deepseek import ClaudeCLIProvider, _AppelCLIAnnulable

DUREE_APPEL = 3.0      # le faux « LLM » met 3 s à répondre
DELAI_ANNULATION = 0.6  # le watchdog coupe bien avant


def _creer_faux_claude(dossier: Path, temoin: Path, duree: float = DUREE_APPEL) -> Path:
    """Faux binaire `claude` : dort, puis écrit le témoin (= la facture) et répond en JSON."""
    corps = (
        "import time, json\n"
        f"time.sleep({duree})\n"
        f"open(r'{temoin}', 'w').write('facture emise')\n"
        "print(json.dumps({'result': 'reponse tardive',"
        " 'usage': {'input_tokens': 289966, 'output_tokens': 12},"
        " 'total_cost_usd': 0.6665}))\n"
    )
    impl = dossier / "faux_claude_impl.py"
    impl.write_text(corps, encoding="utf-8")

    if sys.platform == "win32":
        lanceur = dossier / "claude.cmd"
        lanceur.write_text(f'@echo off\r\n"{sys.executable}" "{impl}" %*\r\n', encoding="utf-8")
    else:
        lanceur = dossier / "claude"
        lanceur.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{impl}" "$@"\n', encoding="utf-8")
        lanceur.chmod(lanceur.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return lanceur


@pytest.fixture
def provider_factice(tmp_path):
    """ClaudeCLIProvider pointé sur un faux binaire. Retourne (provider, témoin)."""
    temoin = tmp_path / "facture.txt"
    lanceur = _creer_faux_claude(tmp_path, temoin)
    provider = ClaudeCLIProvider()
    provider.cmd_path = str(lanceur)
    return provider, temoin


# ── Le cas mesuré ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_annulation_tue_l_appel_et_la_facture(provider_factice):
    """Tâche annulée → le process meurt avec elle : aucune facture émise."""
    provider, temoin = provider_factice

    tache = asyncio.create_task(provider.generate_async("sys", "il me reste combien ?"))
    await asyncio.sleep(DELAI_ANNULATION)
    tache.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tache

    # Bien au-delà de la durée de l'appel : s'il vivait encore, il aurait facturé
    await asyncio.sleep(DUREE_APPEL + 1.0)
    assert not temoin.exists(), (
        "Le process a survécu à l'annulation et a facturé — régression #T314"
    )


@pytest.mark.asyncio
async def test_appel_non_annule_repond_normalement(provider_factice):
    """Non-régression : sans annulation, le chemin nominal répond et facture."""
    provider, temoin = provider_factice

    reponse = await provider.generate_async("sys", "question ordinaire")

    assert "reponse tardive" in reponse
    assert temoin.exists()  # l'appel est allé à son terme, c'est voulu


@pytest.mark.asyncio
async def test_annulation_du_chemin_structure(provider_factice):
    """Le chemin structuré (Planner) est annulable de la même façon."""
    provider, temoin = provider_factice

    tache = asyncio.create_task(
        provider.generate_structured_async("sys", "plan", {"type": "object"})
    )
    await asyncio.sleep(DELAI_ANNULATION)
    tache.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tache

    await asyncio.sleep(DUREE_APPEL + 1.0)
    assert not temoin.exists()


# ── La course annulation / lancement ─────────────────────────────────────────

def test_annulation_avant_lancement_tue_quand_meme(tmp_path):
    """
    Annulation reçue AVANT que le thread ait lancé le process : le lancement qui
    suit doit se tuer lui-même, sinon le correctif serait inopérant par
    intermittence.
    """
    temoin = tmp_path / "facture_course.txt"
    lanceur = _creer_faux_claude(tmp_path, temoin, duree=2.0)

    surveillant = _AppelCLIAnnulable()
    # La coroutine annule d'abord : il n'y a encore aucun process à tuer
    assert surveillant.annuler() is False

    # ... puis le thread lance quand même le process
    proc = subprocess.Popen(
        [str(lanceur)] if sys.platform != "win32" else ["cmd", "/c", str(lanceur)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=(sys.platform != "win32"),
    )
    surveillant.publier(proc)

    proc.wait(timeout=10)
    assert not temoin.exists(), "Le process lancé après l'annulation n'a pas été tué"


# ── Garde-fous du helper ─────────────────────────────────────────────────────

def test_run_cli_command_sans_sink_inchange():
    """Sans process_sink, le comportement historique est strictement préservé."""
    res = run_cli_command(
        [sys.executable, "-c", "print('ok')"],
        capture_output=True, text=True, timeout=30,
    )
    assert res.returncode == 0
    assert "ok" in res.stdout


def test_run_cli_command_avec_sink_publie_et_repond():
    """Avec process_sink : le Popen est publié ET la sortie reste correcte."""
    vus = []
    res = run_cli_command(
        [sys.executable, "-c", "print('ok')"],
        capture_output=True, text=True, timeout=30,
        process_sink=vus.append,
    )
    assert res.returncode == 0
    assert "ok" in res.stdout
    assert len(vus) == 1 and vus[0].pid > 0


def test_tuer_arbre_process_tolere_process_mort():
    """Un process déjà terminé (ou None) ne doit ni lever ni mentir sur son retour."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=30)
    assert tuer_arbre_process(proc) is False
    assert tuer_arbre_process(None) is False
