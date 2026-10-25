import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from erp import guard, seed

DB = seed.DB_PATH
if not os.path.exists(DB):
    seed.build().close()

OK = ["SELECT COUNT(*) FROM invoices", "WITH t AS (SELECT * FROM items) SELECT name_en FROM t LIMIT 3",
      "select * from receivables_ageing", "SELECT 'a;b' AS x"]
BAD = ["DROP TABLE invoices", "DELETE FROM invoices", "SELECT 1; DROP TABLE items", "UPDATE items SET reorder_level=0",
       "SELECT * FROM sqlite_master", "PRAGMA table_info(items)", "ATTACH DATABASE 'x' AS y", "SELECT load_extension('x')",
       "INSERT INTO items SELECT * FROM items", "SELECT * FROM secret_table", "SELECT * FROM items, meta",
       "SELECT readfile('/etc/passwd')", "WITH RECURSIVE r(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM r) SELECT COUNT(*) FROM r"]

for q in OK:
    guard.run(DB, q)
for q in BAD:
    try:
        guard.run(DB, q)
    except guard.Unsafe:
        continue
    raise AssertionError(f"guard let through: {q}")
cols, rows, tables, trunc = guard.run(DB, "SELECT * FROM invoice_lines")
assert len(rows) == guard.MAX_ROWS and trunc and tables == ["invoice_lines"]
print(f"guard ok: {len(OK)} allowed, {len(BAD)} blocked, row cap enforced")
