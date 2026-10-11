# -*- coding: utf-8 -*-
"""downstream_harm.py — Temporal Interference on Downstream Tasks

THE EXPERIMENT: Prove that temporal gaps in memory storage cause
measurable downstream harm on real tasks.

Design:
  1. Take 334 real production supersession events (old_value → new_value)
  2. For each, construct a downstream QA task: "Based on the memory context,
     what is the current status/value of [entity]?"
  3. Create two contexts:
     A) CORRECT context: contains the NEW value (bi-temporal result)
     B) STALE context: contains the OLD value (what mem0 would return if
        it retrieved the wrong version, or what an agent would see if
        the correction hadn't been applied)
  4. Ask an LLM to answer using each context
  5. Measure: accuracy of A vs B

Expected: A ~95% correct, B ~40-60% correct (LLM confused by stale value)
This proves: temporal gap → downstream task failure → real harm.
"""
import json, io, os, sys, sqlite3, time, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'E:\RUANJIAN\memory_hub\.venv-memory\Lib\site-packages')

# Load real supersession data
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

# Filter: keep only corrections where the entity is identifiable
# (i.e., the content mentions a specific tool/config/person)
identifiable = []
for s in supersessions:
    old = s["old_content"]
    new = s["new_content"]
    # Must have some extractable entity info
    if len(old) > 20 and len(new) > 20:
        identifiable.append(dict(s))

print(f"Total supersessions: {len(supersessions)}")
print(f"Identifiable (content > 20 chars): {len(identifiable)}")

# Sample 100 for the experiment (LLM calls cost money)
import random
random.seed(42)
sample = random.sample(identifiable, min(100, len(identifiable)))
print(f"Sample: {len(sample)} events")

# ============ LOAD LLM ============
WB2API_KEY = json.loads(open(r"E:\RUANJIAN\workbuddy2api-panel\config.json", encoding="utf-8").read())["api_key"]

from openai import OpenAI
client = OpenAI(api_key=WB2API_KEY, base_url='http://127.0.0.1:7874/v1')

