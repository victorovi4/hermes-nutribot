from __future__ import annotations

import copy

import pytest

from nutricore import app, catalog
from tests.fakes import FULL_HEADERS, MemoryStore

DATE = "19.09.2026"
H = {name: i for i, name in enumerate(FULL_HEADERS)}


def ids():
    counter = {"n": 0}
    def make(prefix):
        counter["n"] += 1
        return f"{prefix}{counter['n']:07d}"
    return make


def stocked():
    store = MemoryStore(headers=FULL_HEADERS); make = ids()
    for data in (
        {"name": "Зефир", "base": "1 шт", "kcal": 30, "protein_g": 0.6, "fat_g": 0, "carbs_g": 6.7},
        {"name": "Красный чеддер", "base": "100 г", "kcal": 354, "protein_g": 24, "fat_g": 28.8, "carbs_g": 0, "precision": "точно"},
        {"name": "Овсяные хлопья", "base": "100 г", "kcal": 352, "protein_g": 12.3, "fat_g": 6.1, "carbs_g": 61.8},
        {"name": "Банан", "base": "100 г", "kcal": 95, "protein_g": 1.5, "fat_g": 0.2, "carbs_g": 21.8, "units": "штука=120"},
        {"name": "Старое", "base": "1 шт", "kcal": 1, "protein_g": 0, "fat_g": 0, "carbs_g": 0},
    ):
        catalog.upsert_product(store, data, origin="миграция", today="18.09.2026", id_factory=make)
    store.product_rows[4][15] = "да"
    catalog.save_set(store, "Овсяная каша, стандарт", [{"product_id": "p0000003", "quantity": 35, "unit": "г"},
                                                        {"product_id": "p0000004", "quantity": 1, "unit": "штука"}])
    store.writes.clear()
    return store


def add(store, items, **kw):
    return app.add_entries(store, date=DATE, meal=kw.pop("meal", "Перекус после обеда"), items=items,
                           id_factory=kw.pop("id_factory", ids()), today=DATE, **kw)


def test_parse_targets():
    assert app.parse_range("1 800–1 900 ккал/день (ориентир, пересмотр через 2–3 недели)") == [1800.0, 1900.0]
    assert app.parse_range("55–65 г/день минимум") == [55.0, 65.0]
    assert app.parse_range("120-130 г") == [120.0, 130.0]
    assert app.parse_range("не меньше 100 г") == [100.0, 100.0]
    assert app.parse_range("") is None


def test_add_product_entry_with_a_client_record_id_is_idempotent():
    store = stocked()
    item = {"record_id": "rabcdef12", "product_id": "p0000001", "quantity": 1, "unit": "штука"}
    first = add(store, [item])
    assert first["entries"][0]["record_id"] == "rabcdef12" and first["entries"][0]["kcal"] == 30.0
    row = store.food_rows[0]
    assert row[H["Откуда"]] == "приложение" and row[H["Порция"]] == "1 штука" and row[H["ID записи"]] == "rabcdef12"
    assert row[H["Время приёма (MSK)"]] != ""
    again = add(store, [item])
    assert len(store.food_rows) == 1 and again["entries"][0]["record_id"] == "rabcdef12"
    assert again["totals"]["kcal"] == 30.0


def test_bad_client_record_id_is_rejected():
    store = stocked()
    with pytest.raises(ValueError, match="record_id"):
        add(store, [{"record_id": "что-то", "product_id": "p0000001", "quantity": 1, "unit": "штука"}])


def test_add_set_shares_a_group_and_can_remember_the_composition():
    store = stocked()
    items = [{"record_id": "r0000000a", "product_id": "p0000003", "quantity": 40, "unit": "г"},
             {"record_id": "r0000000b", "product_id": "p0000002", "quantity": 15, "unit": "г"}]
    result = add(store, items, meal="Завтрак", set_name="Овсяная каша, стандарт", remember_set=True)
    assert len({e["group_id"] for e in result["entries"]}) == 1 and result["entries"][0]["set_name"] == "Овсяная каша, стандарт"
    saved = catalog.load_sets(store)["Овсяная каша, стандарт"]
    assert [(i.product_name, i.quantity) for i in saved] == [("Овсяные хлопья", 40.0), ("Красный чеддер", 15.0)]


def test_new_product_from_the_store_catalog_becomes_a_card():
    store = stocked()
    card = {"name": "Творог 9%, ВкусВилл", "base": "100 г", "kcal": 157, "protein_g": 16, "fat_g": 9, "carbs_g": 3,
            "units": "упаковка=400", "default_unit": "г", "precision": "точно", "source": "ВкусВилл: https://vkusvill.ru/goods/tvorog-9-188/"}
    result = add(store, [{"record_id": "r00000001", "new_product": card, "quantity": 150, "unit": "г"}], meal="Завтрак")
    assert result["entries"][0]["kcal"] == 235.5 and store.product_rows[-1][14] == "приложение"


