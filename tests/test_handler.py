from __future__ import annotations

import base64
import json

import pytest

from api import handler as handler_mod
from nutricore import catalog
from tests.fakes import FULL_HEADERS, MemoryStore
from tests.test_auth import NOW, TOKEN, signed

DATE = "19.09.2026"


@pytest.fixture
def env(tmp_path):
    store = MemoryStore(headers=FULL_HEADERS)
    catalog.upsert_product(store, {"name": "Зефир", "base": "1 шт", "kcal": 30, "protein_g": 0.6, "fat_g": 0, "carbs_g": 6.7},
                           origin="миграция", today=DATE, id_factory=lambda p: p + "0000001")
    web = tmp_path / "web"; (web / "fonts").mkdir(parents=True)
    (web / "index.html").write_text("<h1>Нутрибот</h1>", encoding="utf-8")
    (web / "app.js").write_text("console.log(1)", encoding="utf-8")
    (web / "fonts" / "a.woff2").write_bytes(b"\x00\x01binary")
    handle = handler_mod.make_handler(store_factory=lambda: store, bot_token=TOKEN, allowed_ids={111222333}, web_dir=web,
                                      vkusvill_search=lambda q: [{"name": q, "new_product": {}}], clock=lambda: NOW)
    return store, handle


def call(handle, method, url, body=None, auth=True, encoded=False):
    headers = {"X-Telegram-Init-Data": signed()} if auth else {}
    raw = json.dumps(body, ensure_ascii=False) if body is not None else ""
    event = {"httpMethod": method, "url": url, "headers": headers,
             "body": base64.b64encode(raw.encode()).decode() if encoded else raw, "isBase64Encoded": encoded}
    response = handle(event, None)
    payload = response["body"]
    if response["headers"]["Content-Type"].startswith("application/json"):
        payload = json.loads(payload)
    return response["statusCode"], payload, response


def test_static_files(env):
    _store, handle = env
    status, body, response = call(handle, "GET", "/", auth=False)
    assert status == 200 and "Нутрибот" in body and response["headers"]["Content-Type"].startswith("text/html")
    assert response["headers"]["Cache-Control"] == "no-cache"
    status, _body, response = call(handle, "GET", "/app.js?v=3", auth=False)
    assert status == 200 and "javascript" in response["headers"]["Content-Type"]
    status, _body, response = call(handle, "GET", "/fonts/a.woff2", auth=False)
    assert status == 200 and response["isBase64Encoded"] and base64.b64decode(response["body"]) == b"\x00\x01binary"
    assert "max-age" in response["headers"]["Cache-Control"]
    assert call(handle, "GET", "/../api/handler.py", auth=False)[0] == 404
    assert call(handle, "GET", "/nope.css", auth=False)[0] == 404


def test_api_requires_a_valid_signature(env):
    _store, handle = env
    status, body, _ = call(handle, "GET", f"/api/day?date={DATE}", auth=False)
    assert status == 401 and body["error"] == "unauthorized"


def test_full_entry_lifecycle(env):
    store, handle = env
    status, body, _ = call(handle, "POST", "/api/entries", {"date": DATE, "meal": "Перекус после обеда", "items": [
        {"record_id": "r00000abc", "product_id": "p0000001", "quantity": 2, "unit": "штука"}]}, encoded=True)
    assert status == 200 and body["entries"][0]["kcal"] == 60.0 and body["day"]["totals"]["kcal"] == 60.0
    status, body, _ = call(handle, "GET", f"/api/day?date={DATE}")
    assert status == 200 and [e["record_id"] for e in body["entries"]] == ["r00000abc"] and body["targets"]["kcal"] == [1800.0, 1900.0]
    status, body, _ = call(handle, "PATCH", "/api/entries/r00000abc", {"quantity": 1})
    assert status == 200 and body["entry"]["kcal"] == 30.0
    status, body, _ = call(handle, "POST", "/api/sets", {"set_name": "Сладкое", "record_ids": ["r00000abc"]})
    assert status == 200 and body["set"]["set_name"] == "Сладкое"
    status, body, _ = call(handle, "GET", "/api/catalog")
    assert status == 200 and body["products"][0]["name"] == "Зефир" and body["sets"][0]["set_name"] == "Сладкое"
    status, body, _ = call(handle, "DELETE", "/api/entries/r00000abc")
    assert status == 200 and body["deleted"] == "r00000abc" and store.food_rows == []


def test_errors_map_to_status_codes(env):
    store, handle = env
    assert call(handle, "PATCH", "/api/entries/r99999999", {"quantity": 1})[0] == 404
    assert call(handle, "POST", "/api/entries", {"date": DATE, "meal": "Обед", "items": []})[0] == 400
    assert call(handle, "POST", "/api/entries", {"date": "вчера", "meal": "Обед", "items": [{"product_id": "p0000001", "quantity": 1, "unit": "штука"}]})[0] == 400
    assert call(handle, "GET", "/api/nothing")[0] == 404
    assert call(handle, "GET", "/api/vkusvill?q=%D1%82%D0%B2%D0%BE%D1%80%D0%BE%D0%B3")[1]["items"][0]["name"] == "творог"
    def boom():
        raise RuntimeError("sheets down")
    store.read_food = boom
    status, body, _ = call(handle, "GET", f"/api/day?date={DATE}")
    assert status == 502 and "Таблица" in body["message"]


def test_bootstrap_returns_day_and_catalog_together(env):
    store, handle = env
    call(handle, "POST", "/api/entries", {"date": DATE, "meal": "Ужин", "items": [{"record_id": "r00000abc", "product_id": "p0000001", "quantity": 2, "unit": "штука"}]})
    status, body, response = call(handle, "GET", f"/api/bootstrap?date={DATE}")
    assert status == 200 and body["day"]["totals"]["kcal"] == 60.0 and body["catalog"]["products"][0]["name"] == "Зефир"
    assert "X-Nutri-Timing" in response["headers"]


def test_timer_events_keep_the_function_warm_without_touching_the_table(env):
    store, handle = env
    store.read_food = lambda: (_ for _ in ()).throw(AssertionError("must not read"))
    assert handle({"messages": [{"event_metadata": {"event_type": "yandex.cloud.events.serverless.triggers.TimerMessage"}}]}, None)["statusCode"] == 200


def test_stats_route(env):
    _store, handle = env
    call(handle, "POST", "/api/entries", {"date": DATE, "meal": "Ужин", "items": [{"record_id": "r00000abc", "product_id": "p0000001", "quantity": 2, "unit": "штука"}]})
    status, body, _ = call(handle, "GET", "/api/stats?from=14.09.2026&to=20.09.2026")
    assert status == 200 and len(body["days"]) == 7 and body["summary"]["days_logged"] == 1
    assert call(handle, "GET", "/api/stats?from=20.09.2026&to=14.09.2026")[0] == 400


def test_the_same_handler_serves_as_a_plain_web_server(env, monkeypatch):
    """deploy/serve.py must keep working: the app on a server is the cloud handler behind a proxy."""
    store, _handle = env
    import importlib.util
    from pathlib import Path
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "111222333")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("NUTRI_STORAGE", "sqlite")
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("nutribot_serve", root / "deploy" / "serve.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.allowed == {111222333}
    response = module.handle({"httpMethod": "GET", "url": "/api/day?date=" + DATE, "headers": {}, "body": ""}, None)
    assert response["statusCode"] == 401                               # no signature — no data, same as in the cloud
