"""Local demo server: static sample site + a tiny JSON API.   python sample_site/serve.py  ->  http://127.0.0.1:8000
Endpoints: /api/health (ok), /api/items (list), /api/items/1, /api/broken (200 but invalid JSON), /api/slow (2.5s), anything else under /api -> 404 JSON."""
import json
import sys
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ITEMS = [{"id": 1, "name": "Widget", "price": 9.99}, {"id": 2, "name": "Gadget", "price": 19.5}]


class Handler(SimpleHTTPRequestHandler):
    def _json(self, code, obj, raw=None):
        body = (raw if raw is not None else json.dumps(obj)).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _api(self):
        p = self.path.split("?")[0].rstrip("/")
        if p == "/api/health": return self._json(200, {"status": "ok", "uptime": 123})
        if p == "/api/items": return self._json(200, ITEMS)
        if p == "/api/items/1": return self._json(200, ITEMS[0])
        if p == "/api/broken": return self._json(200, None, raw="{not valid json")
        if p == "/api/slow":
            time.sleep(2.5); return self._json(200, {"slow": True})
        return self._json(404, {"error": "not found"})

    def _redirect(self):
        self.send_response(302); self.send_header("Location", "http://169.254.169.254/latest/meta-data"); self.send_header("Content-Length", "0"); self.end_headers()

    def do_GET(self):
        if self.path == "/redirect-internal":  # hostile redirect used by the SSRF tests
            return self._redirect()
        return self._api() if self.path.startswith("/api") else super().do_GET()

    def do_HEAD(self):
        return self._api() if self.path.startswith("/api") else super().do_HEAD()

    def do_POST(self):
        if self.path.startswith("/api"):
            n = int(self.headers.get("Content-Length", 0)); data = self.rfile.read(n)
            try:
                return self._json(201, {"created": json.loads(data or b"{}")})
            except ValueError:
                return self._json(400, {"error": "invalid json"})
        body = b"<html><title>Thanks</title><body>Thanks for your message.</body></html>"
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    root = Path(__file__).parent
    srv = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(root)))
    print(f"Sample site running at http://127.0.0.1:{port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
