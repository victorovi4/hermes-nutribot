"""One scenario, both storage backends: whatever the diary code needs, each backend must do the same."""
from __future__ import annotations

import pytest

from nutricore import google_store as gmod
from nutricore.schema import FOOD_HEADERS, FOOD_TAB, PRODUCT_HEADERS, SET_HEADERS
from nutricore.store import open_store
from tests.fakes import FakeSheets

REFERENCE = [["Параметр", "Значение"], ["Стартовая цель энергии", "1 800–1 900 ккал/день"],
             ["Белок", "120–130 г/день"], ["Жиры", "55–65 г/день минимум"], ["Первичная цель", "71 кг"]]


def sheets_store():
    sheets = FakeSheets({FOOD_TAB: [list(FOOD_HEADERS[:13])], "Итоги по дням": [["Дата", "ккал", "Белки, г", "Жиры, г", "Углеводы, г"]],
                         "Справочник": REFERENCE, "Стандартные порции": [["Категория", "Продукт"]]})
    store = gmod.GoogleSheetStore(service=sheets, spreadsheet_id="fixed", single_read=True)
    store.ensure_schema()
    return store


def sqlite_store(tmp_path):
    store = open_store({"NUTRI_STORAGE": "sqlite", "NUTRI_DB_PATH": str(tmp_path / "nutribot.db")})
    store.ensure_schema()
    store.set_targets({"energy": "1 800–1 900 ккал/день", "protein": "120–130 г/день",
                       "fat": "55–65 г/день минимум", "goal": "71 кг"})
    return store


@pytest.fixture(params=["sheets", "sqlite"])
def store(request, tmp_path):
    return sheets_store() if request.param == "sheets" else sqlite_store(tmp_path)


def food_row(date="18.09.2026", meal="Ужин", product="Зефир", portion="2 штуки", kcal=60.0, record_id="r00000001", extra=None):
    values = {"Дата": date, "Приём пищи": meal, "Продукт / блюдо": product, "Порция": portion, "ккал": kcal,
              "Белки, г": 1.2, "Жиры, г": 0.0, "Углеводы, г": 13.4, "Статус порции": "оценочная",
              "ID записи": record_id, "ID продукта": "p0000001", "Количество": 2.0, "Мерка": "штука", "Откуда": "бот"}
    values.update(extra or {})
    return [values.get(header, "") for header in FOOD_HEADERS]


def test_schema_is_ready_and_idempotent(store):
    headers, rows = store.read_food()
    assert headers == FOOD_HEADERS and rows == []
    assert store.read_products()[0] == PRODUCT_HEADERS and store.read_sets()[0] == SET_HEADERS
    store.ensure_schema()
    assert store.read_food()[0] == FOOD_HEADERS


def test_append_read_update_and_numbers(store):
    numbers = store.append_food([food_row(), food_row(product="Кофе", kcal=0.0, record_id="r00000002")])
    assert len(numbers) == 2 and numbers[0] != numbers[1]
    headers, rows = store.read_food()
    assert [r[headers.index("Продукт / блюдо")] for r in rows] == ["Зефир", "Кофе"]
    assert rows[0][headers.index("ккал")] == 60.0 and rows[0][headers.index("Количество")] == 2.0
    back = store.read_food_rows(numbers)
    assert back[numbers[0]][headers.index("ID записи")] == "r00000001" and len(back) == 2
    changed = food_row(product="Зефир", kcal=30.0, record_id="r00000001")
    store.update_food(numbers[0], changed)
    assert store.read_food_rows([numbers[0]])[numbers[0]][headers.index("ккал")] == 30.0


def test_cell_writes_touch_only_the_service_columns(store):
    numbers = store.append_food([food_row(record_id="")])
    before = store.read_food_rows(numbers)[numbers[0]]
    store.write_food_cells_many([(numbers[0], {"ID записи": "rabcdef01", "Откуда": "миграция"})])
    after = store.read_food_rows(numbers)[numbers[0]]
    assert after[:13] == before[:13]
    assert after[FOOD_HEADERS.index("ID записи")] == "rabcdef01" and after[FOOD_HEADERS.index("Откуда")] == "миграция"
    with pytest.raises(ValueError):
        store.write_food_cells(numbers[0], {"ккал": 1})


