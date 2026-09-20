from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from nutricore.schema import (
    FOOD_BASE_HEADERS,
    FOOD_EXTRA_HEADERS,
    FOOD_TAB,
    NUMERIC_FOOD_HEADERS,
    NUMERIC_PRODUCT_HEADERS,
    NUMERIC_SET_HEADERS,
    PRODUCT_HEADERS,
    PRODUCTS_TAB,
    SET_HEADERS,
    SETS_TAB,
)

DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]


FOOD_RANGE = f"'{FOOD_TAB}'!A:T"
FOOD_LAST_COLUMN = "T"


def _norm_header(value: Any) -> str:
    return str(value or "").strip().casefold().replace("ё", "е")


def _numeric_indexes(headers: list[Any], names: set[str]) -> list[int]:
    wanted = {_norm_header(name) for name in names}
    return [index for index, header in enumerate(headers) if _norm_header(header) in wanted]


def _merge_numeric(text_row: list[Any], raw_row: list[Any], width: int, numeric: list[int]) -> list[Any]:
    """Keep formatted text, but take numeric cells from the unformatted read."""
    row = list(text_row)
    if len(row) < width:
        row.extend([""] * (width - len(row)))
    for index in numeric:
        if index < len(raw_row) and raw_row[index] != "":
            row[index] = raw_row[index]
    return row


def _num(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, str):
        # Russian locale renders thousands with a plain, no-break or narrow no-break space.
        value = value.replace(" ", "").replace("\u00a0", "").replace("\u202f", "").replace(",", ".")
    return float(value)


def _col_letter(index: int) -> str:
    if not 0 <= index < 26:
        raise ValueError(f"column index out of range: {index}")
    return chr(ord("A") + index)


def _safe_cell(value: Any) -> Any:
    """USER_ENTERED would turn a leading '=' or '+' into a formula; keep such text literal."""
    if isinstance(value, str) and value[:1] in ("=", "+"):
        return "'" + value
    return value


def _appended_rows(result: dict) -> list[int]:
    updated = (result.get("updates") or {}).get("updatedRange", "")
    match = re.search(r"!A(\d+):[A-Z]+(\d+)$", updated)
    if not match:
        raise RuntimeError(f"Cannot parse appended range: {updated}")
    return list(range(int(match.group(1)), int(match.group(2)) + 1))


def service_account_info() -> dict[str, Any] | None:
    """The cloud's service-account key (it can open this one spreadsheet only), or None on the Mac."""
    sa_key = os.environ.get("GOOGLE_SA_KEY") or ""
    if not sa_key and os.environ.get("GOOGLE_SA_KEY_B64"):           # plain env vars cannot hold commas safely
        import base64

        sa_key = base64.b64decode(os.environ["GOOGLE_SA_KEY_B64"]).decode("utf-8")
    return json.loads(sa_key) if sa_key else None


def _default_credentials():
    info = service_account_info()
    if info:
        from google.oauth2 import service_account

        return service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/spreadsheets"])

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    home = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
    token_path = home / "google_token.json"
    if not token_path.exists():
        raise RuntimeError("Google token is missing")
    payload = json.loads(token_path.read_text(encoding="utf-8"))
    scopes = payload.get("scopes") or DEFAULT_SCOPES
    creds = Credentials.from_authorized_user_file(str(token_path), scopes)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        refreshed = json.loads(creds.to_json())
        refreshed.setdefault("type", "authorized_user")
        token_path.write_text(json.dumps(refreshed, indent=2), encoding="utf-8")
    if not creds.valid:
        raise RuntimeError("Google token is invalid")
    return creds


def _default_service():
    from googleapiclient.discovery import build

    return build("sheets", "v4", credentials=_default_credentials(), cache_discovery=False)


def fast_service():
    """The light REST client used in the cloud: signs requests itself, no heavy Google libraries."""
    from nutricore.rest_sheets import RestSheets, jwt_authorizer

    return RestSheets(jwt_authorizer(service_account_info()))


