#!/usr/bin/env python3
"""make_jury.py — One-command verification for competition judges."""
import json
import os
import subprocess
import sys
import time
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["PYTHONIOENCODING"] = "utf-8"

ROOT = os.path.dirname(os.path.abspath(__file__))
REPORT = []
PASS = 0
FAIL = 0

def log(msg):
    REPORT.append(msg)
    print(msg)

def check(name, cmd, timeout=120):
    global PASS, FAIL
    log(f"\nCHECK: {name}")
    t0 = time.time()
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, cwd=ROOT)
        ok = r.returncode == 0
        elapsed = round(time.time() - t0, 1)
        status = "PASS" if ok else "FAIL"
        if ok: PASS += 1
        else: FAIL += 1
        log(f"  {status} ({elapsed}s)")
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if any(k in line.lower() for k in ["passed", "100%", "recall", "precision", "integrity", "version"]):
                log(f"    {line[:120]}")
        return ok
    except Exception as e:
        FAIL += 1
        log(f"  FAIL: {e}")
        return False

def main():
    log("=" * 60)
    log("MEMTETHER VERIFICATION REPORT")
    log(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log("=" * 60)

    py = sys.executable

    log("\nSECTION 1: PACKAGE")
    check("Import + version", [py, "-c", "import memtether; print(memtether.__version__)"])

    log("\nSECTION 2: CORE TESTS")
    check("Memory Court (hash chain/tamper/verify)", [py, "-m", "pytest", "tests/test_memory_court.py", "-q", "--tb=no"])
    check("Poisoning defense (8 vectors)", [py, "-m", "pytest", "tests/test_poisoning_defense.py", "-q", "--tb=no"])
    check("E2E integration (gate/supersede/verify)", [py, "-m", "pytest", "tests/test_court_e2e_integration.py", "-q", "--tb=no"])
    check("Integrity (cycle/race/privacy)", [py, "-m", "pytest", "tests/test_r6_integrity.py", "-q", "--tb=no"])
    check("Supersession+search", [py, "-m", "pytest", "tests/test_timeline_api.py", "tests/test_consolidation.py", "-q", "--tb=no"])

    log("\nSECTION 3: AUDIT DEMO")
    demo = os.path.join(ROOT, "examples", "audit_chain_demo.py")
    if os.path.exists(demo):
        check("Audit chain demo (tamper -> FAIL)", [py, demo])

    log("\n" + "=" * 60)
    log(f"RESULT: {PASS} passed, {FAIL} failed, {PASS+FAIL} total")
    log("=" * 60)

    report_path = os.path.join(ROOT, "VERIFICATION_REPORT.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(REPORT))
    print(f"Report: {report_path}")
    return FAIL == 0

if __name__ == "__main__":
    sys.exit(0 if main() else 1)