def test_day_view_groups_totals_and_targets():
    store = stocked()
    add(store, [{"record_id": "r00000001", "product_id": "p0000001", "quantity": 2, "unit": "штука"}])
    add(store, [{"record_id": "r0000000a", "product_id": "p0000003", "quantity": 35, "unit": "г"},
                {"record_id": "r0000000b", "product_id": "p0000004", "quantity": 1, "unit": "штука"}],
        meal="Завтрак", set_name="Овсяная каша, стандарт")
    store.food_rows.append(["19.09.2026", "Ужин", "Ролл", "½ порции", 297.0, 13.0, 8.5, 44.5, "", "", "оценочная", "", ""])
    store.food_rows.append(["18.09.2026", "Ужин", "Вчерашнее", "1", 500.0, 1.0, 1.0, 1.0, "", "", "", "", ""])
    view = app.day_view(store, DATE)
    assert view["date"] == DATE and [e["product"] for e in view["entries"]] == ["Зефир", "Овсяные хлопья", "Банан", "Ролл"]
    assert view["totals"] == {"kcal": 594.2, "protein_g": 20.3, "fat_g": 10.8, "carbs_g": 105.7}
    assert view["targets"] == {"kcal": [1800.0, 1900.0], "protein_g": [120.0, 130.0], "fat_g": [55.0, 65.0], "fat_min_only": True}
    zefir, oats, _banana, roll = view["entries"]
    assert zefir["editable"] and zefir["estimated"] and zefir["unit_list"] == [{"name": "штука", "amount": 1.0, "label": "штука"}]
    assert oats["set_name"] == "Овсяная каша, стандарт" and oats["unit_list"][0] == {"name": "г", "amount": 1.0, "label": "граммы"}
    assert roll["record_id"] == "" and roll["editable"] is False and roll["estimated"] is True
    assert view["meals"][:2] == ["До завтрака", "Завтрак"]


def test_catalog_view_hides_hidden_cards_and_reports_usage():
    store = stocked()
    add(store, [{"record_id": "r00000001", "product_id": "p0000001", "quantity": 2, "unit": "штука"}])
    view = app.catalog_view(store)
    names = [p["name"] for p in view["products"]]
    assert "Старое" not in names and names[0] == "Зефир"
    zefir = view["products"][0]
    assert zefir["times_logged"] == 1 and zefir["last_quantity"] == 2.0 and zefir["last_date"] == DATE
    assert zefir["unit_kcal"] == 30.0 and zefir["unit_label"] == "штука"
    banana = next(p for p in view["products"] if p["name"] == "Банан")
    assert [u["name"] for u in banana["unit_list"]] == ["штука", "г"] and banana["unit_kcal"] == 114.0
    assert view["sets"][0]["set_name"] == "Овсяная каша, стандарт" and view["sets"][0]["kcal"] == 237.2


def test_update_product_entry_recalculates_and_moves_meal():
    store = stocked()
    add(store, [{"record_id": "r00000001", "product_id": "p0000001", "quantity": 2, "unit": "штука"}])
    result = app.update_entry(store, "r00000001", {"quantity": 1, "meal": "Ужин"})
    assert result["entry"]["kcal"] == 30.0 and result["entry"]["portion"] == "1 штука" and result["entry"]["meal"] == "Ужин"
    assert store.food_rows[0][H["Количество"]] == 1.0 and store.food_rows[0][H["ID записи"]] == "r00000001"


def test_update_free_entry_changes_text_and_numbers():
    store = stocked()
    store.food_rows.append([DATE, "Ужин", "Ролл", "½ порции", 297.0, 13.0, 8.5, 44.5, "", "", "оценочная", "", "",
                            "r0000000f", "", "", "", "", "", "бот"])
    result = app.update_entry(store, "r0000000f", {"portion": "вся порция", "kcal": 594, "protein_g": 26, "fat_g": 17, "carbs_g": 89})
    assert result["entry"]["kcal"] == 594.0 and store.food_rows[0][H["Порция"]] == "вся порция"
    with pytest.raises(ValueError):
        app.update_entry(store, "r0000000f", {"quantity": 2})          # free entries have no units
    with pytest.raises(ValueError):
        app.update_entry(store, "r0000000f", {"kcal": -5})


def test_unknown_record_id_is_a_lookup_error():
    store = stocked()
    with pytest.raises(LookupError):
        app.update_entry(store, "r99999999", {"quantity": 1})
    with pytest.raises(LookupError):
        app.delete_entry(store, "r99999999")


