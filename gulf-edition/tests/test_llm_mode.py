"""Exercises the LLM code path against a local mock OpenAI-compatible server (no real key, no network)."""
import json, os, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from erp import engine, seed

if not os.path.exists(seed.DB_PATH):
    seed.build().close()
calls = []
SQL = ["DROP TABLE invoices",  # 1st attempt is unsafe -> guard rejects -> engine retries with the error
       "SELECT currency, COUNT(*) AS invoices, ROUND(SUM(total_amount),3) AS sales FROM invoices GROUP BY currency ORDER BY currency"]


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert self.headers["Authorization"] == "Bearer test-key"
        if body.get("response_format"):
            sql = SQL[min(len([c for c in calls if c == "sql"]), 1)]
            calls.append("sql")
            content = json.dumps({"sql": sql})
        else:
            calls.append("answer")
            content = "Sales were highest in SAR."
        data = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def log_message(self, *a): pass


srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ.update(OPENAI_API_KEY="test-key", OPENAI_BASE_URL=f"http://127.0.0.1:{srv.server_port}/v1")
out = engine.ask("What are our sales by currency?")
assert out["mode"] == "llm" and out["answer"] == "Sales were highest in SAR." and len(out["rows"]) == 3, out
assert calls == ["sql", "sql", "answer"], calls  # unsafe first reply was retried
assert out["sources"] == ["invoices"] and out["chart"]
os.environ["OPENAI_BASE_URL"] = "http://127.0.0.1:9/v1"  # unreachable -> graceful fallback to canned
out = engine.ask("Show the receivables ageing")
assert out["mode"] == "canned" and out["warning"] and out["rows"], out
print("llm mode ok: retry-on-unsafe, sources, chart, fallback when endpoint is down")
