from __future__ import annotations

import copy

from nutricore import catalog, ledger
from tests.fakes import FULL_HEADERS, MemoryStore, item

DATE = "19.09.2026"
H = {name: i for i, name in enumerate(FULL_HEADERS)}


def ids():
    counter = {"n": 0}
    def make(prefix):
        counter["n"] += 1
        return f"{prefix}{counter['n']:07d}"
    return make


def stocked(headers=FULL_HEADERS):
    store = MemoryStore(headers=headers); make = ids()
    for data in (
        {"name": "Зефир", "base": "1 шт", "kcal": 30, "protein_g": 0.6, "fat_g": 0, "carbs_g": 6.7},
        {"name": "Красный чеддер", "base": "100 г", "kcal": 354, "protein_g": 24, "fat_g": 28.8, "carbs_g": 0,
         "precision": "точно", "source": "этикетка"},
        {"name": "Овсяные хлопья", "base": "100 г", "kcal": 354, "protein_g": 12, "fat_g": 6.3, "carbs_g": 64},
        {"name": "Банан", "base": "100 г", "kcal": 90, "protein_g": 1.5, "fat_g": 0.3, "carbs_g": 23.7, "units": "штука=120"},
    ):
        catalog.upsert_product(store, data, origin="миграция", today="18.09.2026", id_factory=make)
    store.writes.clear()
    return store


def run(store, items, action="log_food", **kw):
    return ledger.execute(store, action=action, date=DATE, items=items, id_factory=kw.pop("id_factory", ids()), today=DATE, **kw)


def test_one_zefir_is_computed_from_the_card():
    store = stocked()
    result = run(store, [{"meal": "Перекус после обеда", "product_id": "p0000001", "quantity": 1, "unit": "штука"}])
    assert result["success"] is True and result["status"] == "written"
    row = store.food_rows[0]
    assert row[H["Продукт / блюдо"]] == "Зефир" and row[H["Порция"]] == "1 штука"
    assert [row[H[c]] for c in ("ккал", "Белки, г", "Жиры, г", "Углеводы, г")] == [30.0, 0.6, 0.0, 6.7]
    assert row[H["ID продукта"]] == "p0000001" and row[H["Количество"]] == 1.0 and row[H["Мерка"]] == "штука"
    assert row[H["ID записи"]] == "r0000001" and row[H["Откуда"]] == "бот" and row[H["Статус порции"]] == "оценочная"
    assert result["verified_rows"][0]["record_id"] == "r0000001"


def test_model_numbers_are_ignored_for_a_product_item():
    store = stocked()
    run(store, [{"meal": "Обед", "product_id": "p0000002", "quantity": 25, "unit": "г",
                 "product": "что-то", "portion": "много", "kcal": 999, "protein_g": 9, "fat_g": 9, "carbs_g": 9}])
    row = store.food_rows[0]
    assert row[H["Продукт / блюдо"]] == "Красный чеддер" and row[H["Порция"]] == "25 г" and row[H["ккал"]] == 88.5
    assert row[H["Источник / уточнение"]] == "этикетка" and row[H["Статус порции"]] == "точная"


def test_new_product_creates_the_card_and_the_entry_once():
    store = stocked(); make = ids()
    fresh = {"name": "Балтика 0 Белая", "base": "100 мл", "kcal": 30, "protein_g": 0, "fat_g": 0, "carbs_g": 5,
             "units": "банка=450", "precision": "оценка"}
    first = run(store, [{"meal": "Ужин", "new_product": fresh, "quantity": 1, "unit": "банка"}], id_factory=make)
    assert first["created_products"] == [{"product_id": "p0000001", "name": "Балтика 0 Белая"}]
    assert store.food_rows[0][H["Порция"]] == "1 банка (450 мл)" and store.food_rows[0][H["ID продукта"]] == "p0000001"
    second = run(store, [{"meal": "Обед", "new_product": fresh, "quantity": 300, "unit": "мл"}], id_factory=make)
    assert second["created_products"] == [] and len(store.product_rows) == 5
    assert store.food_rows[1][H["ккал"]] == 90.0 and store.food_rows[1][H["ID продукта"]] == "p0000001"


