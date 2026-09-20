from __future__ import annotations

import json

import pytest

from nutricore import rest_sheets


class Transport:
    def __init__(self, replies=None):
        self.calls = []
        self.replies = list(replies or [])

    def __call__(self, method, path, body, headers):
        self.calls.append((method, path, body, dict(headers)))
        status, payload = self.replies.pop(0) if self.replies else (200, {})
        return status, json.dumps(payload).encode()


def service(transport):
    return rest_sheets.RestSheets(authorize=lambda headers: headers.update({"Authorization": "Bearer test"}), transport=transport)


def test_values_get_and_batch_get_build_encoded_urls():
    t = Transport([(200, {"values": [["a"]]}), (200, {"valueRanges": []})])
    s = service(t)
    assert s.spreadsheets().values().get(spreadsheetId="ID", range="'Питание'!A:T", valueRenderOption="UNFORMATTED_VALUE").execute() == {"values": [["a"]]}
    s.spreadsheets().values().batchGet(spreadsheetId="ID", ranges=["'Питание'!A:T", "'Продукты'!A:P"], valueRenderOption="UNFORMATTED_VALUE",
                                       dateTimeRenderOption="FORMATTED_STRING").execute()
    method, path, body, headers = t.calls[0]
    assert method == "GET" and body is None and headers["Authorization"] == "Bearer test"
    assert path == "/v4/spreadsheets/ID/values/%27%D0%9F%D0%B8%D1%82%D0%B0%D0%BD%D0%B8%D0%B5%27%21A%3AT?valueRenderOption=UNFORMATTED_VALUE"
    assert t.calls[1][1].startswith("/v4/spreadsheets/ID/values:batchGet?ranges=%27") and "&ranges=%27" in t.calls[1][1]
    assert t.calls[1][1].endswith("valueRenderOption=UNFORMATTED_VALUE&dateTimeRenderOption=FORMATTED_STRING")


def test_writes_send_json_bodies():
    t = Transport([(200, {"updates": {"updatedRange": "'Питание'!A5:T5"}}), (200, {}), (200, {}), (200, {})])
    v = service(t).spreadsheets().values()
    v.append(spreadsheetId="ID", range="'Питание'!A:T", valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS", body={"values": [[1]]}).execute()
    v.update(spreadsheetId="ID", range="'Питание'!A5:T5", valueInputOption="USER_ENTERED", body={"values": [[2]]}).execute()
    v.batchUpdate(spreadsheetId="ID", body={"valueInputOption": "USER_ENTERED", "data": []}).execute()
    service(t).spreadsheets().batchUpdate(spreadsheetId="ID", body={"requests": []}).execute()
    assert [(c[0], c[1].split("?")[0].rsplit("/", 1)[-1]) for c in t.calls] == [
        ("POST", "%27%D0%9F%D0%B8%D1%82%D0%B0%D0%BD%D0%B8%D0%B5%27%21A%3AT:append"), ("PUT", "%27%D0%9F%D0%B8%D1%82%D0%B0%D0%BD%D0%B8%D0%B5%27%21A5%3AT5"),
        ("POST", "values:batchUpdate"), ("POST", "ID:batchUpdate")]
    assert "valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS" in t.calls[0][1]
    assert json.loads(t.calls[0][2]) == {"values": [[1]]}


def test_spreadsheet_get_and_errors():
    t = Transport([(200, {"sheets": []}), (429, {"error": {"message": "Quota exceeded"}})])
    s = service(t)
    assert s.spreadsheets().get(spreadsheetId="ID", fields="sheets(properties(sheetId,title))").execute() == {"sheets": []}
    assert t.calls[0][1] == "/v4/spreadsheets/ID?fields=sheets%28properties%28sheetId%2Ctitle%29%29"
    with pytest.raises(rest_sheets.SheetsHttpError) as error:
        s.spreadsheets().values().get(spreadsheetId="ID", range="A1").execute()
    assert error.value.status == 429 and "Quota" in str(error.value)
