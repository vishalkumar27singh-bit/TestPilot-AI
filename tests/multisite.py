"""A farm of local 'websites' with deliberately different behaviour, used to prove TestPilot is general-purpose
(nothing here is known to the engine). Each kind runs on its own random port."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LONG = "This page has a reasonable amount of ordinary text content so it is not mistaken for an empty application shell. " * 4

PAGES = {
    "shell": '<!doctype html><html><head><title>Streamlit</title></head><body><div id="root"></div>'
             '<noscript>You need to enable JavaScript to run this app.</noscript><script src="/static/js/main.js"></script></body></html>',
    "minimal": f'<!doctype html><html lang="en"><head><title>A minimal page for testing</title><meta name="viewport" content="width=device-width"></head>'
               f'<body><h1>Hello</h1><p>{LONG}</p></body></html>',
    "sleeping": f'<!doctype html><html><head><title>App</title></head><body><h1>This app has gone to sleep due to inactivity</h1>'
                f'<p>{LONG}</p><button>Yes, get this app back up!</button></body></html>',
    "broken_html": f'<html><head><title>Messy markup page title</title><body><div><p>Unclosed <b>bold <a href="/x">x</a><form><input name=q>'
                   f'<table><tr><td>cell<img src=/none.png><p>{LONG}',
    "head405": f'<!doctype html><html lang="en"><head><title>HEAD not allowed page</title><meta name="viewport" content="width=device-width"></head>'
               f'<body><nav><a href="/ok">OK page</a></nav><p>{LONG}</p></body></html>',
    "login": '<!doctype html><html><head><title>Sign in</title></head><body><h1>Sign in</h1><form action="/login" method="post">'
             '<input name="user" placeholder="user"><input type="password" name="pw" placeholder="pw"><button type="submit">Sign in</button></form></body></html>',
    "home": f'<!doctype html><html lang="en"><head><title>Home after cookie handshake</title><meta name="viewport" content="width=device-width"></head>'
            f'<body><nav><a href="/home">Home</a></nav><p>{LONG}</p></body></html>',
}

class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):  # clients timing out on purpose is expected
        pass


_servers: dict = {}
_lock = threading.Lock()


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *a): pass

    def _send(self, code, body="", ctype="text/html", headers=None):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(b)

    def _redir(self, to, code=302, headers=None):
        self._send(code, "", headers={"Location": to, **(headers or {})})

    do_HEAD = do_POST = lambda self: self.do_GET()

    def do_GET(self):
        kind, path = self.server.kind, self.path.split("?")[0]
        if kind == "shell": return self._send(200, PAGES["shell"])
        if kind == "minimal": return self._send(200, PAGES["minimal"])
        if kind == "sleeping": return self._send(200, PAGES["sleeping"])
        if kind == "broken_html": return self._send(200, PAGES["broken_html"])
        if kind == "empty": return self._send(200, "")
        if kind == "json": return self._send(200, json.dumps({"hello": "world"}), "application/json")
        if kind == "err500": return self._send(500, "<h1>boom</h1>")
        if kind == "notfound": return self._send(404, "<h1>nope</h1>")
        if kind == "forbidden": return self._send(403, "<h1>denied</h1>")
        if kind == "ratelimited": return self._send(429, "slow down")
        if kind == "slow":
            time.sleep(3); return self._send(200, PAGES["minimal"])
        if kind == "login":
            return self._redir("/login", 303) if path != "/login" else self._send(200, PAGES["login"])
        if kind == "loop":
            return self._redir("/b" if path != "/b" else "/a")
        if kind == "cookie":
            if path == "/set": return self._redir("/home", headers={"Set-Cookie": "sid=ok; Path=/"})
            if path == "/home" and "sid=ok" in (self.headers.get("Cookie") or ""): return self._send(200, PAGES["home"])
            return self._redir("/set")
        if kind == "head405":
            if self.command == "HEAD": return self._send(405, "")
            return self._send(200, PAGES["head405"] if path == "/" else "<html><title>ok</title><body>ok</body></html>")
        if kind == "long_chain":  # /h1 -> /h2 -> ... -> /h8 -> /end  (8 redirects; the old client gave up after 5)
            n = int(path[2:]) if path.startswith("/h") and path[2:].isdigit() else 0
            if path == "/end": return self._send(200, PAGES["home"])
            return self._redir("/end" if n >= 8 else f"/h{n + 1}", 301)
        if kind == "endless":  # never reaches a page: /?n=1 -> /?n=2 -> ... (state never repeats, so not a 'loop')
            n = int((self.path.split("n=")[1] if "n=" in self.path else "0").split("&")[0] or 0)
            return self._redir(f"/?n={n + 1}", 303)
        if kind == "accept_gate":  # only serves a page to clients that ask for HTML; otherwise redirects to itself
            return self._send(200, PAGES["home"]) if "text/html" in (self.headers.get("Accept") or "") else self._redir(path, 302)
        if kind == "to_internal": return self._redir("http://169.254.169.254/latest/meta-data")
        if kind == "bounce": return self._redir(self.server.target)
        if kind == "echo":  # returns the request headers it received; /r redirects to another server's /echo
            if path == "/r": return self._redir(self.server.target + "/echo")
            if path == "/same": return self._redir("/echo")
            return self._send(200, json.dumps({k.lower(): v for k, v in self.headers.items()}), "application/json")
        return self._send(404, "unknown kind")


def site(kind: str, target: str = "") -> str:
    """Start (once) a server of this kind; returns its base URL."""
    key = (kind, target)
    with _lock:
        if key not in _servers:
            srv = QuietServer(("127.0.0.1", 0), H)
            srv.kind, srv.target = kind, target
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            _servers[key] = srv
        return f"http://127.0.0.1:{_servers[key].server_address[1]}"