def test_set_items_share_one_group():
    store = stocked()
    items = [{"meal": "Завтрак", "product_id": "p0000003", "quantity": 35, "unit": "г", "set_name": "Овсяная каша, стандарт"},
             {"meal": "Завтрак", "product_id": "p0000004", "quantity": 1, "unit": "штука", "set_name": "Овсяная каша, стандарт"},
             {"meal": "Завтрак", "product_id": "p0000001", "quantity": 1, "unit": "штука"}]
    run(store, items)
    groups = [row[H["Группа"]] for row in store.food_rows]
    assert groups[0] == groups[1] != "" and groups[2] == ""
    assert [row[H["Набор"]] for row in store.food_rows] == ["Овсяная каша, стандарт"] * 2 + [""]
    assert store.food_rows[1][H["Порция"]] == "1 штука (120 г)"


def test_dry_run_writes_neither_entry_nor_card():
    store = stocked()
    fresh = {"name": "Манго", "base": "100 г", "kcal": 60, "protein_g": 0.8, "fat_g": 0.4, "carbs_g": 15}
    result = run(store, [{"meal": "Завтрак", "new_product": fresh, "quantity": 100, "unit": "г"}], dry_run=True)
    assert result["status"] == "dry_run" and result["planned"][0]["kcal"] == 60.0 and result["planned"][0]["portion"] == "100 г"
    assert store.writes == [] and store.food_rows == [] and len(store.product_rows) == 4


def test_validation_happens_before_any_write():
    store = stocked()
    fresh = {"name": "Манго", "base": "100 г", "kcal": 60, "protein_g": 0.8, "fat_g": 0.4, "carbs_g": 15}
    result = run(store, [{"meal": "Завтрак", "new_product": fresh, "quantity": 100, "unit": "г"},
                         {"meal": "Завтрак", "product_id": "p0000001", "quantity": 1, "unit": "ложка"}])
    assert result["success"] is False and result["error"] == "validation_error" and "ложка" in result["message"]
    assert store.writes == []


def test_bad_product_items_are_rejected():
    store = stocked()
    for bad in ({"meal": "Обед", "product_id": "p9999999", "quantity": 1, "unit": "г"},
                {"meal": "Обед", "product_id": "p0000002", "quantity": 0, "unit": "г"},
                {"meal": "Обед", "product_id": "p0000002", "quantity": 10},
                {"meal": "", "product_id": "p0000002", "quantity": 10, "unit": "г"}):
        result = run(store, [bad])
        assert result == {"success": False, "error": "validation_error", "message": result["message"]}
    assert store.writes == []


def test_old_schema_blocks_product_items_but_not_free_entries():
    store = stocked(headers=FULL_HEADERS[:13])
    blocked = run(store, [{"meal": "Обед", "product_id": "p0000002", "quantity": 25, "unit": "г"}])
    assert blocked == {"success": False, "error": "schema_outdated",
                       "message": "в таблице нет колонок продукта; сначала обновить схему"}
    free = run(store, [item()])
    assert free["status"] == "written" and len(store.food_rows[0]) == 13


def test_free_entry_on_the_new_schema_gets_a_record_id_and_origin():
    store = stocked()
    run(store, [item()], origin="приложение")
    row = store.food_rows[0]
    assert row[H["ID записи"]] == "r0000001" and row[H["Откуда"]] == "приложение" and row[H["ID продукта"]] == ""


def test_retry_of_the_same_product_item_is_a_noop():
    store = stocked(); make = ids()
    entry = [{"meal": "Перекус", "product_id": "p0000001", "quantity": 2, "unit": "штука"}]
    run(store, entry, id_factory=make)
    again = run(store, entry, id_factory=make)
    assert again["status"] == "already_recorded" and len(store.food_rows) == 1


