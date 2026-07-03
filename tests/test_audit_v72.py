"""
test_audit_v72.py — Test d'intégration des 19 outils Phase 2/3 + validation ToolRegistry.

Vérifie :
1. OAuth2 est opérationnel (GCPOAuthClient)
2. Les outils Workspace fonctionnent (Calendar, Drive, Gmail, Sheets, Tasks, YouTube, Contacts)
3. Les outils Cloud API fonctionnent (TTS, Translation, Vision, STT)
4. Imagen 4 est accessible
5. KeyPool est fonctionnel
"""

import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')

from dotenv import load_dotenv
load_dotenv()

import os

results = {}

# ─── 1. Test OAuth2 ──────────────────────────────────────────────────
print("=" * 60)
print("1. TEST OAUTH2 GCP CLIENT")
print("=" * 60)
try:
    from core.gcp_oauth_client import get_gcp_client
    client = get_gcp_client()
    print(f"  OAuth2 disponible: {client.available}")
    results["oauth2"] = "OK" if client.available else "INDISPONIBLE"
except Exception as e:
    print(f"  ERREUR: {e}")
    results["oauth2"] = f"ERREUR: {e}"

# ─── 2. Tests Workspace Phase 1 (Calendar + Drive) ───────────────────
print("\n" + "=" * 60)
print("2. TEST WORKSPACE PHASE 1 (Calendar + Drive)")
print("=" * 60)

from tools.google_workspace import list_calendars, get_calendar_events, list_drive_files

# Calendar
try:
    r = list_calendars()
    print(f"  list_calendars: {r[:100]}...")
    results["list_calendars"] = "OK" if "calendrier" in r.lower() or "error" not in r.lower() else f"FAIL: {r[:60]}"
except Exception as e:
    print(f"  list_calendars ERREUR: {e}")
    results["list_calendars"] = f"ERREUR: {e}"

try:
    r = get_calendar_events("primary", "3")
    print(f"  get_calendar_events: {r[:100]}...")
    results["get_calendar_events"] = "OK" if "erreur" not in r.lower() else f"FAIL: {r[:60]}"
except Exception as e:
    print(f"  get_calendar_events ERREUR: {e}")
    results["get_calendar_events"] = f"ERREUR: {e}"

# Drive
try:
    r = list_drive_files("5")
    print(f"  list_drive_files: {r[:100]}...")
    results["list_drive_files"] = "OK" if "erreur" not in r.lower() else f"FAIL: {r[:60]}"
except Exception as e:
    print(f"  list_drive_files ERREUR: {e}")
    results["list_drive_files"] = f"ERREUR: {e}"

# ─── 3. Tests Workspace Phase 2 (Gmail, Sheets, Tasks, YouTube, Contacts)
print("\n" + "=" * 60)
print("3. TEST WORKSPACE PHASE 2")
print("=" * 60)

from tools.google_workspace import search_gmail, get_tasks, search_youtube, get_contacts

# Gmail
try:
    r = search_gmail("is:unread", "2")
    print(f"  search_gmail: {r[:100]}...")
    results["search_gmail"] = "OK" if "erreur" not in r.lower() else f"WARN: {r[:60]}"
except Exception as e:
    print(f"  search_gmail ERREUR: {e}")
    results["search_gmail"] = f"ERREUR: {e}"

# Tasks
try:
    r = get_tasks("@default")
    print(f"  get_tasks: {r[:100]}...")
    results["get_tasks"] = "OK" if "erreur" not in r.lower() else f"WARN: {r[:60]}"
except Exception as e:
    print(f"  get_tasks ERREUR: {e}")
    results["get_tasks"] = f"ERREUR: {e}"

# YouTube
try:
    r = search_youtube("home assistant esphome", "2")
    print(f"  search_youtube: {r[:120]}...")
    results["search_youtube"] = "OK" if "erreur" not in r.lower() else f"WARN: {r[:60]}"
except Exception as e:
    print(f"  search_youtube ERREUR: {e}")
    results["search_youtube"] = f"ERREUR: {e}"

# Contacts
try:
    r = get_contacts("3")
    print(f"  get_contacts: {r[:100]}...")
    results["get_contacts"] = "OK" if "erreur" not in r.lower() else f"WARN: {r[:60]}"
except Exception as e:
    print(f"  get_contacts ERREUR: {e}")
    results["get_contacts"] = f"ERREUR: {e}"

# ─── 4. Tests Cloud APIs Phase 3 ─────────────────────────────────────
print("\n" + "=" * 60)
print("4. TEST CLOUD APIs PHASE 3")
print("=" * 60)

