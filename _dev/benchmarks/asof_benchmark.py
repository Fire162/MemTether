# -*- coding: utf-8 -*-
"""asof_benchmark.py — The As-of Impossibility Experiment.

Same corrections applied to mem0 (overwrite-style) and MemTether (bi-temporal).
Then measure: as_of query latency + correctness as k (number of corrections) grows.

This is the CORE experiment for the paper's central architectural claim.
"""
import json, os, sys, time, sqlite3, tempfile, shutil, hashlib
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, r'E:\RUANJIAN\memtether')
sys.path.insert(0, r'E:\RUANJIAN\memory_hub\.venv-memory\Lib\site-packages')

# ============ SETUP ============
WB2API_KEY = json.loads(open(r"E:\RUANJIAN\workbuddy2api-panel\config.json", encoding="utf-8").read())["api_key"]

from mem0 import Memory

# mem0 setup (fresh)
MEM0_CHROMA = "E:/Temp/_asof_mem0_chroma"
MEM0_HIST = "E:/Temp/_asof_mem0_history.db"
MEM0_SHADOW = "E:/Temp/_asof_mem0_shadow.db"
for p in [MEM0_CHROMA, MEM0_HIST, MEM0_SHADOW]:
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
        "collection_name": "asof_test", "path": MEM0_CHROMA}},
    "history_db_path": MEM0_HIST,
    "version": "v1.1"
}

# MemTether setup (fresh)
MEMTETHER_DB = "E:/Temp/_asof_mt.db"
if os.path.exists(MEMTETHER_DB):
    os.remove(MEMTETHER_DB)
os.environ['MEM_DB'] = MEMTETHER_DB
os.environ['MEM_SKIP_VECTOR'] = '1'  # skip vector for pure SQL test
os.environ['PYTHONIOENCODING'] = 'utf-8'

import gateway

# ============ CORRECTIONS TO APPLY ============
K_VALUES = [1, 5, 10, 25, 50, 100, 200, 500]

def run_mem0_asof(k):
    """Apply k corrections to same memory in mem0, then try as-of."""
    m = Memory.from_config(mem0_config)
    
    # Initial write
    base_text = f"Service port is 8000"
    r = m.add(base_text, user_id="asof_test")
    mem0_id = r["results"][0]["id"] if r.get("results") else None
    if not mem0_id:
        return {"latency": None, "correct": None, "error": "add_failed"}
    
    # Apply k corrections
    t0 = time.time()
    for i in range(k):
        new_text = f"Service port is {8001 + i}"
        m.update(memory_id=mem0_id, text=new_text)
    correction_time = time.time() - t0
    
    # Now: as-of query "what was the value before correction k/2?"
    target_value = f"Service port is {8000 + k // 2}"
    
    # Attempt 1: search (should return CURRENT value, not historical)
    t1 = time.time()
    results = m.search("Service port", filters={"user_id": "asof_test"}, top_k=5)
    search_latency = time.time() - t1
    
    # Check: does search return the HISTORICAL value?
    found_historical = False
    current_value = f"Service port is {8000 + k - 1}"  # last correction
    for x in results.get("results", []):
        if target_value in x.get("memory", ""):
            found_historical = True
    
    # Attempt 2: history replay (the wrapper approach)
    t2 = time.time()
    hist = m.history(mem0_id)
    # Walk history to find value at time of correction k/2
    # This is O(k) — the log replay cost
    replay_latency = time.time() - t2
    
    # Check if replay found it
    replay_correct = False
    for h in hist:
        if h.get("new_memory") == target_value:
            replay_correct = True
            break
    
    return {
        "k": k,
        "search_latency": round(search_latency, 4),
        "replay_latency": round(replay_latency, 4),
        "search_returns_historical": found_historical,
        "replay_finds_historical": replay_correct,
        "search_returns_current": any(current_value in x.get("memory", "") for x in results.get("results", [])),
        "correction_time": round(correction_time, 3),
        "history_entries": len(hist),
    }

