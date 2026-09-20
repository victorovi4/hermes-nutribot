"""Smoke-check the deployed app: page, sign-in wall, and a full add → edit → delete cycle on a far-future date."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

def _app_url() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    if os.environ.get("NUTRI_APP_URL"):
        return os.environ["NUTRI_APP_URL"]
    for name in (os.environ.get("NUTRI_APP_URL_FILE", ""), "app-url.txt"):
        if name:
            path = Path(name) if Path(name).is_absolute() else Path(__file__).resolve().parent.parent / name
            if path.exists():
                return path.read_text(encoding="utf-8")
    raise SystemExit("не знаю адрес приложения: передай его первым аргументом или задай NUTRI_APP_URL")


BASE = _app_url().strip().rstrip("/")
ENV = Path(os.environ.get("NUTRI_CLOUD_ENV") or Path(__file__).with_name("cloud.env"))
values = dict(line.strip().split("=", 1) for line in ENV.read_text().splitlines()
              if "=" in line and not line.strip().startswith("#")) if ENV.exists() else {}
values = {**values, **{k: v for k, v in os.environ.items() if k.startswith(("TELEGRAM_", "NUTRI_"))}}
TOKEN = values["TELEGRAM_BOT_TOKEN"].strip("\"'")
USER_ID = int(values["TELEGRAM_ALLOWED_USERS"].strip("\"'").split(",")[0])
TEST_DATE = "01.01.2030"


def init_data(user_id=USER_ID, token=TOKEN):
    fields = {"auth_date": str(int(time.time())), "query_id": "check", "user": json.dumps({"id": user_id, "first_name": "check"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields)


def call(method, path, body=None, auth=None):
    """One HTTP call through curl, so NUTRI_SOCKS=127.0.0.1:1080 can route it via an SSH tunnel in Russia.

    The gateway address refuses connections from abroad, and this Mac usually sits behind a foreign VPN.
    """
    import subprocess
    command = ["curl", "-s", "-m", "60", "-X", method, "-w", "\n%{http_code} %{time_total}", "-H", "Content-Type: application/json"]
    if os.environ.get("NUTRI_SOCKS"):
        command += ["--socks5-hostname", os.environ["NUTRI_SOCKS"]]
    if auth:
        command += ["-H", "X-Telegram-Init-Data: " + auth]
    if body is not None:
        command += ["--data-binary", json.dumps(body, ensure_ascii=False)]
    output = subprocess.run(command + [BASE + path], capture_output=True).stdout.decode("utf-8", errors="replace")   # fonts are binary
    raw, _, tail = output.rpartition("\n")
    status, took = (tail.split() + ["0", "0"])[:2]
    try:
        return int(status), json.loads(raw), float(took)
    except ValueError:
        return int(status), raw, float(took)


def main() -> int:
    ok = True
    def check(name, condition, detail=""):
        nonlocal ok
        ok = ok and bool(condition)
        print(("ok  " if condition else "FAIL"), name, detail)

    status, page, took = call("GET", "/")
    check("page opens, styles and script are inside it", status == 200 and "Дневник питания" in page and "Нутрибот: приложение" in page
          and "<style>" in page, f"{took:.1f}s")
    status, body, _ = call("GET", "/telegram-web-app.js")
    check("own copy of the Telegram script is served", status == 200 and "WebApp" in body)
    status, body, _ = call("GET", "/fonts/golos-text-cyrillic.woff2")
    check("fonts are served", status == 200)
    status, body, _ = call("GET", f"/api/day?date={TEST_DATE}")
    check("no signature -> 401", status == 401)
    status, body, _ = call("GET", f"/api/day?date={TEST_DATE}", auth=init_data(token="1:forged"))
    check("forged signature -> 401", status == 401)
    status, body, _ = call("GET", f"/api/day?date={TEST_DATE}", auth=init_data(user_id=1))
    check("other user -> 403", status == 403)

    auth = init_data()
    status, day, took = call("GET", "/api/day?date=18.09.2026", auth=auth)
    check("signed day request", status == 200 and day["totals"]["kcal"] > 0, f"18.09 = {day.get('totals')} in {took:.1f}s" if status == 200 else str(day))
    status, boot, took = call("GET", "/api/bootstrap?date=18.09.2026", auth=auth)
    check("bootstrap (day + catalog in one call)", status == 200 and boot["day"]["totals"] == day["totals"] and len(boot["catalog"]["products"]) >= 60, f"in {took:.1f}s")
    status, stats, took = call("GET", "/api/stats?from=14.09.2026&to=20.09.2026", auth=auth)
    check("statistics for a week", status == 200 and len(stats["days"]) == 7 and stats["summary"]["days_logged"] >= 5,
          f"{stats['summary']['kcal_in']} of {stats['summary']['days_counted']} days in goal, in {took:.1f}s" if status == 200 else str(stats))
    status, catalog, took = call("GET", "/api/catalog", auth=auth)
    check("catalog", status == 200 and len(catalog["products"]) >= 60, f"{len(catalog.get('products', []))} products, {len(catalog.get('sets', []))} sets in {took:.1f}s")
    product = next(p for p in catalog["products"] if p["name"].startswith("Зефир в шоколаде"))
    record_id = "r" + os.urandom(4).hex()
    item = {"date": TEST_DATE, "meal": "Ужин", "items": [{"record_id": record_id, "product_id": product["product_id"], "quantity": 2, "unit": "штука"}]}
    status, added, took = call("POST", "/api/entries", item, auth=auth)
    check("add", status == 200 and added["entries"][0]["kcal"] == 184.0, f"{added['entries'][0]['portion'] if status == 200 else added} in {took:.1f}s")
    status, again, _ = call("POST", "/api/entries", item, auth=auth)
    check("repeat adds nothing", status == 200 and len(again["day"]["entries"]) == len(added["day"]["entries"]))
    status, patched, took = call("PATCH", f"/api/entries/{record_id}", {"quantity": 1}, auth=auth)
    check("edit", status == 200 and patched["entry"]["kcal"] == 92.0, f"in {took:.1f}s")
    status, deleted, took = call("DELETE", f"/api/entries/{record_id}", auth=auth)
    check("delete", status == 200 and deleted["deleted"] == record_id, f"in {took:.1f}s")
    status, after, _ = call("GET", f"/api/day?date={TEST_DATE}", auth=auth)
    check("test date is clean again", status == 200 and not any(e["record_id"] == record_id for e in after["entries"]),
          "" if status == 200 else f"HTTP {status}: {after}")
    status, found, took = call("GET", "/api/vkusvill?q=" + urllib.parse.quote("кефир"), auth=auth)
    check("vkusvill search", status == 200 and len(found["items"]) > 0, f"in {took:.1f}s")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