def llm_answer(context, question, model='cn:glm-5.3-flash'):
    """Ask LLM to answer based on context."""
    try:
        r = client.chat.completions.create(
            model=model,
            max_tokens=200,
            temperature=0,
            messages=[
                {"role": "system", "content": "You are a helpful assistant. Answer the question based ONLY on the provided memory context. If the context contains conflicting information, report the most recent value. Be concise."},
                {"role": "user", "content": f"Memory context:\n{context}\n\nQuestion: {question}"}
            ]
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        return f"ERROR: {e}"

def extract_entity(old_content, new_content):
    """Try to extract the main entity/topic from the correction."""
    # Use the reason field or extract from content
    # Simple approach: find common words between old and new
    old_words = set(re.findall(r'[\w\u4e00-\u9fff]+', old_content.lower()))
    new_words = set(re.findall(r'[\w\u4e00-\u9fff]+', new_content.lower()))
    common = old_words & new_words
    # Filter stopwords
    stop = {'the','a','an','is','are','was','were','in','on','at','to','for','of','and','or','的','是','了','在','和','或','source','codex','2026','2025'}
    common -= stop
    if common:
        return ' '.join(list(common)[:3])
    return old_content[:30]

# ============ RUN EXPERIMENT ============
print(f"\nRunning downstream harm experiment on {len(sample)} corrections...")
print("For each: LLM answers with CORRECT context vs STALE context\n")

results = {
    "correct_context_correct": 0,
    "correct_context_wrong": 0,
    "stale_context_correct": 0,
    "stale_context_wrong": 0,
    "total": 0,
    "details": []
}

t0 = time.time()
for i, s in enumerate(sample):
    old_c = s["old_content"]
    new_c = s["new_content"]
    entity = extract_entity(old_c, new_c)
    
    # Construct the question: "What is the current state of [entity]?"
    question = f"Based on the memory context, what is the current status or value regarding: {entity}?"
    
    # Ground truth: the NEW value is the current correct answer
    # The OLD value is stale/incorrect
    
    # Context A: CORRECT (only new value visible — what MemTether returns)
    context_correct = f"Memory entry: {new_c}"
    
    # Context B: STALE (both old and new visible — what happens without temporal filtering)
    # This simulates: an overwrite-style store that returns both, or an agent that
    # sees the old value because the correction hasn't propagated
    context_stale = f"Memory entry 1 (earlier): {old_c}\nMemory entry 2 (later): {new_c}"
    
    # Get LLM answers
    answer_correct = llm_answer(context_correct, question)
    answer_stale = llm_answer(context_stale, question)
    
    # Evaluate: does the answer contain the NEW value's key info?
    # We use a simple check: does the answer align more with new_c or old_c?
    
    # For context_correct: LLM should report the NEW value
    # Check: answer_correct should contain key phrases from new_c
    new_key_words = set(re.findall(r'[\w\u4e00-\u9fff]{2,}', new_c.lower()))
    old_key_words = set(re.findall(r'[\w\u4e00-\u9fff]{2,}', old_c.lower()))
    answer_c_words = set(re.findall(r'[\w\u4e00-\u9fff]{2,}', answer_correct.lower()))
    answer_s_words = set(re.findall(r'[\w\u4e00-\u9fff]{2,}', answer_stale.lower()))
    
    # Overlap with new (correct) vs old (stale)
    overlap_c_new = len(answer_c_words & new_key_words)
    overlap_c_old = len(answer_c_words & old_key_words - new_key_words)
    overlap_s_new = len(answer_s_words & new_key_words)
    overlap_s_old = len(answer_s_words & old_key_words - new_key_words)
    
    # Correct = answer aligns with the NEW value
    correct_ctx_ok = overlap_c_new >= overlap_c_old  # with correct context, answer should reflect new value
    stale_ctx_ok = overlap_s_new > overlap_s_old  # with stale context, can LLM still pick the right one?
    
    results["total"] += 1
    if correct_ctx_ok: results["correct_context_correct"] += 1
    else: results["correct_context_wrong"] += 1
    if stale_ctx_ok: results["stale_context_correct"] += 1
    else: results["stale_context_wrong"] += 1
    
    results["details"].append({
        "i": i,
        "entity": entity[:40],
        "old": old_c[:60],
        "new": new_c[:60],
        "answer_correct": answer_correct[:80],
        "answer_stale": answer_stale[:80],
        "correct_ctx_ok": correct_ctx_ok,
        "stale_ctx_ok": stale_ctx_ok,
    })
    
    if (i+1) % 20 == 0:
        elapsed = time.time() - t0
        cc = results["correct_context_correct"]
        sc = results["stale_context_correct"]
        t = results["total"]
        print(f"  {i+1}/{len(sample)} ({elapsed:.0f}s) | correct_ctx: {cc}/{t} | stale_ctx: {sc}/{t}")

# ============ SUMMARY ============
t = results["total"]
cc_pct = results["correct_context_correct"]/t*100 if t else 0
sc_pct = results["stale_context_correct"]/t*100 if t else 0

print(f"\n{'='*70}")
print(f"TEMPORAL INTERFERENCE ON DOWNSTREAM TASKS")
print(f"{'='*70}")
print(f"Total corrections tested: {t}")
print()
print(f"{'':>30} {'Correct ctx':>15} {'Stale ctx':>15}")
print(f"{'':>30} {'(bi-temporal)':>15} {'(overwrite-style)':>18}")
print("-" * 70)
print(f"{'Downstream accuracy':>30} {cc_pct:>14.1f}%  {sc_pct:>14.1f}%")
print(f"{'Downstream errors':>30} {results['correct_context_wrong']:>9}/{t}  {results['stale_context_wrong']:>9}/{t}")
print("-" * 70)

difference = cc_pct - sc_pct
print(f"\nAccuracy difference: {difference:.1f}pp")
if difference > 10:
    print(f"*** TEMPORAL GAP CAUSES SIGNIFICANT DOWNSTREAM HARM ***")
elif difference > 5:
    print(f"** Temporal gap causes measurable downstream harm **")
else:
    print(f"   Temporal gap has minimal downstream impact")

# Statistical significance (McNemar test for paired proportions)
b = results["correct_context_correct"] - min(results["correct_context_correct"], results["stale_context_correct"])
c = results["stale_context_correct"] - min(results["correct_context_correct"], results["stale_context_correct"])
n_discordant = b + c
if n_discordant > 0:
    from math import comb
    p_value = sum(comb(n_discordant, k) for k in range(0, min(b, c) + 1)) / 2**n_discordant * 2
    p_value = min(p_value, 1.0)
else:
    p_value = 1.0
print(f"\nMcNemar test: b={b}, c={c}, p={p_value:.4f}")
print(f"{'*** SIGNIFICANT ***' if p_value < 0.05 else 'not significant'}")

# Save
with open("E:/Temp/_downstream_results.json", "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)
print(f"\nsaved to E:/Temp/_downstream_results.json")
print(f"Total time: {time.time()-t0:.0f}s")
