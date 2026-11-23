"""Test helpers: a throw-away copy of the sample site/API on a random free port, and temp databases."""
import sys
import tempfile
import threading
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sample_site"))
import serve  # noqa: E402  (the sample_site/serve.py module)

_server = None
LOOPBACK = {"allow_loopback": True, "allow_private": False}


def base_url() -> str:
    global _server
    if _server is None:
        _server = ThreadingHTTPServer(("127.0.0.1", 0), partial(serve.Handler, directory=str(ROOT / "sample_site")))
        threading.Thread(target=_server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{_server.server_address[1]}"


def temp_db():
    from database.db_manager import DB
    return DB(tempfile.mktemp(suffix=".db"))