# Cloud Translation
from tools.cloud_translate import translate_text
try:
    r = translate_text("Hello, how are you?", "fr")
    print(f"  translate_text: {r[:100]}...")
    results["translate_text"] = "OK" if "bonjour" in r.lower() or "traduction" in r.lower() else f"FAIL: {r[:60]}"
except Exception as e:
    print(f"  translate_text ERREUR: {e}")
    results["translate_text"] = f"ERREUR: {e}"

# Cloud TTS
from tools.cloud_tts import cloud_tts_synthesize
try:
    r = cloud_tts_synthesize("Bonjour, test rapide.", "neural2_female", "fr-FR")
    print(f"  cloud_tts: {r[:100]}...")
    results["cloud_tts"] = "OK" if "audio" in r.lower() or ".mp3" in r.lower() else f"FAIL: {r[:60]}"
except Exception as e:
    print(f"  cloud_tts ERREUR: {e}")
    results["cloud_tts"] = f"ERREUR: {e}"

# Cloud Vision (test sur une image existante)
from tools.cloud_vision import analyze_image
try:
    test_img = os.path.join(os.path.dirname(os.path.abspath(__file__)), "images", "imagen_1779811763.png")
    if os.path.exists(test_img):
        r = analyze_image(test_img)
        print(f"  analyze_image: {r[:120]}...")
        results["analyze_image"] = "OK" if "analyse" in r.lower() or "labels" in r.lower() else f"FAIL: {r[:60]}"
    else:
        print(f"  analyze_image: SKIP (pas d'image de test)")
        results["analyze_image"] = "SKIP"
except Exception as e:
    print(f"  analyze_image ERREUR: {e}")
    results["analyze_image"] = f"ERREUR: {e}"

# Cloud STT (test avec le WAV existant)
from tools.cloud_stt import transcribe_audio
try:
    test_wav = os.path.join(os.path.dirname(os.path.abspath(__file__)), "images", "test_tts.wav")
    if os.path.exists(test_wav):
        r = transcribe_audio(test_wav, "fr-FR")
        print(f"  transcribe_audio: {r[:100]}...")
        results["transcribe_audio"] = "OK" if "erreur" not in r.lower() or "transcription" in r.lower() else f"WARN: {r[:60]}"
    else:
        print(f"  transcribe_audio: SKIP (pas de fichier audio)")
        results["transcribe_audio"] = "SKIP"
except Exception as e:
    print(f"  transcribe_audio ERREUR: {e}")
    results["transcribe_audio"] = f"ERREUR: {e}"

# ─── 5. Test KeyPool ──────────────────────────────────────────────────
print("\n" + "=" * 60)
print("5. TEST KEYPOOL")
print("=" * 60)

from core.key_pool import GeminiKeyPool
try:
    pool = GeminiKeyPool()
    key = pool.get_key()
    stats = pool.get_stats()
    print(f"  KeyPool: {stats['total_keys']} cles, active={key[:12]}...")
    results["key_pool"] = f"OK ({stats['total_keys']} cles)"
except Exception as e:
    print(f"  KeyPool ERREUR: {e}")
    results["key_pool"] = f"ERREUR: {e}"

# ─── 6. Test Factory (22 outils) ─────────────────────────────────────
print("\n" + "=" * 60)
print("6. TEST FACTORY (INTEGRATION)")
print("=" * 60)

from core.factory import create_engine
try:
    engine, router, config = create_engine("test_audit_v72", register_git_tools=True)
    tools = sorted(engine.agents["executor"].tool_registry._tools.keys())
    agents = sorted(engine.agents.keys())
    print(f"  Factory: {len(tools)} outils, {len(agents)} agents")
    print(f"  Agents: {', '.join(agents)}")
    print(f"  Outils: {', '.join(tools)}")
    results["factory"] = f"OK ({len(tools)} outils, {len(agents)} agents)"
except Exception as e:
    print(f"  Factory ERREUR: {e}")
    results["factory"] = f"ERREUR: {e}"

# ─── BILAN ────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("BILAN AUDIT V7.2")
print("=" * 60)

ok_count = sum(1 for v in results.values() if v.startswith("OK"))
warn_count = sum(1 for v in results.values() if v.startswith("WARN"))
fail_count = sum(1 for v in results.values() if v.startswith("FAIL") or v.startswith("ERREUR"))
skip_count = sum(1 for v in results.values() if v.startswith("SKIP"))
total = len(results)

for name, status in results.items():
    icon = "OK" if status.startswith("OK") else ("!!" if status.startswith("WARN") else ("XX" if status.startswith(("FAIL", "ERREUR")) else "--"))
    print(f"  [{icon}] {name}: {status}")

print(f"\n  TOTAL: {ok_count} OK, {warn_count} WARN, {skip_count} SKIP, {fail_count} FAIL sur {total} tests")