class GoogleSheetStore:
    def __init__(self, service=None, spreadsheet_id: str = "", single_read: bool = False):
        if not str(spreadsheet_id or "").strip():
            raise ValueError("нужен номер Google-таблицы дневника (NUTRI_SPREADSHEET_ID)")
        self.service = service or _default_service()
        self.spreadsheet_id = spreadsheet_id
        # single_read: one request per read (raw numbers + dates as displayed) instead of a formatted and an
        # unformatted one. The app uses it; the bot keeps the two-request read it has always had.
        self.single_read = single_read
        self._sheet_id_cache: dict[str, int] | None = None

    @property
    def values(self):
        return self.service.spreadsheets().values()

    def _get(self, range_name: str) -> list[list[Any]]:
        result = self.values.get(
            spreadsheetId=self.spreadsheet_id,
            range=range_name,
            valueRenderOption="FORMATTED_VALUE",
        ).execute()
        return result.get("values", [])

    def _single(self, range_name: str) -> list[list[Any]]:
        return self.values.get(
            spreadsheetId=self.spreadsheet_id, range=range_name,
            valueRenderOption="UNFORMATTED_VALUE", dateTimeRenderOption="FORMATTED_STRING",
        ).execute().get("values", [])

    @staticmethod
    def _table(values: list[list[Any]]):
        headers = values[0] if values else []
        return headers, [list(row) + [""] * (len(headers) - len(row)) for row in values[1:]]

    def read_food(self):
        if self.single_read:
            values = self._single(FOOD_RANGE)
            if not values:
                raise RuntimeError("Питание: header row is missing")
            return self._table(values)
        formatted = self.values.get(
            spreadsheetId=self.spreadsheet_id,
            range=FOOD_RANGE,
            valueRenderOption="FORMATTED_VALUE",
        ).execute().get("values", [])
        unformatted = self.values.get(
            spreadsheetId=self.spreadsheet_id,
            range=FOOD_RANGE,
            valueRenderOption="UNFORMATTED_VALUE",
        ).execute().get("values", [])
        if not formatted:
            raise RuntimeError("Питание: header row is missing")
        headers = formatted[0]
        numeric = _numeric_indexes(headers, NUMERIC_FOOD_HEADERS)
        rows = []
        for index, text_row in enumerate(formatted[1:], start=1):
            raw = unformatted[index] if index < len(unformatted) else []
            rows.append(_merge_numeric(text_row, raw, len(headers), numeric))
        return headers, rows

    def food_row_numbers(self) -> list[int]:
        """In a spreadsheet the number is the position: row 1 is the header, so data starts at 2."""
        return list(range(2, len(self.read_food()[1]) + 2))

    def read_snapshot(self) -> dict[str, Any]:
        """Everything the app shows, in one request: diary, products, sets and targets."""
        ranges = [FOOD_RANGE, f"'{PRODUCTS_TAB}'!A:{_col_letter(len(PRODUCT_HEADERS) - 1)}",
                  f"'{SETS_TAB}'!A:{_col_letter(len(SET_HEADERS) - 1)}", "'Справочник'!A:B"]
        blocks = self.values.batchGet(
            spreadsheetId=self.spreadsheet_id, ranges=ranges,
            valueRenderOption="UNFORMATTED_VALUE", dateTimeRenderOption="FORMATTED_STRING",
        ).execute().get("valueRanges", [])
        food, products, sets, reference = ([block.get("values", []) for block in blocks] + [[]] * 4)[:4]
        if not food:
            raise RuntimeError("Питание: header row is missing")
        return {"food": self._table(food), "products": self._table(products), "sets": self._table(sets),
                "targets": self._targets_from(reference)}

    def append_food(self, rows):
        result = self.values.append(
            spreadsheetId=self.spreadsheet_id,
            range=FOOD_RANGE,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": rows},
        ).execute()
        return _appended_rows(result)

    def update_food(self, row_number, row):
        self.values.update(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{FOOD_TAB}'!A{int(row_number)}:{FOOD_LAST_COLUMN}{int(row_number)}",
            valueInputOption="USER_ENTERED",
            body={"values": [row]},
        ).execute()

    def read_food_rows(self, row_numbers):
        row_numbers = [int(n) for n in row_numbers]
        # Row 1 rides along so numeric columns are found by header, not by position.
        ranges = [f"'{FOOD_TAB}'!A{n}:{FOOD_LAST_COLUMN}{n}" for n in [1] + row_numbers]
        if self.single_read:
            blocks = self.values.batchGet(
                spreadsheetId=self.spreadsheet_id, ranges=ranges,
                valueRenderOption="UNFORMATTED_VALUE", dateTimeRenderOption="FORMATTED_STRING",
            ).execute().get("valueRanges", [])
            if not blocks:
                return {}
            width = max(len((blocks[0].get("values") or [[]])[0]), 13)
            return {number: (list(block["values"][0]) + [""] * width)[:width]
                    for number, block in zip(row_numbers, blocks[1:]) if block.get("values")}
        formatted = self.values.batchGet(
            spreadsheetId=self.spreadsheet_id,
            ranges=ranges,
            valueRenderOption="FORMATTED_VALUE",
        ).execute().get("valueRanges", [])
        unformatted = self.values.batchGet(
            spreadsheetId=self.spreadsheet_id,
            ranges=ranges,
            valueRenderOption="UNFORMATTED_VALUE",
        ).execute().get("valueRanges", [])
        if not formatted:
            return {}
        headers = (formatted[0].get("values") or [[]])[0]
        numeric = _numeric_indexes(headers, NUMERIC_FOOD_HEADERS)
        width = max(len(headers), 13)
        output = {}
        for number, formatted_range, unformatted_range in zip(row_numbers, formatted[1:], unformatted[1:]):
            text_rows = formatted_range.get("values", [])
            if not text_rows:
                continue
            raw_rows = unformatted_range.get("values", [])
            output[number] = _merge_numeric(text_rows[0], raw_rows[0] if raw_rows else [], width, numeric)
        return output

    def read_day_total(self, date):
        for row in self._get("'Итоги по дням'!A:E")[1:]:
            if row and str(row[0]).strip() == date:
                padded = list(row) + [0] * (5 - len(row))
                return {"kcal": _num(padded[1]), "protein_g": _num(padded[2]),
                        "fat_g": _num(padded[3]), "carbs_g": _num(padded[4])}
        # A sheet without the totals formulas (a freshly created diary) still owes an answer.
        return self._totals_from_food().get(date, {"kcal": 0.0, "protein_g": 0.0, "fat_g": 0.0, "carbs_g": 0.0})

    def _totals_from_food(self) -> dict[str, dict[str, float]]:
        headers, rows = self.read_food()
        index = {_norm_header(h): i for i, h in enumerate(headers)}
        fields = (("kcal", "ккал"), ("protein_g", "белки, г"), ("fat_g", "жиры, г"), ("carbs_g", "углеводы, г"))
        totals: dict[str, dict[str, float]] = {}
        for row in rows:
            date = str(row[index[_norm_header("Дата")]] if index.get(_norm_header("Дата"), 0) < len(row) else "").strip()
            if not date:
                continue
            entry = totals.setdefault(date, {name: 0.0 for name, _h in fields})
            for name, header in fields:
                position = index.get(_norm_header(header))
                if position is not None and position < len(row):
                    entry[name] += _num(row[position])
        return {date: {k: round(v, 4) for k, v in values.items()} for date, values in totals.items()}

    @staticmethod
    def _targets_from(rows: list[list[Any]]) -> dict[str, str]:
        pairs = {str(row[0]).strip(): str(row[1]).strip() for row in rows[1:] if len(row) >= 2}
        return {
            "energy": pairs.get("Стартовая цель энергии", ""),
            "protein": pairs.get("Белок", ""),
            "fat": pairs.get("Жиры", ""),
            "goal": pairs.get("Первичная цель", ""),
        }

    def read_targets(self):
        return self._targets_from(self._get("'Справочник'!A:B"))

    def find_standards(self, query):
        rows = self._get("'Стандартные порции'!A:G")
        needle = str(query or "").strip().casefold().replace("ё", "е")
        if not needle:
            return rows[1:10]
        found = []
        for row in rows[1:]:
            haystack = " ".join(map(str, row)).casefold().replace("ё", "е")
            if needle in haystack:
                found.append(row)
        return found[:10]

    # --- schema ---------------------------------------------------------
    def _sheet_ids(self) -> dict[str, int]:
        if self._sheet_id_cache is not None:
            return self._sheet_id_cache
        meta = self.service.spreadsheets().get(
            spreadsheetId=self.spreadsheet_id,
            fields="sheets(properties(sheetId,title))",
        ).execute()
        ids = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}
        if all(tab in ids for tab in (FOOD_TAB, PRODUCTS_TAB, SETS_TAB)):      # tabs never get new ids; cache once complete
            self._sheet_id_cache = ids
        return ids

    def ensure_schema(self) -> dict:
        """Add the catalog tabs and the extra diary columns. Idempotent; never touches A–M."""
        food_headers = (self._get(f"'{FOOD_TAB}'!A1:Z1") or [[]])[0]
        known = {_norm_header(h) for h in FOOD_BASE_HEADERS + FOOD_EXTRA_HEADERS}
        foreign = [h for h in food_headers[len(FOOD_BASE_HEADERS):] if _norm_header(h) and _norm_header(h) not in known]
        if foreign:
            raise RuntimeError(f"{FOOD_TAB}: колонка справа занята чужим заголовком: {', '.join(map(str, foreign))}")
        present = {_norm_header(h) for h in food_headers}
        missing = [h for h in FOOD_EXTRA_HEADERS if _norm_header(h) not in present]
        if missing and len(missing) != len(FOOD_EXTRA_HEADERS):
            raise RuntimeError(f"{FOOD_TAB}: новые колонки добавлены частично, нужна ручная проверка: {', '.join(missing)}")

        titles = self._sheet_ids()
        wanted = ((PRODUCTS_TAB, PRODUCT_HEADERS), (SETS_TAB, SET_HEADERS))
        to_create, to_head = [], []
        for title, headers in wanted:
            if title not in titles:
                to_create.append(title)
                to_head.append((title, headers))
                continue
            current = (self._get(f"'{title}'!A1:Z1") or [[]])[0]
            if not current:
                to_head.append((title, headers))
            elif [_norm_header(h) for h in current] != [_norm_header(h) for h in headers]:
                raise RuntimeError(f"{title}: вкладка уже есть, но с другими заголовками")

        if to_create:
            self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": t}}} for t in to_create]},
            ).execute()
        for title, headers in to_head:
            self.values.update(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{title}'!A1:{_col_letter(len(headers) - 1)}1",
                valueInputOption="RAW",
                body={"values": [list(headers)]},
            ).execute()
        if missing:
            first = _col_letter(len(FOOD_BASE_HEADERS))
            self.values.update(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{FOOD_TAB}'!{first}1:{FOOD_LAST_COLUMN}1",
                valueInputOption="RAW",
                body={"values": [list(FOOD_EXTRA_HEADERS)]},
            ).execute()
        return {"created_tabs": to_create, "added_food_headers": missing}

    def write_food_cells(self, row_number: int, cells: dict[str, Any]) -> None:
        """Write single cells of the extra diary columns (N–T) by header name."""
        self.write_food_cells_many([(row_number, cells)])

    def write_food_cells_many(self, updates) -> None:
        """Same, for many rows in one request: [(row_number, {header: value}), ...]."""
        allowed = {_norm_header(h) for h in FOOD_EXTRA_HEADERS}
        bad = sorted({name for _row, cells in updates for name in cells if _norm_header(name) not in allowed})
        if bad:
            raise ValueError(f"only extra diary columns can be written cell-wise, got: {', '.join(bad)}")
        headers = (self._get(f"'{FOOD_TAB}'!A1:{FOOD_LAST_COLUMN}1") or [[]])[0]
        positions = {_norm_header(h): i for i, h in enumerate(headers)}
        data = []
        for row_number, cells in updates:
            for name, value in cells.items():
                index = positions.get(_norm_header(name))
                if index is None:
                    raise RuntimeError(f"{FOOD_TAB}: нет колонки «{name}», сначала ensure_schema")
                data.append({"range": f"'{FOOD_TAB}'!{_col_letter(index)}{int(row_number)}", "values": [[_safe_cell(value)]]})
        if data:
            self.values.batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"valueInputOption": "USER_ENTERED", "data": data},
            ).execute()

    def read_all_day_totals(self) -> dict[str, dict[str, float]]:
        totals: dict[str, dict[str, float]] = {}
        for row in self._get("'Итоги по дням'!A:E")[1:]:
            if not row or not str(row[0]).strip():
                continue
            padded = list(row) + [0] * (5 - len(row))
            totals[str(row[0]).strip()] = {
                "kcal": _num(padded[1]), "protein_g": _num(padded[2]),
                "fat_g": _num(padded[3]), "carbs_g": _num(padded[4]),
            }
        return totals or self._totals_from_food()

    def set_targets(self, targets) -> None:
        """Write the goals into «Справочник», by the same parameter names read_targets looks for."""
        names = {"energy": "Стартовая цель энергии", "protein": "Белок", "fat": "Жиры", "goal": "Первичная цель"}
        rows = self._get("'Справочник'!A:B")
        positions = {str(row[0]).strip(): number for number, row in enumerate(rows[1:], start=2) if row}
        data, appended = [], []
        for key, value in targets.items():
            title = names.get(key, key)
            if title in positions:
                data.append({"range": f"'Справочник'!B{positions[title]}", "values": [[_safe_cell(value)]]})
            else:
                appended.append([title, _safe_cell(value)])
        if data:
            self.values.batchUpdate(spreadsheetId=self.spreadsheet_id,
                                    body={"valueInputOption": "USER_ENTERED", "data": data}).execute()
        if appended:
            self.values.append(spreadsheetId=self.spreadsheet_id, range="'Справочник'!A:B",
                               valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
                               body={"values": appended}).execute()

    # --- catalog tabs ---------------------------------------------------
    def _read_table(self, tab: str, width: int, numeric_names: set[str]):
        rng = f"'{tab}'!A:{_col_letter(width - 1)}"
        if self.single_read:
            values = self._single(rng)
            if not values:
                raise RuntimeError(f"{tab}: header row is missing")
            return self._table(values)
        formatted = self.values.get(spreadsheetId=self.spreadsheet_id, range=rng,
                                    valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
        unformatted = self.values.get(spreadsheetId=self.spreadsheet_id, range=rng,
                                      valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
        if not formatted:
            raise RuntimeError(f"{tab}: header row is missing")
        headers = formatted[0]
        numeric = _numeric_indexes(headers, numeric_names)
        rows = []
        for index, text_row in enumerate(formatted[1:], start=1):
            raw = unformatted[index] if index < len(unformatted) else []
            rows.append(_merge_numeric(text_row, raw, len(headers), numeric))
        return headers, rows

    def _append_table(self, tab: str, width: int, rows) -> list[int]:
        result = self.values.append(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{tab}'!A:{_col_letter(width - 1)}",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [[_safe_cell(v) for v in row] for row in rows]},
        ).execute()
        return _appended_rows(result)

    def read_products(self):
        return self._read_table(PRODUCTS_TAB, len(PRODUCT_HEADERS), NUMERIC_PRODUCT_HEADERS)

    def append_products(self, rows) -> list[int]:
        return self._append_table(PRODUCTS_TAB, len(PRODUCT_HEADERS), rows)

    def update_product(self, row_number: int, row) -> None:
        last = _col_letter(len(PRODUCT_HEADERS) - 1)
        self.values.update(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{PRODUCTS_TAB}'!A{int(row_number)}:{last}{int(row_number)}",
            valueInputOption="USER_ENTERED",
            body={"values": [[_safe_cell(v) for v in row]]},
        ).execute()

    def read_sets(self):
        return self._read_table(SETS_TAB, len(SET_HEADERS), NUMERIC_SET_HEADERS)

    def delete_rows(self, tab: str, row_numbers) -> None:
        sheet_id = self._sheet_ids()[tab]
        requests = [
            {"deleteDimension": {"range": {"sheetId": sheet_id, "dimension": "ROWS",
                                           "startIndex": int(n) - 1, "endIndex": int(n)}}}
            for n in sorted({int(n) for n in row_numbers}, reverse=True)
        ]
        if requests:
            self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id, body={"requests": requests}).execute()

    def replace_set(self, name: str, rows) -> None:
        _, existing = self.read_sets()
        wanted = _norm_header(name)
        old = [number for number, row in enumerate(existing, start=2) if row and _norm_header(row[0]) == wanted]
        self.delete_rows(SETS_TAB, old)
        if rows:
            self._append_table(SETS_TAB, len(SET_HEADERS), rows)

    def copy_spreadsheet(self, title: str) -> str:
        """Copy the whole spreadsheet on Drive (backup / test copy). Returns the new spreadsheet id."""
        from googleapiclient.discovery import build

        drive = build("drive", "v3", credentials=_default_credentials(), cache_discovery=False)
        return drive.files().copy(fileId=self.spreadsheet_id, body={"name": title}, fields="id").execute()["id"]
