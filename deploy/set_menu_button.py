"""Put the «Дневник» button (opens the app) into the bot's menu for the owner's chat. Usage: set_menu_button.py [show]"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

ENV = Path(os.environ.get("NUTRI_CLOUD_ENV") or Path(__file__).with_name("cloud.env"))
values = dict(line.strip().split("=", 1) for line in ENV.read_text().splitlines() if "=" in line and not line.startswith("#")) if ENV.exists() else {}
values = {**values, **{k: v for k, v in os.environ.items() if k.startswith(("TELEGRAM_", "NUTRI_"))}}
TOKEN = values["TELEGRAM_BOT_TOKEN"].strip("\"'")
CHAT_ID = int(values["TELEGRAM_ALLOWED_USERS"].strip("\"'").split(",")[0])
URL = (os.environ.get("NUTRI_APP_URL") or Path(__file__).with_name("app-url.txt").read_text()).strip()


def api(method: str, payload: dict) -> dict:
    request = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/{method}", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


if __name__ == "__main__":
    if sys.argv[1:] != ["show"]:
        result = api("setChatMenuButton", {"chat_id": CHAT_ID, "menu_button": {"type": "web_app", "text": "Дневник", "web_app": {"url": URL}}})
        print("set:", result.get("ok"), result.get("description", ""))
    button = api("getChatMenuButton", {"chat_id": CHAT_ID})["result"]
    print("menu button:", button.get("type"), button.get("text"), (button.get("web_app") or {}).get("url"))
