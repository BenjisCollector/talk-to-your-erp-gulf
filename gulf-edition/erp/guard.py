"""Read-only SQL safety: text guard + SQLite authorizer + read-only connection + row/time limits."""
import re
import sqlite3

ALLOWED_TABLES = {"customers", "suppliers", "items", "warehouses", "stock_levels", "invoices", "invoice_lines",
                  "purchase_orders", "po_lines", "fx_rates", "receivables_ageing"}
SAFE_FUNCS = {"sum", "count", "avg", "min", "max", "total", "round", "abs", "coalesce", "ifnull", "nullif", "iif",
              "length", "lower", "upper", "substr", "trim", "replace", "strftime", "date", "julianday", "datetime",
              "printf", "cast", "group_concat", "like", "instr", "typeof"}
MAX_ROWS = 200
FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|vacuum|reindex|analyze|"
                       r"begin|commit|rollback|savepoint|load_extension|sqlite_master|sqlite_schema)\b", re.I)


class Unsafe(ValueError):
    pass


def _strip(sql):
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql.strip().rstrip(";").strip()


def check(sql):
    """Return cleaned SQL or raise Unsafe. The authorizer in run() is the real enforcement; this gives clear errors."""
    s = _strip(sql)
    if not s:
        raise Unsafe("empty query")
    bare = re.sub(r"'(?:[^']|'')*'", "''", s)  # ignore string literals
    if ";" in bare:
        raise Unsafe("only one statement is allowed")
    if not re.match(r"(select|with)\b", bare, re.I):
        raise Unsafe("only SELECT queries are allowed")
    bad = FORBIDDEN.search(bare)
    if bad:
        raise Unsafe(f"keyword not allowed: {bad.group(1).lower()}")
    ctes = set(re.findall(r"(?:\bwith|,)\s*(?:recursive\s+)?([a-z_]\w*)\s*(?:\([^)]*\))?\s*as\s*\(", bare, re.I))
    for name in re.findall(r"\b(?:from|join)\s+([a-z_]\w*)", bare, re.I):
        if name.lower() not in ALLOWED_TABLES and name.lower() not in {c.lower() for c in ctes}:
            raise Unsafe(f"table not allowed: {name}")
    return s


def run(db_path, sql, max_rows=MAX_ROWS):
    """Execute guarded SQL on a read-only connection. Returns (columns, rows, tables_read, truncated)."""
    s = check(sql)
    tables = []

    def authorizer(action, a1, a2, dbname, trigger):
        if action == sqlite3.SQLITE_SELECT or action == sqlite3.SQLITE_RECURSIVE:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            if a1 in ALLOWED_TABLES:
                if a1 not in tables:
                    tables.append(a1)
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION and (a2 or "").lower() in SAFE_FUNCS:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        con.execute("PRAGMA query_only=ON")
        con.set_authorizer(authorizer)
        steps = [0]

        def watchdog():  # abort runaway queries (cartesian joins, recursive CTE loops)
            steps[0] += 1
            return 1 if steps[0] > 300 else 0  # ~3M VM instructions

        con.set_progress_handler(watchdog, 10000)
        try:
            cur = con.execute(s)
            rows = cur.fetchmany(max_rows + 1)
        except sqlite3.DatabaseError as e:
            raise Unsafe(f"query rejected or failed: {e}") from e
        cols = [c[0] for c in cur.description]
        return cols, [list(r) for r in rows[:max_rows]], tables, len(rows) > max_rows
    finally:
        con.close()