def test_delete_entry_removes_exactly_that_row():
    store = stocked()
    add(store, [{"record_id": "r00000001", "product_id": "p0000001", "quantity": 2, "unit": "штука"},
                {"record_id": "r00000002", "product_id": "p0000002", "quantity": 25, "unit": "г"}])
    result = app.delete_entry(store, "r00000001")
    assert result["deleted"] == "r00000001" and result["totals"]["kcal"] == 88.5
    assert [row[H["ID записи"]] for row in store.food_rows] == ["r00000002"]


def test_delete_refuses_when_the_row_moved_under_us():
    store = stocked()
    add(store, [{"record_id": "r00000001", "product_id": "p0000001", "quantity": 2, "unit": "штука"}])
    original = store.read_food_rows
    store.read_food_rows = lambda numbers: {n: ["x"] * 20 for n in numbers}
    with pytest.raises(app.ConflictError):
        app.delete_entry(store, "r00000001")
    store.read_food_rows = original
    assert len(store.food_rows) == 1


def test_save_meal_as_set_uses_only_product_entries():
    store = stocked()
    add(store, [{"record_id": "r00000001", "product_id": "p0000003", "quantity": 40, "unit": "г"},
                {"record_id": "r00000002", "product_id": "p0000004", "quantity": 1, "unit": "штука"}], meal="Завтрак")
    saved = app.save_meal_as_set(store, "Мой завтрак", ["r00000001", "r00000002"])
    assert saved["set_name"] == "Мой завтрак" and len(saved["items"]) == 2
    store.food_rows.append([DATE, "Ужин", "Ролл", "½", 297.0, 13.0, 8.5, 44.5, "", "", "", "", "", "r0000000f", "", "", "", "", "", "бот"])
    with pytest.raises(ValueError, match="Ролл"):
        app.save_meal_as_set(store, "Ужин", ["r0000000f"])


# --- statistics -------------------------------------------------------------
def stats_store():
    store = MemoryStore(headers=FULL_HEADERS)
    def row(date, kcal, protein, fat):
        return [date, "Обед", "Еда", "1", float(kcal), float(protein), float(fat), 100.0, "", "", "", "", "", "r" + date[:2] + "00000", "", "", "", "", "", "бот"]
    store.food_rows = [row("15.09.2026", 1850, 125, 60), row("16.09.2026", 1500, 90, 40), row("16.09.2026", 200, 35, 20),
                       row("17.09.2026", 2100, 130, 70), row("19.09.2026", 900, 50, 30)]
    return store


def test_stats_view_classifies_days_against_the_targets():
    view = app.stats_view(stats_store(), "14.09.2026", "20.09.2026", today="19.09.2026")
    assert view["from"] == "14.09.2026" and view["to"] == "20.09.2026" and len(view["days"]) == 7
    by_date = {d["date"]: d for d in view["days"]}
    assert by_date["14.09.2026"]["logged"] is False and by_date["14.09.2026"]["kcal_state"] is None
    assert (by_date["15.09.2026"]["kcal_state"], by_date["15.09.2026"]["kcal_delta"], by_date["15.09.2026"]["protein_ok"], by_date["15.09.2026"]["fat_ok"]) == ("in", 0.0, True, True)
    assert (by_date["16.09.2026"]["kcal"], by_date["16.09.2026"]["kcal_state"], by_date["16.09.2026"]["kcal_delta"]) == (1700.0, "below", -100.0)
    assert by_date["16.09.2026"]["protein_ok"] is True and by_date["16.09.2026"]["entries"] == 2
    assert (by_date["17.09.2026"]["kcal_state"], by_date["17.09.2026"]["kcal_delta"]) == ("above", 200.0)
    assert by_date["19.09.2026"]["today"] is True and by_date["20.09.2026"]["future"] is True


def test_stats_summary_skips_today_and_empty_days():
    summary = app.stats_view(stats_store(), "14.09.2026", "20.09.2026", today="19.09.2026")["summary"]
    assert summary == {"days_logged": 4, "days_counted": 3, "kcal_in": 1, "kcal_below": 1, "kcal_above": 1, "protein_ok": 3, "fat_ok": 3,
                       "avg": {"kcal": 1883.3, "protein_g": 126.7, "fat_g": 63.3, "carbs_g": 133.3}}
    empty = app.stats_view(stats_store(), "01.09.2026", "07.09.2026", today="19.09.2026")["summary"]
    assert empty["days_counted"] == 0 and empty["avg"] is None


def test_stats_view_validates_the_period():
    for bad in (("20.09.2026", "14.09.2026"), ("01.01.2026", "20.09.2026"), ("вчера", "20.09.2026")):
        with pytest.raises(ValueError):
            app.stats_view(stats_store(), *bad, today="19.09.2026")
