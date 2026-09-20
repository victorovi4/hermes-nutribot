"""Local preview of the app: the real handler on http://127.0.0.1:$NUTRI_DEV_PORT (default 8791), sign-in switched off.

Run with a Python that has the Google libraries (the Hermes venv works):
    HERMES_HOME=~/.hermes/profiles/nutrition NUTRI_STORAGE=sheets NUTRI_SPREADSHEET_ID=<id> python deploy/dev_server.py
Never point it at the live spreadsheet unless you mean to edit the real diary.
"""
from __future__ import annotations

import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT)]

from api.handler import make_handler  # noqa: E402
from nutricore.store import open_store  # noqa: E402

PORT = int(os.environ.get("NUTRI_DEV_PORT", "8791"))
_store = {}
_lock = threading.Lock()          # the Google client is not thread-safe; the cloud function also serves one request at a time


def store():
    if "s" not in _store:
        _store["s"] = open_store(os.environ)
    return _store["s"]


handle = make_handler(store_factory=store, bot_token="", allowed_ids=set(), web_dir=ROOT / "web", auth_disabled=True)


class Server(BaseHTTPRequestHandler):
    def _serve(self):
        length = int(self.headers.get("Content-Length") or 0)
        event = {"httpMethod": self.command, "url": self.path, "headers": dict(self.headers),
                 "body": self.rfile.read(length).decode("utf-8") if length else "", "isBase64Encoded": False}
        with _lock:
            result = handle(event, None)
        body = result["body"]
        if result.get("isBase64Encoded"):
            import base64
            data = base64.b64decode(body)
        else:
            data = body.encode("utf-8")
        self.send_response(result["statusCode"])
        for key, value in result["headers"].items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PATCH = do_DELETE = _serve

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"http://127.0.0.1:{PORT}  (хранилище: {os.environ.get('NUTRI_STORAGE', 'sqlite')})")
    ThreadingHTTPServer(("127.0.0.1", PORT), Server).serve_forever()
