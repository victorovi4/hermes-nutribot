"""Shared in-memory fakes for nutricore tests."""
from __future__ import annotations

import copy


BASE_HEADERS = [
    "Дата", "Приём пищи", "Продукт / блюдо", "Порция", "ккал",
    "Белки, г", "Жиры, г", "Углеводы, г", "Источник / уточнение",
    "Самочувствие / симптомы", "Статус порции",
    "Версия оценки / рецепт", "Время приёма (MSK)",
]
FULL_HEADERS = BASE_HEADERS + ["ID записи", "ID продукта", "Количество", "Мерка", "Набор", "Группа", "Откуда"]


class MemoryStore:
    def __init__(self, food_rows=None, headers=None):
        self.headers = headers or [
            "Дата", "Приём пищи", "Продукт / блюдо", "Порция", "ккал",
            "Белки, г", "Жиры, г", "Углеводы, г", "Источник / уточнение",
            "Самочувствие / симптомы", "Статус порции",
            "Версия оценки / рецепт", "Время приёма (MSK)",
        ]
        self.food_rows = copy.deepcopy(food_rows or [])
        self.writes = []
        self.product_rows = []
        self.set_rows = []
        self.targets = {"energy": "1 800–1 900 ккал/день", "protein": "120–130 г/день", "fat": "55–65 г/день минимум", "goal": ""}

    def read_products(self):
        from nutricore.schema import PRODUCT_HEADERS
        return list(PRODUCT_HEADERS), copy.deepcopy(self.product_rows)

    def append_products(self, rows):
        start = len(self.product_rows) + 2
        self.product_rows.extend(copy.deepcopy(rows))
        self.writes.append(("append_products", copy.deepcopy(rows)))
        return list(range(start, start + len(rows)))

    def update_product(self, row_number, row):
        self.product_rows[row_number - 2] = copy.deepcopy(row)
        self.writes.append(("update_product", row_number, copy.deepcopy(row)))

    def read_sets(self):
        from nutricore.schema import SET_HEADERS
        return list(SET_HEADERS), copy.deepcopy(self.set_rows)

    def replace_set(self, name, rows):
        self.set_rows = [r for r in self.set_rows if r[0].casefold() != name.casefold()] + copy.deepcopy(rows)
        self.writes.append(("replace_set", name, copy.deepcopy(rows)))

    def write_food_cells(self, row_number, cells):
        self.write_food_cells_many([(row_number, cells)])

    def write_food_cells_many(self, updates):
        for row_number, cells in updates:
            row = self.food_rows[row_number - 2]
            while len(row) < len(self.headers):
                row.append("")
            for name, value in cells.items():
                row[self.headers.index(name)] = value
        self.writes.append(("cells", [(n, dict(c)) for n, c in updates]))

    def delete_rows(self, tab, row_numbers):
        assert tab == "Питание"
        for number in sorted(set(row_numbers), reverse=True):
            del self.food_rows[number - 2]
        self.writes.append(("delete_rows", tab, sorted(row_numbers)))

    def read_all_day_totals(self):
        dates = []
        for row in self.food_rows:
            if row and row[0] and row[0] not in dates:
                dates.append(row[0])
        return {d: self.read_day_total(d) for d in dates}

    def read_food(self):
        return self.headers, copy.deepcopy(self.food_rows)

    def append_food(self, rows):
        start = len(self.food_rows) + 2
        self.food_rows.extend(copy.deepcopy(rows))
        self.writes.append(("append", copy.deepcopy(rows)))
        return list(range(start, start + len(rows)))

    def update_food(self, row_number, row):
        self.food_rows[row_number - 2] = copy.deepcopy(row)
        self.writes.append(("update", row_number, copy.deepcopy(row)))

    def read_food_rows(self, row_numbers):
        return {n: copy.deepcopy(self.food_rows[n - 2]) for n in row_numbers}

    def read_day_total(self, date):
        idx = {name: i for i, name in enumerate(self.headers)}
        nums = [0.0, 0.0, 0.0, 0.0]
        for row in self.food_rows:
            if row and row[idx["Дата"]] == date:
                for j, name in enumerate(("ккал", "Белки, г", "Жиры, г", "Углеводы, г")):
                    nums[j] += float(row[idx[name]])
        return dict(zip(("kcal", "protein_g", "fat_g", "carbs_g"), nums))

    def read_targets(self):
        return dict(self.targets)

    def set_targets(self, targets):
        self.targets.update({k: str(v) for k, v in targets.items()})
        self.writes.append(("set_targets", dict(targets)))

    def find_standards(self, query):
        return []

    def read_food_row(self, row_number):
        if row_number < 2 or row_number - 2 >= len(self.food_rows):
            return None
        return copy.deepcopy(self.food_rows[row_number - 2])


