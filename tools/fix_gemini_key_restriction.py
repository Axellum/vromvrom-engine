# -*- coding: utf-8 -*-
"""
Répare la clé API "Gemini API Key" du projet gen-lang-client-0619520185 :
ajoute `generativelanguage.googleapis.com` à ses restrictions d'API
(actuellement absente → d'où le 403 de GEMINI_PAYANT_API_KEY).

N'écrase PAS les autres APIs autorisées : il les relit et ajoute seulement la manquante.
DRY-RUN par défaut. Écriture réelle avec --apply.

Usage :
  python tools/fix_gemini_key_restriction.py            # dry-run
  python tools/fix_gemini_key_restriction.py --apply     # applique
"""
import json, sys, requests

APPLY = "--apply" in sys.argv
PID = "gen-lang-client-0619520185"
GEMINI_API = "generativelanguage.googleapis.com"
# Clés à réparer (par displayName). Ajoute "Clé API 2" ici si besoin.
TARGET_KEY_NAMES = {"Gemini API Key"}

def refresh(cid, cs, rt):
    return requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": cid, "client_secret": cs,
        "refresh_token": rt, "grant_type": "refresh_token"}, timeout=20).json()["access_token"]

dv = json.load(open("google_token_vertex.json", encoding="utf-8"))
AT = refresh(dv["client_id"], dv["client_secret"], dv["refresh_token"])
H = {"Authorization": f"Bearer {AT}", "x-goog-user-project": PID, "Content-Type": "application/json"}

keys = requests.get(f"https://apikeys.googleapis.com/v2/projects/{PID}/locations/global/keys",
                    headers=H, timeout=25).json().get("keys", [])

plan = []
for k in keys:
    if k.get("displayName") not in TARGET_KEY_NAMES:
        continue
    restr = k.get("restrictions", {})
    targets = restr.get("apiTargets", [])
    services = [t.get("service") for t in targets]
    if GEMINI_API in services:
        print(f"• {k['displayName']} : Generative Language déjà autorisée — rien à faire.")
        continue
    new_targets = targets + [{"service": GEMINI_API}]
    plan.append((k["name"], k["displayName"], services, new_targets, dict(restr, apiTargets=new_targets)))

print(f"\n=== PLAN ({len(plan)} clé(s) à corriger) ===")
for name, disp, services, new_targets, _ in plan:
    print(f"  {disp}: +{GEMINI_API}")
    print(f"     avant ({len(services)} APIs): {services}")
    print(f"     après ({len(new_targets)} APIs): +Generative Language API")

if not plan:
    print("\nRien à corriger."); sys.exit(0)
if not APPLY:
    print("\n[DRY-RUN] Aucune écriture. Relancer avec --apply pour appliquer.")
    sys.exit(0)

print("\n=== APPLICATION ===")
for name, disp, services, new_targets, new_restr in plan:
    r = requests.patch(f"https://apikeys.googleapis.com/v2/{name}", headers=H,
                       params={"updateMask": "restrictions"},
                       json={"restrictions": new_restr}, timeout=30)
    if r.status_code in (200, 201):
        print(f"  ✅ {disp} : Generative Language API ajoutée (opération lancée).")
    else:
        try: msg = r.json().get("error", {}).get("message", "")
        except: msg = r.text
        print(f"  ❌ {disp} : HTTP {r.status_code} {msg[:140]}")
print("\nNote : la propagation peut prendre ~1-2 min. Retester GEMINI_PAYANT_API_KEY ensuite.")
