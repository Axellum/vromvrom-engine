"""Script d'analyse temporaire — consommation token_usage réelle."""
import sqlite3
import os

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "moteur_runtime.db")

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# 1. Total par modèle
rows = conn.execute("""
    SELECT model, COUNT(*) as appels, SUM(prompt_tokens) as prompt,
           SUM(completion_tokens) as completion, SUM(total_tokens) as total,
           SUM(cost_usd) as cout
    FROM token_usage
    GROUP BY model
    ORDER BY cout DESC
""").fetchall()
print("=== CONSOMMATION PAR MODÈLE ===")
for r in rows:
    print(f"{r['model']}: {r['appels']} appels, {r['prompt']} prompt, "
          f"{r['completion']} completion, {r['total']} total, {r['cout']:.6f} USD")

# 2. Total global
row = conn.execute(
    "SELECT COALESCE(SUM(total_tokens),0) as total, COALESCE(SUM(cost_usd),0) as cout FROM token_usage"
).fetchone()
print(f"\nTOTAL GLOBAL: {row['total']} tokens, {row['cout']:.6f} USD")

# 3. Par agent_name
print("\n=== PAR AGENT ===")
rows = conn.execute("""
    SELECT agent_name, COUNT(*) as appels, SUM(total_tokens) as total, SUM(cost_usd) as cout
    FROM token_usage
    GROUP BY agent_name
    ORDER BY cout DESC
""").fetchall()
for r in rows:
    print(f"{r['agent_name']}: {r['appels']} appels, {r['total']} tokens, {r['cout']:.6f} USD")

# 4. Par canal
print("\n=== PAR CANAL ===")
rows = conn.execute("""
    SELECT channel, COUNT(*) as appels, SUM(total_tokens) as total, SUM(cost_usd) as cout
    FROM token_usage
    GROUP BY channel
    ORDER BY cout DESC
""").fetchall()
for r in rows:
    print(f"{r['channel']}: {r['appels']} appels, {r['total']} tokens, {r['cout']:.6f} USD")

# 5. Derniers 30 jours par modèle
print("\n=== 30 DERNIERS JOURS PAR MODÈLE ===")
import time
depuis = time.time() - 30 * 86400
rows = conn.execute("""
    SELECT model, COUNT(*) as appels, SUM(total_tokens) as total, SUM(cost_usd) as cout
    FROM token_usage
    WHERE timestamp >= ?
    GROUP BY model
    ORDER BY cout DESC
""", (depuis,)).fetchall()
for r in rows:
    print(f"{r['model']}: {r['appels']} appels, {r['total']} tokens, {r['cout']:.6f} USD")

# 6. Recherche spécifique Cerebras / gpt-oss-120b
print("\n=== RECHERCHE gpt-oss-120b / CEREBRAS ===")
rows = conn.execute("""
    SELECT model, COUNT(*) as appels, SUM(total_tokens) as total, SUM(cost_usd) as cout,
           MIN(timestamp) as premier, MAX(timestamp) as dernier
    FROM token_usage
    WHERE model LIKE '%gpt-oss%' OR model LIKE '%cerebras%' OR model LIKE '%gemma-4-31b%' OR model LIKE '%zai-glm%'
    GROUP BY model
""").fetchall()
if rows:
    for r in rows:
        print(f"{r['model']}: {r['appels']} appels, {r['total']} tokens, {r['cout']:.6f} USD, "
              f"premier={r['premier']}, dernier={r['dernier']}")
else:
    print("AUCUNE trace de gpt-oss-120b / Cerebras dans token_usage")

# 7. Par source_type si disponible
print("\n=== COLONNES DE token_usage ===")
cols = conn.execute("PRAGMA table_info(token_usage)").fetchall()
for c in cols:
    print(f"  {c['name']} ({c['type']})")

conn.close()
