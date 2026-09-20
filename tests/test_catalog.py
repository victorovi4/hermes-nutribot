from __future__ import annotations

import pytest

from nutricore import catalog
from nutricore.units import Unit
from tests.fakes import FULL_HEADERS, MemoryStore

TODAY = "19.09.2026"


def ids():
    counter = {"n": 0}
    def make(prefix):
        counter["n"] += 1
        return f"{prefix}{counter['n']:07d}"
    return make


def zefir(**over):
    data = {"name": "Зефир", "base": "1 шт", "kcal": 30, "protein_g": 0.6, "fat_g": 0, "carbs_g": 6.7}
    data.update(over)
    return data


def add(store, data, make=None):
    return catalog.upsert_product(store, data, origin="бот", today=TODAY, id_factory=make or ids())


def test_upsert_creates_a_card():
    store = MemoryStore()
    product, created = add(store, zefir())
    assert created is True and product.id == "p0000001"
    assert product.units == (Unit("штука", 1.0),) and product.default_unit == "штука"
    row = store.product_rows[0]
    assert row[0:3] == ["p0000001", "Зефир", "1 шт"] and row[3:7] == [30.0, 0.6, 0.0, 6.7]
    assert row[7] == "штука" and row[9] == "оценка" and row[12] == TODAY and row[14] == "бот"


def test_upsert_finds_duplicates_by_name_and_alias():
    store = MemoryStore(); make = ids()
    add(store, {"name": "Зелёный чай", "base": "1 шт", "kcal": 0, "protein_g": 0, "fat_g": 0, "carbs_g": 0,
                "units": "чашка"}, make)
    same, created = add(store, {"name": "зеленый  чай", "base": "1 шт", "kcal": 0, "protein_g": 0, "fat_g": 0, "carbs_g": 0}, make)
    assert created is False and len(store.product_rows) == 1 and same.id == "p0000001"
    add(store, {"name": "Кофе чёрный", "aliases": ["Кофе"], "base": "1 шт", "kcal": 0, "protein_g": 0, "fat_g": 0,
                "carbs_g": 0, "units": "чашка"}, make)
    found, created = add(store, {"name": "кофе", "base": "1 шт", "kcal": 0, "protein_g": 0, "fat_g": 0, "carbs_g": 0}, make)
    assert created is False and found.name == "Кофе чёрный"


def test_a_new_name_for_a_known_card_becomes_an_alias():
    store = MemoryStore(); make = ids()
    add(store, {"name": "Кофе чёрный", "aliases": ["Кофе"], "base": "1 шт", "kcal": 0, "protein_g": 0, "fat_g": 0,
                "carbs_g": 0, "units": "чашка"}, make)
    product, _ = add(store, {"name": "Кофе", "aliases": ["Американо"], "base": "1 шт", "kcal": 0, "protein_g": 0,
                             "fat_g": 0, "carbs_g": 0}, make)
    assert "Американо" in product.aliases and len(store.product_rows) == 1


def test_label_upgrades_an_estimate_but_not_the_other_way():
    store = MemoryStore(); make = ids()
    add(store, zefir(), make)
    label = {"name": "Зефир", "base": "100 г", "kcal": 300, "protein_g": 1, "fat_g": 0.1, "carbs_g": 74,
             "units": "штука=30", "precision": "точно", "source": "этикетка"}
    product, created = add(store, label, make)
    assert created is False and product.base == "100 г" and product.kcal == 300.0 and product.precision == "точно"
    assert product.units == (Unit("штука", 30.0),)          # old per-piece unit is replaced with the weighed one
    assert store.product_rows[0][13] == TODAY
    again, _ = add(store, zefir(kcal=99), make)
    assert again.kcal == 300.0 and again.precision == "точно"


def test_new_units_are_merged_into_a_card_with_the_same_base():
    store = MemoryStore(); make = ids()
    add(store, {"name": "Творог 9%", "base": "100 г", "kcal": 159, "protein_g": 16.7, "fat_g": 9, "carbs_g": 2,
                "precision": "точно"}, make)
    product, _ = add(store, {"name": "Творог 9%", "base": "100 г", "kcal": 159, "protein_g": 16.7, "fat_g": 9,
                             "carbs_g": 2, "units": "пачка=180"}, make)
    assert product.units == (Unit("пачка", 180.0),)


def test_dry_run_writes_nothing():
    store = MemoryStore()
    product, created = catalog.upsert_product(store, zefir(), origin="бот", today=TODAY, id_factory=ids(), dry_run=True)
    assert created is True and product.name == "Зефир" and store.product_rows == [] and store.writes == []


@pytest.mark.parametrize("bad", [
    zefir(name=" "), zefir(base="100 кг"), zefir(kcal=-1), zefir(kcal="много"),
    zefir(base="100 г", units="штука"), zefir(precision="наверное"), zefir(default_unit="ложка"),
])
def test_upsert_rejects_bad_cards(bad):
    store = MemoryStore()
    with pytest.raises(ValueError):
        add(store, bad)
    assert store.product_rows == []


def test_defaults_for_weight_based_cards():
    store = MemoryStore()
    product, _ = add(store, {"name": "Гречка варёная", "base": "100 г", "kcal": 97, "protein_g": 3.9, "fat_g": 1, "carbs_g": 19.2})
    assert product.units == () and product.default_unit == "г"