def test_correct_food_rescales_and_keeps_the_record_id():
    store = stocked(); make = ids()
    run(store, [{"meal": "Перекус", "product_id": "p0000001", "quantity": 2, "unit": "штука"}], id_factory=make)
    before = copy.deepcopy(store.food_rows[0])
    result = run(store, [{"meal": "Перекус", "product_id": "p0000001", "quantity": 1, "unit": "штука"}],
                 action="correct_food", id_factory=make)
    row = store.food_rows[0]
    assert result["status"] == "updated" and row[H["ккал"]] == 30.0 and row[H["Порция"]] == "1 штука"
    assert row[H["Количество"]] == 1.0 and row[H["ID записи"]] == before[H["ID записи"]] and len(store.food_rows) == 1


# --- catalog actions ------------------------------------------------------
def test_find_product_returns_cards_with_usage():
    store = stocked(); make = ids()
    run(store, [{"meal": "Перекус", "product_id": "p0000001", "quantity": 2, "unit": "штука"}], id_factory=make)
    result = ledger.execute(store, action="find_product", query="зефир")
    assert result["success"] is True and result["status"] == "read" and len(result["products"]) == 1
    card = result["products"][0]
    assert card["product_id"] == "p0000001" and card["units"] == "штука" and card["default_unit"] == "штука"
    assert card["times_logged"] == 1 and card["last_quantity"] == 2.0 and card["last_unit"] == "штука"
    assert ledger.execute(store, action="find_product", query=" ")["error"] == "validation_error"
    assert store.writes == [w for w in store.writes if w[0] == "append"]


def test_save_set_and_find_set_through_execute():
    store = stocked()
    saved = ledger.execute(store, action="save_set", set_name="Овсяная каша, стандарт",
                           items=[{"product_id": "p0000003", "quantity": 35, "unit": "г"},
                                  {"product_id": "p0000004", "quantity": 1, "unit": "штука"}])
    assert saved["success"] is True and saved["status"] == "saved" and len(saved["items"]) == 2
    found = ledger.execute(store, action="find_set", query="каша")
    assert found["sets"][0]["set_name"] == "Овсяная каша, стандарт" and found["sets"][0]["kcal"] == 231.9
    assert len(ledger.execute(store, action="find_set")["sets"]) == 1
    bad = ledger.execute(store, action="save_set", set_name="Пусто", items=[])
    assert bad["success"] is False and bad["error"] == "validation_error"


def test_save_set_supports_dry_run():
    store = stocked()
    result = ledger.execute(store, action="save_set", set_name="Каша", dry_run=True,
                            items=[{"product_id": "p0000003", "quantity": 35, "unit": "г"}])
    assert result["status"] == "dry_run" and store.set_rows == []


def test_find_set_accepts_the_name_in_set_name_too():
    store = stocked()
    ledger.execute(store, action="save_set", set_name="Овсяная каша, стандарт", items=[{"product_id": "p0000003", "quantity": 35, "unit": "г"}])
    ledger.execute(store, action="save_set", set_name="Творожный завтрак", items=[{"product_id": "p0000002", "quantity": 25, "unit": "г"}])
    found = ledger.execute(store, action="find_set", set_name="каша стандарт")
    assert [s["set_name"] for s in found["sets"]] == ["Овсяная каша, стандарт"]


def test_set_targets_writes_goals_and_reads_them_back():
    store = stocked()
    result = ledger.execute(store, action="set_targets", targets={"energy": "2 000–2 100 ккал/день", "protein": "от 130 г"})
    assert result["success"] is True and result["status"] == "saved"
    assert result["targets"]["energy"] == "2 000–2 100 ккал/день"
    assert ledger.execute(store, action="day_status", date=DATE)["targets"]["protein"] == "от 130 г"
    assert ledger.execute(store, action="set_targets", targets={})["error"] == "validation_error"
