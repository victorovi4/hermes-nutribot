"""A small Google Sheets client over plain HTTPS, shaped like the part of googleapiclient that the store uses.

The cloud function needs a fast cold start: importing googleapiclient costs over a second and ~290 MB, and
exchanging a service-account key for an access token costs one more slow round trip to Google. Here requests
are signed with a self-signed JWT (no token exchange) and sent through http.client.
"""
from __future__ import annotations

import gzip
import http.client
import json
import threading
from typing import Any, Callable
from urllib.parse import quote, urlencode

HOST = "sheets.googleapis.com"
AUDIENCE = f"https://{HOST}/"
_RETRIABLE = (http.client.RemoteDisconnected, http.client.CannotSendRequest, ConnectionError, BrokenPipeError, TimeoutError, OSError)


class SheetsHttpError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"Google Sheets answered {status}: {body[:300]}")
        self.status = status


_idle: list[http.client.HTTPSConnection] = []          # keep-alive connections, shared by every thread of the instance
_idle_lock = threading.Lock()


stats = {"reused": 0, "fresh": 0}                       # how often a request could skip the TLS handshake


def _take_connection(fresh: bool) -> http.client.HTTPSConnection:
    if not fresh:
        with _idle_lock:
            if _idle:
                stats["reused"] += 1
                return _idle.pop()
    stats["fresh"] += 1
    return http.client.HTTPSConnection(HOST, timeout=20)


def _https_transport(method: str, path: str, body: bytes | None, headers: dict[str, str]) -> tuple[int, bytes]:
    """Reads reuse a keep-alive connection (a TLS handshake to Google costs about as much as the request itself)
    and are retried once if that connection went stale. Writes always get a fresh connection, so a request is
    never silently sent twice."""
    attempts = 2 if method == "GET" else 1
    for attempt in range(attempts):
        connection = _take_connection(fresh=method != "GET" or attempt > 0)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            if response.getheader("Content-Encoding", "").lower() == "gzip":
                raw = gzip.decompress(raw)
            result = response.status, raw
        except _RETRIABLE:
            connection.close()
            if attempt == attempts - 1:
                raise
            continue
        with _idle_lock:
            if len(_idle) < 3:
                _idle.append(connection)
            else:
                connection.close()
        return result
    raise AssertionError("unreachable")


def jwt_authorizer(service_account_info: dict[str, Any]) -> Callable[[dict[str, str]], None]:
    from google.auth import jwt

    credentials = jwt.Credentials.from_service_account_info(service_account_info, audience=AUDIENCE)

    def authorize(headers: dict[str, str]) -> None:
        credentials.before_request(None, "GET", AUDIENCE, headers)      # signs locally; refreshes itself hourly

    return authorize


class _Call:
    def __init__(self, service: "RestSheets", method: str, path: str, params: list[tuple[str, str]], body: Any = None):
        self._service, self._method, self._body = service, method, body
        self._path = path + ("?" + urlencode(params, quote_via=quote) if params else "")

    def execute(self) -> dict[str, Any]:
        # Google compresses API responses only when both of these say "gzip"; the diary shrinks several times.
        headers = {"Accept": "application/json", "Accept-Encoding": "gzip", "User-Agent": "nutribot-app (gzip)"}
        payload = None
        if self._body is not None:
            payload = json.dumps(self._body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        self._service.authorize(headers)
        status, raw = self._service.transport(self._method, self._path, payload, headers)
        text = raw.decode("utf-8", errors="replace")
        if not 200 <= status < 300:
            raise SheetsHttpError(status, text)
        return json.loads(text) if text.strip() else {}


def _options(**options: Any) -> list[tuple[str, str]]:
    return [(key, str(value)) for key, value in options.items() if value is not None]


class _Values:
    def __init__(self, service: "RestSheets"):
        self._service = service

    def _base(self, spreadsheet_id: str) -> str:
        return f"/v4/spreadsheets/{quote(spreadsheet_id, safe='')}/values"

    def get(self, *, spreadsheetId, range, valueRenderOption=None, dateTimeRenderOption=None):
        return _Call(self._service, "GET", f"{self._base(spreadsheetId)}/{quote(range, safe='')}",
                     _options(valueRenderOption=valueRenderOption, dateTimeRenderOption=dateTimeRenderOption))

    def batchGet(self, *, spreadsheetId, ranges, valueRenderOption=None, dateTimeRenderOption=None):
        params = [("ranges", r) for r in ranges] + _options(valueRenderOption=valueRenderOption, dateTimeRenderOption=dateTimeRenderOption)
        return _Call(self._service, "GET", f"{self._base(spreadsheetId)}:batchGet", params)

    def append(self, *, spreadsheetId, range, valueInputOption, insertDataOption=None, body):
        return _Call(self._service, "POST", f"{self._base(spreadsheetId)}/{quote(range, safe='')}:append",
                     _options(valueInputOption=valueInputOption, insertDataOption=insertDataOption), body)

    def update(self, *, spreadsheetId, range, valueInputOption, body):
        return _Call(self._service, "PUT", f"{self._base(spreadsheetId)}/{quote(range, safe='')}",
                     _options(valueInputOption=valueInputOption), body)

    def batchUpdate(self, *, spreadsheetId, body):
        return _Call(self._service, "POST", f"{self._base(spreadsheetId)}:batchUpdate", [], body)


class RestSheets:
    def __init__(self, authorize: Callable[[dict[str, str]], None], transport: Callable[..., tuple[int, bytes]] = _https_transport):
        self.authorize = authorize
        self.transport = transport

    def spreadsheets(self) -> "RestSheets":
        return self

    def values(self) -> _Values:
        return _Values(self)

    def get(self, *, spreadsheetId, fields=None):
        return _Call(self, "GET", f"/v4/spreadsheets/{quote(spreadsheetId, safe='')}", _options(fields=fields))

    def batchUpdate(self, *, spreadsheetId, body):
        return _Call(self, "POST", f"/v4/spreadsheets/{quote(spreadsheetId, safe='')}:batchUpdate", [], body)
