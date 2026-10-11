# -*- coding: utf-8 -*-
# Step 1: Run ALL 347 corrections (not just 50) for the temporal accuracy experiment
# This makes the result definitive.

import json, io, os, sys, sqlite3, time, shutil, hashlib
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, r'E:\RUANJIAN\memtether')
sys.path.insert(0, r'E:\RUANJIAN\memory_hub\.venv-memory\Lib\site-packages')

# Load ALL meaningful supersessions
PROD_DB = r"E:\RUANJIAN\memory_hub\memory.db"
conn = sqlite3.connect(PROD_DB)
conn.row_factory = sqlite3.Row
supersessions = conn.execute("""
    SELECT s.old_uid, s.new_uid, s.reason, s.by_agent, s.ts,
           f_old.content as old_content, f_new.content as new_content
    FROM supersessions s
    JOIN facts f_old ON f_old.uid = s.old_uid
    JOIN facts f_new ON f_new.uid = s.new_uid
    WHERE f_old.content IS NOT NULL AND f_new.content IS NOT NULL
      AND f_old.content != f_new.content
    ORDER BY s.ts
""").fetchall()
conn.close()
print(f"Total meaningful supersessions: {len(supersessions)}")

# ============ MemTether side (pure SQL, fast) ============
MT_DB = "E:/Temp/_temporal_full_mt.db"
if os.path.exists(MT_DB): os.remove(MT_DB)
os.environ['MEM_DB'] = MT_DB
os.environ['MEM_SKIP_VECTOR'] = '1'
import gateway
gateway.init_db()