def test_delete_removes_exactly_one_row(store):
    """Row numbers may shift after a delete (a spreadsheet renumbers), so the code above always
    re-reads and finds its row by «ID записи». What every backend must promise: the right row goes."""
    numbers = store.append_food([food_row(record_id="r1"), food_row(record_id="r2"), food_row(record_id="r3")])
    store.delete_rows(FOOD_TAB, [numbers[1]])
    headers, left = store.read_food()
    assert [r[headers.index("ID записи")] for r in left] == ["r1", "r3"]
    again = store.append_food([food_row(record_id="r4")])
    assert again[0] not in numbers[:1]                                  # a new row never reuses a live number
    assert [r[headers.index("ID записи")] for r in store.read_food()[1]] == ["r1", "r3", "r4"]


def test_day_totals(store):
    store.append_food([food_row(kcal=60.0), food_row(date="17.09.2026", kcal=100.0, record_id="r2")])
    assert store.read_day_total("18.09.2026")["kcal"] == 60.0
    totals = store.read_all_day_totals()
    assert totals["18.09.2026"]["kcal"] == 60.0 and totals["17.09.2026"]["kcal"] == 100.0


def test_products_and_sets(store):
    product = ["p0000001", "Зефир", "1 шт", 30.0, 0.6, 0.0, 6.7, "штука", "штука", "оценка", "", "", "19.09.2026", "", "бот", ""]
    rows = store.append_products([product])
    assert len(rows) == 1
    headers, products = store.read_products()
    assert products[0][1] == "Зефир" and products[0][3] == 30.0 and len(products[0]) == len(PRODUCT_HEADERS)
    product[3] = 31.0
    store.update_product(rows[0], product)
    assert store.read_products()[1][0][3] == 31.0
    store.replace_set("Каша", [["Каша", "p0000001", "Зефир", 2.0, "штука", 1, ""]])
    store.replace_set("Завтрак", [["Завтрак", "p0000001", "Зефир", 1.0, "штука", 1, ""]])
    store.replace_set("Каша", [["Каша", "p0000001", "Зефир", 3.0, "штука", 1, ""]])
    sets = store.read_sets()[1]
    assert sorted((r[0], r[3]) for r in sets) == [("Завтрак", 1.0), ("Каша", 3.0)]


def test_snapshot_matches_separate_reads(store):
    store.append_food([food_row()])
    store.append_products([["p1", "Зефир", "1 шт", 30.0, 0.6, 0.0, 6.7, "штука", "штука", "оценка", "", "", "", "", "бот", ""]])
    store.replace_set("Каша", [["Каша", "p1", "Зефир", 2.0, "штука", 1, ""]])
    snapshot = store.read_snapshot()
    assert snapshot["food"] == store.read_food()
    assert snapshot["products"] == store.read_products() and snapshot["sets"] == store.read_sets()
    assert snapshot["targets"] == store.read_targets()


def test_targets(store):
    targets = store.read_targets()
    assert targets["energy"].startswith("1 800") and targets["protein"].startswith("120")
    store.set_targets({"energy": "2 000 ккал/день"})
    assert store.read_targets()["energy"] == "2 000 ккал/день"
    assert store.read_targets()["protein"].startswith("120")            # untouched keys survive


def test_the_app_can_add_edit_and_delete_on_either_backend(store):
    """The bug this catches: row numbers are positions in a spreadsheet and ids in a database.
    Whatever they are, adding, editing and deleting an entry must work the same way."""
    from nutricore import app, catalog

    catalog.upsert_product(store, {"name": "Зефир", "base": "1 шт", "kcal": 30, "protein_g": 0.6, "fat_g": 0, "carbs_g": 6.7},
                           origin="тест", today="21.09.2026", id_factory=lambda prefix: prefix + "0000001")
    product_id = catalog.load_products(store)[0].id
    added = app.add_entries(store, date="21.09.2026", meal="Ужин",
                            items=[{"record_id": "raaaaaaa1", "product_id": product_id, "quantity": 2, "unit": "штука"}])
    assert added["totals"]["kcal"] == 60.0
    edited = app.update_entry(store, "raaaaaaa1", {"quantity": 3})
    assert edited["entry"]["kcal"] == 90.0 and edited["totals"]["kcal"] == 90.0
    deleted = app.delete_entry(store, "raaaaaaa1")
    assert deleted["deleted"] == "raaaaaaa1" and deleted["totals"]["kcal"] == 0.0
    assert store.read_food()[1] == []