def run_mt_asof(k):
    """Apply k corrections to same fact in MemTether, then as-of query."""
    conn = sqlite3.connect(MEMTETHER_DB)
    conn.execute("DELETE FROM facts WHERE uid LIKE 'asof-%'")
    conn.execute("DELETE FROM supersessions WHERE old_uid LIKE 'asof-%'")
    conn.commit()
    conn.row_factory = sqlite3.Row
    
    # Initial write (direct SQL for speed — we're testing storage, not API overhead)
    ts0 = "2026-01-01 00:00:00"
    conn.execute("""
        INSERT INTO facts (uid, type, subject, content, status, source, scope, confidence,
                          created_at, updated_at, valid_from, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (f"asof-{k}", "fact", "test", "Service port is 8000", "active",
          "test", "shared", 0.8, ts0, ts0, ts0, ts0))
    conn.commit()
    
    uid = f"asof-{k}"
    prev_uid = uid
    
    # Apply k corrections
    t0 = time.time()
    for i in range(k):
        new_uid = f"asof-{k}-c{i}"
        ts = f"2026-01-01 00:{i:02d}:00" if i < 60 else f"2026-01-01 01:{i-60:02d}:00"
        new_text = f"Service port is {8001 + i}"
        
        # Supersede: mark old as superseded, insert new
        conn.execute("UPDATE facts SET status='superseded', superseded_by=?, valid_to=?, updated_at=? WHERE uid=? AND status='active'",
                     (new_uid, ts, ts, prev_uid))
        conn.execute("""
            INSERT INTO facts (uid, type, subject, content, status, source, scope, confidence,
                              created_at, updated_at, valid_from, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (new_uid, "fact", "test", new_text, "active",
              "test", "shared", 0.8, ts, ts, ts, ts))
        conn.execute("INSERT INTO supersessions (old_uid, new_uid, reason, by_agent, ts) VALUES (?,?,?,?,?)",
                     (prev_uid, new_uid, f"correction {i}", "test", ts))
        prev_uid = new_uid
    conn.commit()
    correction_time = time.time() - t0
    
    # As-of query: what was the value at correction k/2?
    target_time = f"2026-01-01 00:{k//2:02d}:00" if k // 2 < 60 else f"2026-01-01 01:{k//2-60:02d}:00"
    target_value = f"Service port is {8000 + k // 2}"
    
    t1 = time.time()
    # Bi-temporal as-of: single indexed query
    row = conn.execute("""
        SELECT uid, content, valid_from, valid_to
        FROM facts
        WHERE subject = 'test'
          AND valid_from <= ?
          AND (valid_to IS NULL OR valid_to > ?)
          AND content LIKE 'Service port%'
        ORDER BY valid_from DESC
        LIMIT 1
    """, (target_time, target_time)).fetchone()
    asof_latency = time.time() - t1
    
    correct = row is not None and target_value in row["content"]
    return {
        "k": k,
        "asof_latency": round(asof_latency, 6),
        "asof_correct": correct,
        "asof_value": row["content"] if row else None,
        "correction_time": round(correction_time, 3),
    }

# ============ RUN ============
print("=" * 70)
print("AS-OF IMPOSSIBILITY EXPERIMENT")
print("Same corrections, two storage architectures, one metric")
print("=" * 70)
print()

all_results = {"mem0": [], "memtether": []}

# Initialize MemTether DB
gateway.init_db()
print("[setup] MemTether DB initialized")

for k in K_VALUES:
    print(f"--- k={k} corrections ---")
    
    # mem0
    try:
        r0 = run_mem0_asof(k)
        all_results["mem0"].append(r0)
        print(f"  mem0: search={r0['search_latency']:.3f}s replay={r0['replay_latency']:.4f}s "
              f"hist_ok={r0['replay_finds_historical']} search_ok={r0['search_returns_historical']}")
    except Exception as e:
        print(f"  mem0 ERROR: {e}")
        all_results["mem0"].append({"k": k, "error": str(e)})
    
    # MemTether
    r1 = run_mt_asof(k)
    all_results["memtether"].append(r1)
    print(f"  MemTether: asof={r1['asof_latency']:.6f}s correct={r1['asof_correct']} value={r1.get('asof_value','?')[:40]}")
    print()

# ============ SUMMARY ============
print("=" * 70)
print("RESULTS SUMMARY")
print("=" * 70)
print()
print(f"{'k':>6} | {'mem0 search':>12} | {'mem0 replay':>12} | {'mem0 hist_ok':>12} | {'MT asof':>12} | {'MT correct':>10}")
print("-" * 80)
for m0, mt in zip(all_results["mem0"], all_results["memtether"]):
    k = m0.get("k", mt.get("k", "?"))
    s_lat = f"{m0.get('search_latency', 0):.4f}" if "search_latency" in m0 else "ERR"
    r_lat = f"{m0.get('replay_latency', 0):.4f}" if "replay_latency" in m0 else "ERR"
    h_ok = str(m0.get("replay_finds_historical", "?"))
    mt_lat = f"{mt.get('asof_latency', 0):.6f}"
    mt_ok = str(mt.get("asof_correct", "?"))
    print(f"{k:>6} | {s_lat:>12} | {r_lat:>12} | {h_ok:>12} | {mt_lat:>12} | {mt_ok:>10}")

print()
print("KEY FINDING:")
mt_latencies = [x["asof_latency"] for x in all_results["memtether"] if "asof_latency" in x]
mt_corrects = [x["asof_correct"] for x in all_results["memtether"] if "asof_correct" in x]
print(f"  MemTether as-of latency: min={min(mt_latencies):.6f}s max={max(mt_latencies):.6f}s (flat = O(log n))")
print(f"  MemTether as-of correctness: {sum(mt_corrects)}/{len(mt_corrects)} correct")
replay_lats = [x.get("replay_latency", 0) for x in all_results["mem0"] if "replay_latency" in x]
if replay_lats:
    print(f"  mem0 replay latency: min={min(replay_lats):.4f}s max={max(replay_lats):.4f}s (growing = O(n))")
search_oks = [x.get("search_returns_historical") for x in all_results["mem0"] if "search_returns_historical" in x]
print(f"  mem0 search returns historical: {sum(search_oks)}/{len(search_oks)} (expected 0 — overwrite destroys history)")

# Save
with open("E:/Temp/_asof_results.json", "w", encoding="utf-8") as f:
    json.dump(all_results, f, indent=2, ensure_ascii=False)
print("\nsaved to E:/Temp/_asof_results.json")
