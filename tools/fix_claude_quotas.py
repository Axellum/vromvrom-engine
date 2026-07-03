# -*- coding: utf-8 -*-
"""
Corrige et organise les demandes de quota Claude sur Vertex (projet ha-delta).

Problème corrigé : les `quotaPreferences` token/min sont demandées à 100 (ou 10),
ce qui rend Claude inutilisable. On remonte Input/Output tokens à des valeurs réelles,
on cale les requêtes/min, sur les modèles réellement utiles uniquement.

Sécurité : DRY-RUN par défaut (validateOnly). Écriture réelle seulement avec --apply.

Usage :
  python tools/fix_claude_quotas.py            # dry-run (n'écrit rien)
  python tools/fix_claude_quotas.py --apply     # applique les corrections
"""
import json, sys, requests

APPLY = "--apply" in sys.argv
PROJECT = "ha-delta"
CONTACT = "axxxums@gmail.com"

# Modèles à garder + valeurs cibles (par minute, par base model)
TARGET_MODELS = {
    "anthropic-claude-opus-4-8":   {"req": 60,  "in": 200000, "out": 50000},  # à CRÉER (jamais demandé)
    "anthropic-claude-sonnet-4-6": {"req": 100, "in": 400000, "out": 80000},
    "anthropic-claude-haiku-4-5":  {"req": 100, "in": 400000, "out": 80000},
    "anthropic-claude-fable":      {"req": 60,  "in": 200000, "out": 50000},
}
# Régions visées (le moteur tourne EU ; global = routage dynamique)
KEEP_REGIONS = ("Eu", "Global")  # préfixe du quotaId

def refresh(cid, cs, rt):
    return requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": cid, "client_secret": cs,
        "refresh_token": rt, "grant_type": "refresh_token"}, timeout=20).json()["access_token"]

dv = json.load(open("google_token_vertex.json", encoding="utf-8"))
AT = refresh(dv["client_id"], dv["client_secret"], dv["refresh_token"])
H = {"Authorization": f"Bearer {AT}", "x-goog-user-project": PROJECT, "Content-Type": "application/json"}

# Mapping (région, type) -> quotaId
QUOTA_IDS = {
    ("Eu", "req"):     "EuOnlinePredictionRequestsPerMinPerProjectPerBaseModel",
    ("Eu", "in"):      "EuOnlinePredictionInputTokensPerMinutePerBaseModel",
    ("Eu", "out"):     "EuOnlinePredictionOutputTokensPerMinutePerBaseModel",
    ("Global", "req"): "GlobalOnlinePredictionRequestsPerMinutePerProjectPerBaseModel",
    ("Global", "in"):  "GlobalOnlinePredictionInputTokensPerMinutePerBaseModel",
    ("Global", "out"): "GlobalOnlinePredictionOutputTokensPerMinutePerBaseModel",
}

def kind(quota_id):
    if "RequestsPerMin" in quota_id: return "req"
    if "InputTokens" in quota_id:    return "in"
    if "OutputTokens" in quota_id:   return "out"
    return None

# 1) Indexer les quotaPreferences existantes par (quotaId, base_model)
url = f"https://cloudquotas.googleapis.com/v1/projects/{PROJECT}/locations/global/quotaPreferences"
qps = requests.get(url, headers=H, params={"pageSize": 100}, timeout=25).json().get("quotaPreferences", [])
existing = {}
for q in qps:
    bm = q.get("dimensions", {}).get("base_model", "")
    existing[(q.get("quotaId", ""), bm)] = q
print(f"{len(qps)} quotaPreferences existantes.\n")

# 2) Construire le plan : PATCH si existe et diffère, CREATE si manquant
patches, creates = [], []
for bm, vals in TARGET_MODELS.items():
    for region in KEEP_REGIONS:
        for k in ("req", "in", "out"):
            qid = QUOTA_IDS[(region, k)]
            target = vals[k]
            q = existing.get((qid, bm))
            if q is None:
                creates.append((qid, bm, target))
            else:
                cur = q.get("quotaConfig", {}).get("preferredValue", "?")
                if str(cur) != str(target):
                    patches.append((q["name"], qid, bm, cur, target))

print(f"=== PLAN : {len(patches)} PATCH + {len(creates)} CREATE ===")
for _, qid, bm, cur, target in patches:
    print(f"  PATCH  {bm:30} {qid:58} {cur} → {target}")
for qid, bm, target in creates:
    print(f"  CREATE {bm:30} {qid:58} (nouveau) → {target}")

if not patches and not creates:
    print("\nRien à faire."); sys.exit(0)

if not APPLY:
    print("\n[DRY-RUN] Aucune écriture. Relancer avec --apply pour appliquer.")
    sys.exit(0)

def err_detail(r):
    try:
        e = r.json().get("error", {})
        out = e.get("message", "")
        for d in e.get("details", []):
            for v in d.get("violations", []):
                out += f" | {v.get('type','')}: {v.get('description','')}"
            if d.get("reason"): out += f" | reason={d['reason']}"
        return out
    except Exception:
        return r.text

# Décroissances : acquittement des contrôles (liste = params répétés)
IGNORE = ["QUOTA_DECREASE_BELOW_USAGE", "QUOTA_DECREASE_PERCENTAGE_TOO_HIGH"]

print("\n=== APPLICATION ===")
ok = 0
for name, qid, bm, cur, target in patches:
    params = {"updateMask": "quotaConfig.preferredValue"}
    try:
        if int(target) < int(cur): params["ignoreSafetyChecks"] = IGNORE
    except Exception: pass
    # le corps doit être une QuotaPreference valide (service/quotaId/dimensions requis)
    body = {"service": "aiplatform.googleapis.com", "quotaId": qid,
            "dimensions": {"base_model": bm},
            "quotaConfig": {"preferredValue": str(target)}, "contactEmail": CONTACT}
    r = requests.patch(f"https://cloudquotas.googleapis.com/v1/{name}", headers=H, params=params,
                       json=body, timeout=30)
    if r.status_code in (200, 201): ok += 1; print(f"  ✅ PATCH {bm} {qid.split('OnlinePrediction')[-1]} → {target}")
    else: print(f"  ❌ PATCH {bm} {qid.split('OnlinePrediction')[-1]}: HTTP {r.status_code}\n       {err_detail(r)}")

for qid, bm, target in creates:
    pref_id = f"{bm}-{qid}".replace("anthropic-", "")[:60]
    body = {"service": "aiplatform.googleapis.com", "quotaId": qid,
            "dimensions": {"base_model": bm},
            "quotaConfig": {"preferredValue": str(target)}, "contactEmail": CONTACT}
    r = requests.post(url, headers=H, params={"quotaPreferenceId": pref_id}, json=body, timeout=30)
    if r.status_code in (200, 201): ok += 1; print(f"  ✅ CREATE {bm} {qid.split('OnlinePrediction')[-1]} → {target}")
    else: print(f"  ❌ CREATE {bm} {qid.split('OnlinePrediction')[-1]}: HTTP {r.status_code}\n       {err_detail(r)}")

print(f"\n{ok}/{len(patches)+len(creates)} opérations réussies.")
