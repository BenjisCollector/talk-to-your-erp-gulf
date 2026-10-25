"""Tiny stdlib HTTP server: serves the RTL UI and POST /api/ask. No third-party dependencies."""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import engine, seed

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            with open(os.path.join(WEB, "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if path == "/health":
            return self._send(200, {"status": "ok"})
        if path == "/api/samples":
            return self._send(200, {"mode": "llm" if engine.llm_config() else "canned", "as_of": seed.AS_OF,
                                    "samples": [{"id": k, "en": v[0], "ar": v[1]} for k, v in engine.SHOWCASE]})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/ask":
            return self._send(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length") or 0)
        if n > 4096:
            return self._send(413, {"error": "question too long"})
        try:
            q = json.loads(self.rfile.read(n) or b"{}").get("question", "")
        except ValueError:
            return self._send(400, {"error": "bad json"})
        self._send(200, engine.ask(q))

    def log_message(self, fmt, *a):
        sys.stderr.write("[erp] " + fmt % a + "\n")


def main():
    if not os.path.exists(seed.DB_PATH):
        seed.build().close()
        print("[erp] seeded", os.path.abspath(seed.DB_PATH))
    host, port = os.environ.get("HOST", "127.0.0.1"), int(os.environ.get("PORT", "8080"))
    mode = "LLM (OpenAI-compatible)" if engine.llm_config() else "canned demo answers (no API key found)"
    print(f"[erp] Talk to your ERP, Gulf edition: http://{host}:{port}  mode: {mode}")
    ThreadingHTTPServer((host, port), H).serve_forever()


if __name__ == "__main__":
    main()
