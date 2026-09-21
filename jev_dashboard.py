import json

from jev_config import ROOT
from jev_usage import aggregate, read_ledger

DASHBOARD = (ROOT / "dashboard.html").read_text(encoding="utf-8")


def build_server(port=8767):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def send_text(self, code, body, mime="application/json"):
            payload = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", mime + "; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            # Reject non-loopback Host headers so a DNS-rebinding page cannot read the ledger.
            host = (self.headers.get("Host") or "").split(":")[0].strip("[]").lower()
            if host not in ("127.0.0.1", "localhost", "::1"):
                self.send_text(403, json.dumps({"error": "forbidden"}))
                return
            if self.path.startswith("/api/stats"):
                rows = read_ledger()
                self.send_text(200, json.dumps({"totals": aggregate(rows), "recent": rows[-30:][::-1]}))
            else:
                self.send_text(200, DASHBOARD, "text/html")

        def log_message(self, *_args):
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(port=8767):
    build_server(port).serve_forever()