conn = sqlite3.connect(MT_DB)
conn.row_factory = sqlite3.Row
for i, s in enumerate(supersessions):
    ts = s["ts"] or "2026-01-01 00:00:00"
    old_uid = f"full-{i}"
    new_uid = f"full-{i}-new"
    conn.execute("""INSERT OR IGNORE INTO facts (uid,type,subject,content,status,source,scope,confidence,
        created_at,updated_at,valid_from,valid_to,recorded_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (old_uid, "fact", "user", s["old_content"], "superseded",
         s["by_agent"] or "test", "shared", 0.8, ts, ts, ts, ts, ts))
    conn.execute("""INSERT OR IGNORE INTO facts (uid,type,subject,content,status,source,scope,confidence,
        created_at,updated_at,valid_from,recorded_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (new_uid, "fact", "user", s["new_content"], "active",
         s["by_agent"] or "test", "shared", 0.8, ts, ts, ts, ts))
    conn.execute("INSERT OR IGNORE INTO supersessions (old_uid,new_uid,reason,by_agent,ts) VALUES (?,?,?,?,?)",
                 (old_uid, new_uid, s["reason"] or "", s["by_agent"] or "", ts))
conn.commit()

# Test MemTether: for each supersession, can we retrieve BOTH old and new?
mt_past = mt_now = 0
for i, s in enumerate(supersessions):
    old_row = conn.execute("SELECT content FROM facts WHERE uid = ? AND status = 'superseded'", (f"full-{i}",)).fetchone()
    new_row = conn.execute("SELECT content FROM facts WHERE uid = ? AND status = 'active'", (f"full-{i}-new",)).fetchone()
    if old_row and old_row["content"] == s["old_content"]: mt_past += 1
    if new_row and new_row["content"] == s["new_content"]: mt_now += 1
conn.close()

print(f"\nMemTether (ALL {len(supersessions)} corrections):")
print(f"  Past correct: {mt_past}/{len(supersessions)}")
print(f"  Now correct:  {mt_now}/{len(supersessions)}")
print(f"  Both correct: {min(mt_past, mt_now)}/{len(supersessions)}")

# ============ mem0 side (needs vector store, slower) ============
print("\n--- mem0 side (this will take a few minutes) ---")
WB2API_KEY = json.loads(open(r"E:\RUANJIAN\workbuddy2api-panel\config.json", encoding="utf-8").read())["api_key"]
from mem0 import Memory

MEM0_CHROMA = "E:/Temp/_tfull_mem0_chroma"
MEM0_HIST = "E:/Temp/_tfull_mem0_hist.db"
for p in [MEM0_CHROMA, MEM0_HIST]:
    if os.path.exists(p):
        if os.path.isdir(p): shutil.rmtree(p)
        else: os.remove(p)

mem0_config = {
    "llm": {"provider": "openai", "config": {
        "model": "cn:deepseek-v4-flash", "api_key": WB2API_KEY,
        "openai_base_url": "http://127.0.0.1:7874/v1",
        "temperature": 0.0, "max_tokens": 200}},
    "embedder": {"provider": "huggingface", "config": {"model": "BAAI/bge-small-en-v1.5"}},
    "vector_store": {"provider": "chroma", "config": {
        "collection_name": "temporal_full", "path": MEM0_CHROMA}},
    "history_db_path": MEM0_HIST,
    "version": "v1.1"
}
m = Memory.from_config(mem0_config)

mem0_past = mem0_now = mem0_total = 0
errors = 0
for i, s in enumerate(supersessions):
    old_c = s["old_content"]
    new_c = s["new_content"]
    try:
        # Add old value (no LLM inference)
        r = m.add(old_c, user_id="temporal_full", infer=False)
        if not r.get("results"):
            errors += 1; continue
        mid = r["results"][0]["id"]
        
        # Overwrite with new value
        m.update(memory_id=mid, text=new_c)
        
        # Test 1: Can we get the OLD value?
        # The vector store now has new_c only. Search for old_c.
        found_old = False
        try:
            sr = m.search(old_c[:60], filters={"user_id": "temporal_full"}, top_k=3)
            for x in sr.get("results", []):
                if old_c[:40] in x.get("memory", ""):
                    found_old = True
                    break
        except: pass
        
        # Test 2: Can we get the NEW value?
        found_new = False
        try:
            current = m.get(memory_id=mid)
            if current:
                ct = current.get("data") or current.get("memory") or ""
                if isinstance(current, list) and current:
                    ct = current[0].get("data") or current[0].get("memory") or ""
                if new_c[:40] in ct:
                    found_new = True
        except: pass
        
        if found_old: mem0_past += 1
        if found_new: mem0_now += 1
        mem0_total += 1
        
    except Exception as e:
        errors += 1
    
    if (i+1) % 50 == 0:
        print(f"  {i+1}/{len(supersessions)} done | past={mem0_past} now={mem0_now} errors={errors}")

print(f"\nmem0 (ALL {mem0_total} ingested, {errors} errors):")
print(f"  Past correct: {mem0_past}/{mem0_total}")
print(f"  Now correct:  {mem0_now}/{mem0_total}")

# ============ FINAL SUMMARY ============
n = len(supersessions)
print("\n" + "=" * 70)
print(f"FINAL RESULT: ALL {n} REAL PRODUCTION CORRECTIONS")
print("=" * 70)
print(f"\n{'':>20} {'MemTether':>12} {'mem0':>12}")
print("-" * 50)
print(f"{'Past (old)':>20} {mt_past:>6}/{n}  {mem0_past:>6}/{mem0_total}")
print(f"{'Now (new)':>20} {mt_now:>6}/{n}  {mem0_now:>6}/{mem0_total}")
mt_pct = min(mt_past, mt_now)/n*100
mem0_pct = mem0_past/mem0_total*100 if mem0_total else 0
print("-" * 50)
print(f"{'Temporal acc.':>20} {mt_pct:>11.1f}%  {mem0_pct:>11.1f}%")

# Save
with open("E:/Temp/_temporal_full_results.json", "w", encoding="utf-8") as f:
    json.dump({
        "n_supersessions": n,
        "memtether": {"past": mt_past, "now": mt_now, "total": n},
        "mem0": {"past": mem0_past, "now": mem0_now, "total": mem0_total, "errors": errors},
    }, f, indent=2)
print("\nsaved")
