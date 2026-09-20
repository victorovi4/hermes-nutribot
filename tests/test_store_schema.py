from __future__ import annotations

import pytest

from nutricore import google_store as mod
from nutricore.schema import FOOD_BASE_HEADERS, FOOD_EXTRA_HEADERS, FOOD_HEADERS, PRODUCT_HEADERS, SET_HEADERS
from tests.fakes import FakeSheets

FOOD_ROW = ["18.09.2026", "Ужин", "Балтика 0 Белая", "1 банка, 450 мл", 135.0, 0.0, 0.0, 22.5, "", "", "", "", ""]
TOTALS = [["Дата", "ккал", "Белки, г", "Жиры, г", "Углеводы, г"], ["18.09.2026", "1 812,1", "73,5", "58,3", "237,5"]]


def book(food_headers=None, extra_tabs=None):
    tabs = {"Питание": [list(food_headers or FOOD_BASE_HEADERS), list(FOOD_ROW)], "Итоги по дням": TOTALS}
    tabs.update(extra_tabs or {})
    return FakeSheets(tabs)


def store_for(sheets):
    return mod.GoogleSheetStore(service=sheets, spreadsheet_id="fixed")


def test_ensure_schema_creates_tabs_and_extra_headers():
    sheets = book(); result = store_for(sheets).ensure_schema()
    assert result == {"created_tabs": ["Продукты", "Наборы"], "added_food_headers": FOOD_EXTRA_HEADERS}
    assert sheets.tabs["Продукты"][0] == PRODUCT_HEADERS
    assert sheets.tabs["Наборы"][0] == SET_HEADERS
    assert sheets.tabs["Питание"][0] == FOOD_HEADERS
    assert sheets.tabs["Питание"][1][:13] == FOOD_ROW


def test_ensure_schema_is_idempotent():
    sheets = book(); store = store_for(sheets); store.ensure_schema()
    before = len(sheets.writes)
    assert store.ensure_schema() == {"created_tabs": [], "added_food_headers": []}
    assert len(sheets.writes) == before


def test_ensure_schema_refuses_a_foreign_column():
    sheets = book(food_headers=FOOD_BASE_HEADERS + ["Заметка"])
    with pytest.raises(RuntimeError, match="Заметка"):
        store_for(sheets).ensure_schema()
    assert sheets.writes == []


def test_ensure_schema_refuses_foreign_headers_in_catalog_tab():
    sheets = book(extra_tabs={"Продукты": [["Что-то своё"]]})
    with pytest.raises(RuntimeError, match="Продукты"):
        store_for(sheets).ensure_schema()


def test_write_food_cells_touches_only_the_named_extra_columns():
    sheets = book(); store = store_for(sheets); store.ensure_schema()
    before = len(sheets.writes)
    store.write_food_cells(2, {"ID записи": "rabc12345", "Откуда": "миграция"})
    assert sheets.writes[before:] == [("update", "'Питание'!N2", [["rabc12345"]]), ("update", "'Питание'!T2", [["миграция"]])]
    assert sheets.tabs["Питание"][1][:13] == FOOD_ROW
    with pytest.raises(ValueError, match="ккал"):
        store.write_food_cells(2, {"ккал": 1})


def test_products_roundtrip_keeps_numbers_numeric():
    sheets = book(); store = store_for(sheets); store.ensure_schema()
    row = ["p0000001", "Зефир", "1 шт", 30.0, 0.6, 0.0, 6.7, "штука", "штука", "оценка", "", "", "19.09.2026", "", "бот", ""]
    assert store.append_products([row]) == [2]
    headers, rows = store.read_products()
    assert headers == PRODUCT_HEADERS and rows[0][3:7] == [30.0, 0.6, 0.0, 6.7] and len(rows[0]) == 16
    row[3] = 31.0; store.update_product(2, row)
    assert store.read_products()[1][0][3] == 31.0


def test_replace_set_swaps_only_that_set():
    sheets = book(); store = store_for(sheets); store.ensure_schema()
    sheets.tabs["Наборы"] += [["Каша", "p1", "Хлопья", 35.0, "г", 1.0, ""], ["Творог", "p3", "Творог", 150.0, "г", 1.0, ""],
                              ["Каша", "p2", "Банан", 1.0, "штука", 2.0, ""]]
    store.replace_set("Каша", [["Каша", "p1", "Хлопья", 40.0, "г", 1.0, ""]])
    names = [r[0] for r in sheets.tabs["Наборы"][1:]]
    assert names == ["Творог", "Каша"]
    _, rows = store.read_sets()
    assert rows[1][3] == 40.0


def test_read_all_day_totals_parses_russian_numbers():
    totals = store_for(book()).read_all_day_totals()
    assert totals == {"18.09.2026": {"kcal": 1812.1, "protein_g": 73.5, "fat_g": 58.3, "carbs_g": 237.5}}


def test_snapshot_reads_everything_the_app_needs_in_one_call():
    sheets = book(extra_tabs={"Справочник": [["Параметр", "Значение"], ["Стартовая цель энергии", "1 800–1 900 ккал/день"], ["Белок", "120–130 г/день"]]})
    store = store_for(sheets); store.ensure_schema()
    store.append_products([["p0000001", "Зефир", "1 шт", 30.0, 0.6, 0.0, 6.7, "штука", "штука", "оценка", "", "", "19.09.2026", "", "бот", ""]])
    calls = []
    original = sheets.values
    class Spy:
        def __init__(self, inner): self.inner = inner
        def __getattr__(self, name):
            def wrapped(**kw):
                calls.append((name, kw)); return getattr(self.inner, name)(**kw)
            return wrapped
    sheets.values = lambda: Spy(original())
    snapshot = store.read_snapshot()
    assert [c[0] for c in calls] == ["batchGet"]
    assert calls[0][1]["valueRenderOption"] == "UNFORMATTED_VALUE" and calls[0][1]["dateTimeRenderOption"] == "FORMATTED_STRING"
    headers, rows = snapshot["food"]
    assert headers == FOOD_HEADERS and rows[0][:8] == FOOD_ROW[:8] and len(rows[0]) == 20
    assert snapshot["products"][1][0][1] == "Зефир" and snapshot["products"][1][0][3] == 30.0 and len(snapshot["products"][1][0]) == 16
    assert snapshot["sets"] == (SET_HEADERS, [])
    assert snapshot["targets"]["energy"] == "1 800–1 900 ккал/день" and snapshot["targets"]["protein"] == "120–130 г/день"


def test_single_read_mode_uses_one_call_per_read():
    sheets = book(); store = mod.GoogleSheetStore(service=sheets, spreadsheet_id="fixed", single_read=True); store.ensure_schema()
    calls = []
    original = sheets.values
    class Spy:
        def __init__(self, inner): self.inner = inner
        def __getattr__(self, name):
            def wrapped(**kw):
                calls.append(name); return getattr(self.inner, name)(**kw)
            return wrapped
    sheets.values = lambda: Spy(original())
    headers, rows = store.read_food()
    assert calls == ["get"] and rows[0][4] == 135.0 and rows[0][0] == "18.09.2026"
    calls.clear()
    assert store.read_food_rows([2])[2][4] == 135.0 and calls == ["batchGet"]
