"""
tools/verify_facts_freshness.py — Boucle de fraîcheur des faits vérifiés (P1/#7).

Re-vérifie périodiquement les faits clés de `contexte_ia/01_Core/faits_verifies_meta.md`
contre la VÉRITÉ TERRAIN (code, config, models_registry.db). Signale toute DÉRIVE
(un fait devenu faux parce que le code a évolué). À lancer ex. tous les trimestres,
ou après un gros refactor.

Sortie : OK ✅ / DÉRIVE ⚠️ par fait, + code retour non nul s'il y a dérive.

Usage :
  python tools/verify_facts_freshness.py
"""
from __future__ import annotations
import asyncio, os, re, sqlite3, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))          # moteur_agents/
WS = os.path.dirname(ROOT)                                                   # workspace racine

# Add ROOT to sys.path to allow importing from core
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    from core.tab5_pusher import push_notification
except ImportError:
    push_notification = None


def _exists(rel_to_ws: str) -> bool:
    return os.path.exists(os.path.join(WS, rel_to_ws))


def _grep(pattern: str, *subdirs: str) -> bool:
    """Vrai si `pattern` trouvé dans les sous-dossiers (du moteur)."""
    for sd in subdirs:
        base = os.path.join(ROOT, sd)
        if not os.path.isdir(base):
            continue
        for r, _, files in os.walk(base):
            for f in files:
                if f.endswith((".py",)):
                    try:
                        if pattern in open(os.path.join(r, f), encoding="utf-8", errors="ignore").read():
                            return True
                    except Exception:
                        pass
    return False


def _db_count(table: str) -> int:
    try:
        c = sqlite3.connect(f"file:{os.path.join(ROOT,'models_registry.db')}?mode=ro", uri=True)
        n = c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]; c.close(); return n
    except Exception:
        return -1


def main() -> int:
    checks = []  # (libellé, condition_ok, détail_si_dérive)

    # Existence des composants clés
    for rel, label in [
        ("00ProjetTab", "00ProjetTab (source Tab5)"),
        ("contexte_ia/01_Core", "contexte_ia 3-layers"),
        ("moteur_agents/core/llm_gateway.py", "core/llm_gateway.py"),
        ("moteur_agents/gui_server.py", "gui_server.py"),
        ("moteur_agents/models_registry.db", "models_registry.db"),
        ("moteur_agents/moteur_runtime.db", "moteur_runtime.db"),
        ("Archives/EcranTab5_Legacy", "Archives/EcranTab5_Legacy"),
    ]:
        checks.append((f"existe: {label}", _exists(rel), f"ABSENT: {rel}"))

    # Symboles de code (agents, RAG)
    for sym, where in [("class LLMGateway", ("core",)), ("PlannerAgent", ("core", "agents")),
                       ("ExecutorAgent", ("core", "agents")), ("ReviewerAgent", ("core", "agents")),
                       ("ChromaDB", ("core", "memory")), ("ToolRegistry", ("core",))]:
        checks.append((f"symbole: {sym}", _grep(sym, *where), f"INTROUVABLE: {sym}"))

    # Tab5 = ESP32-P4 (pas S3) dans la config matérielle
    p4 = False
    for r, _, files in os.walk(os.path.join(WS, "00ProjetTab")):
        for f in files:
            if f.endswith((".yaml", ".yml")):
                try:
                    if "esp32-p4" in open(os.path.join(r, f), encoding="utf-8", errors="ignore").read().lower():
                        p4 = True
                except Exception:
                    pass
    checks.append(("Tab5 = ESP32-P4 (config réelle)", p4, "esp32-p4 introuvable dans 00ProjetTab/*.yaml"))

    # Compteurs (signalent une dérive du doc si changés)
    n_models = _db_count("models")
    n_prov = _db_count("providers")
    checks.append(("models_registry peuplé", n_models > 0, f"models={n_models}"))

    # Rapport
    drift = 0
    print(f"== Fraîcheur des faits vérifiés ==  (models={n_models}, providers={n_prov})\n")
    for label, ok, detail in checks:
        print(f"  {'✅' if ok else '⚠️ DÉRIVE'}  {label}" + ("" if ok else f"  → {detail}"))
        if not ok:
            drift += 1

    print()
    if drift:
        print(f"⚠️ {drift} dérive(s) détectée(s) → mettre à jour contexte_ia/01_Core/faits_verifies_meta.md")
        if push_notification:
            try:
                asyncio.run(push_notification(
                    title="⚠️ DÉRIVE FAITS (RAG)",
                    body=f"{drift} fait(s) clé(s) ont dérivé de la base de code. Mettre à jour faits_verifies_meta.md.",
                    color="red"
                ))
            except Exception as e:
                print(f"Erreur lors de la notification Tab5: {e}")
    else:
        print("✅ Aucun fait clé n'a dérivé.")
    print("Rappel : régénérer le catalogue via `python scratch/export_models_docs.py` si la DB a changé.")
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
