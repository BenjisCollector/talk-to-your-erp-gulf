"""20 golden questions: does the generated SQL return the same result set as a hand-written gold query?
Works in both modes: canned engine (default, offline) or LLM (when OPENAI_API_KEY / AZURE_OPENAI_* are set).
Usage: python3 eval/run_eval.py [--canned]   (--canned forces the offline engine even if a key exists)"""
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from erp import engine, guard, seed  # noqa: E402

UNSAFE = ["DROP TABLE invoices", "DELETE FROM customers", "UPDATE items SET reorder_level = 0", "SELECT 1; DROP TABLE items",
          "SELECT * FROM sqlite_master", "SELECT * FROM users", "PRAGMA table_info(items)", "ATTACH DATABASE 'x.db' AS x"]


def canon(v, ar):
    if isinstance(v, float):
        return round(v, 2)
    return engine.VALUE_AR.get(v, v) if ar and isinstance(v, str) else v


def same(gold_cols, pred_rows, gold_rows, ordered):
    """Every gold column must appear (as the same list/multiset of values) among the predicted columns."""
    if len(gold_rows) != len(pred_rows):
        return False
    used = set()
    for gi in range(len(gold_rows[0]) if gold_rows else 0):
        gv = [r[gi] for r in gold_rows]
        for pi in range(len(pred_rows[0])):
            pv = [r[pi] for r in pred_rows]
            if pi not in used and (gv == pv if ordered else sorted(map(str, gv)) == sorted(map(str, pv))):
                used.add(pi)
                break
        else:
            return False
    return True


def main():
    force = "--canned" in sys.argv
    if not os.path.exists(seed.DB_PATH):
        seed.build().close()
    golden = json.load(open(os.path.join(HERE, "golden.json"), encoding="utf-8"))
    db = sqlite3.connect(f"file:{seed.DB_PATH}?mode=ro", uri=True)
    passed = 0
    mode = None
    for g in golden:
        out = engine.ask(g["question"], force_canned=force)
        mode = out["mode"]
        L = out["language"]
        ok, why = False, out["error"] or ""
        if not out["error"]:
            gold = [[canon(v, L == "ar") for v in r] for r in db.execute(g["gold_sql"].replace("{L}", L)).fetchall()]
            pred = [[canon(v, L == "ar") for v in r] for r in out["rows"]]
            ok = same(None, pred, gold, g.get("ordered", False))
            why = "" if ok else f"result mismatch ({len(pred)} rows vs {len(gold)} gold)"
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'}  {g['id']:>2}  [{L}] {g['question']}  {why}")
    blocked = 0
    for q in UNSAFE:
        try:
            guard.run(seed.DB_PATH, q)
        except guard.Unsafe:
            blocked += 1
    print(f"\nSQL correctness: {passed}/{len(golden)} passed (mode: {mode})")
    print(f"Read-only guard: {blocked}/{len(UNSAFE)} unsafe statements blocked")
    sys.exit(0 if passed == len(golden) and blocked == len(UNSAFE) else 1)


if __name__ == "__main__":
    main()