def item(product="Творог 9%", portion="175 г", kcal=278.25):
    return {
        "meal": "Завтрак", "product": product, "portion": portion,
        "kcal": kcal, "protein_g": 29.23, "fat_g": 15.75, "carbs_g": 3.5,
        "source": "по этикетке", "symptoms": "", "portion_status": "точная",
        "estimate_version": "этикетка v1", "consumed_time": "09:15",
    }


class _Req:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


def _split_range(text):
    """'Tab'!A2:T5 -> (tab, first_col, first_row, last_col, last_row); rows may be None."""
    import re
    tab, _, cells = text.partition("!")
    tab = tab.strip("'")
    match = re.fullmatch(r"([A-Z]+)(\d+)?(?::([A-Z]+)(\d+)?)?", cells)
    first_col, first_row, last_col, last_row = match.groups()
    col = lambda letters: ord(letters) - ord("A")
    return (tab, col(first_col), int(first_row) if first_row else None,
            col(last_col or first_col), int(last_row) if last_row else (int(first_row) if first_row and not last_col else None))


class FakeSheets:
    """A tiny in-memory Google Sheets: tabs of rows, enough API for GoogleSheetStore."""

    def __init__(self, tabs):
        self.tabs = {name: [list(row) for row in rows] for name, rows in tabs.items()}
        self.sheet_ids = {name: 100 + i for i, name in enumerate(self.tabs)}
        self.writes = []

    # --- service facade -------------------------------------------------
    def spreadsheets(self):
        return self

    def values(self):
        return _FakeValues(self)

    def get(self, **kw):
        return _Req({"sheets": [{"properties": {"title": t, "sheetId": i}} for t, i in self.sheet_ids.items()]})

    def batchUpdate(self, **kw):
        for request in kw["body"]["requests"]:
            self.writes.append(("structure", request))
            if "addSheet" in request:
                title = request["addSheet"]["properties"]["title"]
                self.tabs[title] = []
                self.sheet_ids[title] = 100 + len(self.sheet_ids)
            elif "deleteDimension" in request:
                rng = request["deleteDimension"]["range"]
                title = next(t for t, i in self.sheet_ids.items() if i == rng["sheetId"])
                del self.tabs[title][rng["startIndex"]:rng["endIndex"]]
        return _Req({})


class _FakeValues:
    def __init__(self, book):
        self.book = book

    def _read(self, text, render):
        tab, c1, r1, c2, r2 = _split_range(text)
        rows = self.book.tabs[tab]
        first = (r1 or 1) - 1
        last = r2 if r2 else len(rows)
        out = []
        for row in rows[first:last]:
            cells = list(row[c1:c2 + 1])
            if render == "FORMATTED_VALUE":
                cells = [str(v).replace(".", ",") if isinstance(v, float) else v for v in cells]
            while cells and cells[-1] == "":
                cells.pop()
            out.append(cells)
        while out and not out[-1]:
            out.pop()
        return out

    def _write(self, text, values):
        tab, c1, r1, _c2, _r2 = _split_range(text)
        rows = self.book.tabs[tab]
        for offset, new in enumerate(values):
            index = r1 - 1 + offset
            while len(rows) <= index:
                rows.append([])
            row = rows[index]
            while len(row) < c1 + len(new):
                row.append("")
            row[c1:c1 + len(new)] = list(new)

    def get(self, **kw):
        return _Req({"values": self._read(kw["range"], kw.get("valueRenderOption", "FORMATTED_VALUE"))})

    def batchGet(self, **kw):
        render = kw.get("valueRenderOption", "FORMATTED_VALUE")
        return _Req({"valueRanges": [{"range": r, "values": self._read(r, render)} for r in kw["ranges"]]})

    def update(self, **kw):
        self.book.writes.append(("update", kw["range"], kw["body"]["values"]))
        self._write(kw["range"], kw["body"]["values"])
        return _Req({})

    def batchUpdate(self, **kw):
        for item in kw["body"]["data"]:
            self.book.writes.append(("update", item["range"], item["values"]))
            self._write(item["range"], item["values"])
        return _Req({})

    def append(self, **kw):
        tab, _c1, _r1, c2, _r2 = _split_range(kw["range"])
        rows = self.book.tabs[tab]
        start = len(rows) + 1
        for new in kw["body"]["values"]:
            rows.append(list(new))
        self.book.writes.append(("append", kw["range"], kw["body"]["values"]))
        last_col = chr(ord("A") + c2)
        return _Req({"updates": {"updatedRange": f"'{tab}'!A{start}:{last_col}{len(rows)}"}})