def test_find_products_matches_tokens_skips_hidden_and_sorts_by_usage():
    store = MemoryStore(headers=FULL_HEADERS); make = ids()
    add(store, {"name": "Трубочка ВкусВилл", "base": "100 г", "kcal": 407, "protein_g": 7, "fat_g": 17.8, "carbs_g": 55,
                "units": "трубочка=40"}, make)
    add(store, {"name": "Вафельная трубочка", "base": "100 г", "kcal": 212, "protein_g": 2.8, "fat_g": 10, "carbs_g": 27,
                "units": "трубочка=60"}, make)
    add(store, {"name": "Трубочка старая", "base": "1 шт", "kcal": 100, "protein_g": 1, "fat_g": 1, "carbs_g": 1}, make)
    store.product_rows[2][15] = "да"
    row = ["18.09.2026", "Перекус", "Вафельная трубочка", "1 трубочка (60 г)", 127, 1.7, 6.1, 16.3, "", "", "", "", "",
           "r1", "p0000002", 1, "трубочка", "", "", "бот"]
    store.food_rows = [list(row), list(row)]
    stats = catalog.usage_stats(*store.read_food())
    found = catalog.find_products(store, "трубочки", stats=stats)
    assert [p.name for p in found] == ["Вафельная трубочка", "Трубочка ВкусВилл"]
    assert catalog.get_product(store, "p0000003").hidden is True


def test_usage_stats_takes_the_latest_date():
    headers = FULL_HEADERS
    def row(date, qty):
        return [date, "Перекус", "Зефир", "", 30, 0, 0, 0, "", "", "", "", "", "r", "p1", qty, "штука", "", "", "бот"]
    stats = catalog.usage_stats(headers, [row("17.09.2026", 2), row("18.09.2026", 1), row("09.09.2026", 3)])
    assert stats == {"p1": {"times": 3, "last_quantity": 1.0, "last_unit": "штука", "last_date": "18.09.2026"}}
    assert catalog.usage_stats(headers[:13], []) == {}


def test_product_public_shape():
    store = MemoryStore()
    product, _ = add(store, {"name": "Зефир «Шармэль»", "base": "100 г", "kcal": 368, "protein_g": 2.4, "fat_g": 10,
                             "carbs_g": 65.6, "units": "штука=25", "precision": "точно"})
    public = catalog.product_public(product, {"times": 3, "last_quantity": 1.0, "last_unit": "штука", "last_date": "17.09.2026"})
    assert public == {"product_id": "p0000001", "name": "Зефир «Шармэль»", "base": "100 г", "kcal": 368.0,
                      "protein_g": 2.4, "fat_g": 10.0, "carbs_g": 65.6, "units": "штука=25", "default_unit": "штука",
                      "precision": "точно", "times_logged": 3, "last_quantity": 1.0, "last_unit": "штука"}


# --- sets ---------------------------------------------------------------
def kasha_store():
    store = MemoryStore(); make = ids()
    add(store, {"name": "Овсяные хлопья", "base": "100 г", "kcal": 354, "protein_g": 12, "fat_g": 6.3, "carbs_g": 64,
                "precision": "точно"}, make)
    add(store, {"name": "Банан", "base": "100 г", "kcal": 90, "protein_g": 1.5, "fat_g": 0.3, "carbs_g": 23.7,
                "units": "штука=120"}, make)
    return store


KASHA = [{"product_id": "p0000001", "quantity": 35, "unit": "г"}, {"product_id": "p0000002", "quantity": 1, "unit": "штука"}]


def test_save_set_writes_ordered_rows_with_product_names():
    store = kasha_store()
    saved = catalog.save_set(store, "Овсяная каша, стандарт", KASHA)
    assert [(i.product_name, i.quantity, i.unit, i.order) for i in saved] == [("Овсяные хлопья", 35.0, "г", 1), ("Банан", 1.0, "штука", 2)]
    assert store.set_rows == [["Овсяная каша, стандарт", "p0000001", "Овсяные хлопья", 35.0, "г", 1, ""],
                              ["Овсяная каша, стандарт", "p0000002", "Банан", 1.0, "штука", 2, ""]]


def test_save_set_replaces_the_previous_composition():
    store = kasha_store()
    catalog.save_set(store, "Овсяная каша, стандарт", KASHA)
    catalog.save_set(store, "овсяная каша, стандарт", KASHA[:1])
    assert len(store.set_rows) == 1


@pytest.mark.parametrize("name,items", [
    ("", KASHA), ("Каша", []), ("Каша", [{"product_id": "p9999999", "quantity": 1, "unit": "г"}]),
    ("Каша", [{"product_id": "p0000001", "quantity": 1, "unit": "штука"}]),
    ("Каша", [{"product_id": "p0000001", "quantity": 0, "unit": "г"}]),
])
def test_save_set_rejects_bad_input_without_writing(name, items):
    store = kasha_store()
    with pytest.raises(ValueError):
        catalog.save_set(store, name, items)
    assert store.set_rows == []


def test_find_sets_returns_composition_and_totals():
    store = kasha_store()
    catalog.save_set(store, "Овсяная каша, стандарт", KASHA)
    found = catalog.find_sets(store, "каша")
    assert len(found) == 1 and found[0]["set_name"] == "Овсяная каша, стандарт"
    assert found[0]["kcal"] == round(123.9 + 108.0, 1)
    assert found[0]["items"][1] == {"product_id": "p0000002", "product": "Банан", "quantity": 1.0, "unit": "штука"}
    assert catalog.find_sets(store, "") == found and catalog.find_sets(store, "суп") == []


def test_find_sets_marks_a_hidden_product_as_missing():
    store = kasha_store()
    catalog.save_set(store, "Овсяная каша, стандарт", KASHA)
    store.product_rows[1][15] = "да"
    found = catalog.find_sets(store, "каша")[0]
    assert found["items"][1]["missing"] is True and found["kcal"] == 123.9


def test_hidden_set_rows_are_ignored():
    store = kasha_store()
    catalog.save_set(store, "Овсяная каша, стандарт", KASHA)
    for row in store.set_rows:
        row[6] = "да"
    assert catalog.load_sets(store) == {}
