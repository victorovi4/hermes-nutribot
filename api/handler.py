"""Yandex Cloud Function entry point: serves the app page and its JSON API."""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
for extra in (ROOT,):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from api.auth import AuthError, verify_init_data  # noqa: E402
from nutricore import app, vkusvill  # noqa: E402
from nutricore.cache import CachingStore  # noqa: E402

INSTANCE = os.urandom(2).hex()                          # tells instances apart in the timing header
TEXT_TYPES = ("text/", "application/javascript", "application/json", "image/svg+xml")
JSON_HEADERS = {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}


def _json(status: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {"statusCode": status, "headers": dict(JSON_HEADERS), "isBase64Encoded": False,
            "body": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}


def _error(status: int, code: str, message: str) -> dict[str, Any]:
    return _json(status, {"error": code, "message": message})


def _static(web_dir: Path, path: str) -> dict[str, Any]:
    relative = "index.html" if path in ("", "/") else unquote(path).lstrip("/")
    target = (web_dir / relative).resolve()
    if web_dir.resolve() not in target.parents or not target.is_file():
        return _error(404, "not_found", "нет такого файла")
    mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    if target.suffix == ".js":
        mime = "application/javascript"
    if target.suffix == ".woff2":
        mime = "font/woff2"
    is_text = mime.startswith(TEXT_TYPES)
    data = target.read_bytes()
    headers = {"Content-Type": mime + ("; charset=utf-8" if is_text else ""),
               "Cache-Control": "public, max-age=31536000, immutable" if relative.startswith("fonts/") else "no-cache"}
    return {"statusCode": 200, "headers": headers, "isBase64Encoded": not is_text,
            "body": data.decode("utf-8") if is_text else base64.b64encode(data).decode("ascii")}


def make_handler(*, store_factory: Callable[[], Any], bot_token: str, allowed_ids: set[int], web_dir: Path,
                 vkusvill_search: Callable[[str], list] = vkusvill.search, clock: Callable[[], float] = time.time,
                 auth_disabled: bool = False):
    def route(store_factory, method: str, path: str, query: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        parts = [p for p in path.split("/") if p][1:]                      # drop the leading "api"
        if parts != ["vkusvill"]:
            store_factory().preload()                                       # one spreadsheet request serves the whole call
        if method == "GET" and parts == ["bootstrap"]:
            store = store_factory()
            return {"day": app.day_view(store, query.get("date", "")), "catalog": app.catalog_view(store)}
        if method == "GET" and parts == ["stats"]:
            return app.stats_view(store_factory(), query.get("from", ""), query.get("to", ""))
        if method == "GET" and parts == ["day"]:
            return app.day_view(store_factory(), query.get("date", ""))
        if method == "GET" and parts == ["catalog"]:
            return app.catalog_view(store_factory())
        if method == "GET" and parts == ["vkusvill"]:
            return {"items": vkusvill_search(query.get("q", ""))}
        if method == "POST" and parts == ["entries"]:
            return app.add_entries(store_factory(), date=body.get("date", ""), meal=str(body.get("meal") or ""),
                                   items=body.get("items") or [], set_name=str(body.get("set_name") or ""),
                                   remember_set=bool(body.get("remember_set")))
        if method == "PATCH" and len(parts) == 2 and parts[0] == "entries":
            return app.update_entry(store_factory(), parts[1], body)
        if method == "DELETE" and len(parts) == 2 and parts[0] == "entries":
            return app.delete_entry(store_factory(), parts[1])
        if method == "POST" and parts == ["sets"]:
            if body.get("record_ids"):
                return {"set": app.save_meal_as_set(store_factory(), body.get("set_name", ""), body["record_ids"])}
            from nutricore import catalog
            catalog.save_set(store_factory(), body.get("set_name", ""), body.get("items") or [])
            return {"set": next(s for s in catalog.find_sets(store_factory(), "")
                                if s["set_name"].casefold() == " ".join(str(body.get("set_name")).split()).casefold())}
        raise LookupError("нет такого адреса")

    shared_cache: dict[str, Any] = {}
    real_store_factory = store_factory

    def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
        request_store: dict[str, Any] = {}

        def per_request_store():                        # one caching view of the spreadsheet per request
            if "s" not in request_store:
                request_store["s"] = CachingStore(real_store_factory(), shared_cache)
            return request_store["s"]

        started = time.perf_counter()
        response = _handle(event, per_request_store)
        calls = request_store["s"].timings if "s" in request_store else []
        from nutricore import rest_sheets
        response["headers"]["X-Nutri-Timing"] = "total=%.0fms; sheets=%.0fms; %s; instance=%s conn reused/fresh=%d/%d" % (
            (time.perf_counter() - started) * 1000, sum(t for _n, t in calls) * 1000,
            ", ".join("%s=%.0f" % (name, seconds * 1000) for name, seconds in calls),
            INSTANCE, rest_sheets.stats["reused"], rest_sheets.stats["fresh"])
        return response

    def _handle(event: dict[str, Any], store_factory) -> dict[str, Any]:
        if "httpMethod" not in event and "messages" in event:              # timer trigger: keeps an instance warm
            return {"statusCode": 200, "headers": {}, "isBase64Encoded": False, "body": "warm"}
        method = str(event.get("httpMethod") or "GET").upper()
        url = urlsplit(str(event.get("url") or event.get("path") or "/"))
        path = url.path or "/"
        if not path.startswith("/api/"):
            return _static(web_dir, path) if method == "GET" else _error(405, "method_not_allowed", "только GET")
        headers = {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}
        try:
            if not auth_disabled:
                verify_init_data(str(headers.get("x-telegram-init-data") or ""), bot_token, allowed_ids, now=clock())
            query = {k: v[-1] for k, v in parse_qs(url.query).items()}
            query.update({k: v for k, v in (event.get("queryStringParameters") or {}).items() if k not in query})
            raw = event.get("body") or ""
            if raw and event.get("isBase64Encoded"):
                raw = base64.b64decode(raw).decode("utf-8")
            body = json.loads(raw) if raw else {}
            if not isinstance(body, dict):
                raise ValueError("тело запроса должно быть объектом")
            return _json(200, route(store_factory, method, path, query, body))
        except AuthError as exc:
            return _error(exc.status, "unauthorized" if exc.status == 401 else "forbidden", str(exc))
        except app.ConflictError as exc:
            return _error(409, "conflict", str(exc))
        except LookupError as exc:
            return _error(404, "not_found", str(exc))
        except (ValueError, TypeError, KeyError) as exc:
            return _error(400, "bad_request", str(exc))
        except vkusvill.VkusvillError as exc:
            return _error(502, "vkusvill_unavailable", str(exc))
        except Exception:                                                   # Sheets, network and everything unexpected
            traceback.print_exc()
            return _error(502, "storage_unavailable", "Таблица не ответила, ничего не записано. Попробуй ещё раз.")

    return handler


_production = None


def handler(event, context):
    """Production entry point: configuration comes from the function's environment."""
    global _production
    if _production is None:
        from nutricore.store import open_store

        state: dict[str, Any] = {}

        def store_factory():
            if "store" not in state:
                state["store"] = open_store(os.environ, fast=True)
            return state["store"]

        allowed = {int(v) for v in os.environ.get("TELEGRAM_ALLOWED_USERS", "").replace(";", ",").split(",") if v.strip()}
        _production = make_handler(store_factory=store_factory, bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
                                   allowed_ids=allowed, web_dir=ROOT / "web")
    return _production(event, context)
