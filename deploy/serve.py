"""Run the app as an ordinary web server — the same handler the cloud function uses.

Behind a reverse proxy that terminates HTTPS (docker compose brings Caddy for that).
Settings come from the environment: NUTRI_STORAGE / NUTRI_SPREADSHEET_ID / NUTRI_DB_PATH,
TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USERS, PORT (default 8080).
"""
from __future__ import annotations

import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT)]

from api.handler import make_handler  # noqa: E402
from nutricore.store import open_store  # noqa: E402

PORT = int(os.environ.get("PORT", "8080"))
_state: dict = {}
_lock = threading.Lock()          # one request at a time: the storage clients are not thread-safe


def store():
    if "s" not in _state:
        _state["s"] = open_store(os.environ)
    return _state["s"]


allowed = {int(v) for v in os.environ.get("TELEGRAM_ALLOWED_USERS", "").replace(";", ",").split(",") if v.strip()}
handle = make_handler(store_factory=store, bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
                      allowed_ids=allowed, web_dir=ROOT / "web",
                      auth_disabled=os.environ.get("NUTRI_AUTH_DISABLED") == "1")


class Server(BaseHTTPRequestHandler):
    server_version = "nutribot"

    def _serve(self):
        length = int(self.headers.get("Content-Length") or 0)
        event = {"httpMethod": self.command, "url": self.path, "headers": dict(self.headers),
                 "body": self.rfile.read(length).decode("utf-8") if length else "", "isBase64Encoded": False}
        with _lock:
            result = handle(event, None)
        if result.get("isBase64Encoded"):
            import base64

            data = base64.b64decode(result["body"])
        else:
            data = result["body"].encode("utf-8")
        self.send_response(result["statusCode"])
        for key, value in result["headers"].items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PATCH = do_DELETE = _serve

    def log_message(self, fmt, *args):                 # one line per request, no personal data
        sys.stderr.write("%s %s\n" % (self.command, self.path.split("?")[0]))


if __name__ == "__main__":
    if not allowed and os.environ.get("NUTRI_AUTH_DISABLED") != "1":
        raise SystemExit("TELEGRAM_ALLOWED_USERS пуст — приложение никого не пустит. Укажи свой Telegram-id.")
    print(f"nutribot: слушаю 0.0.0.0:{PORT}, хранилище {os.environ.get('NUTRI_STORAGE', 'sqlite')}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Server).serve_forever()
