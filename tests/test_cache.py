from __future__ import annotations

from nutricore import app, catalog
from nutricore.cache import CachingStore
from tests.fakes import FULL_HEADERS, MemoryStore

DATE = "19.09.2026"
READS = ("read_food", "read_products", "read_sets", "read_targets", "read_food_rows", "read_day_total", "read_all_day_totals")


class Counting(MemoryStore):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.calls = []

    def __getattribute__(self, name):
        value = super().__getattribute__(name)
        if name in READS:
            calls = super().__getattribute__("calls")
            def counted(*a, **kw):
                calls.append(name)
                return value(*a, **kw)
            return counted
        return value


def stocked():
    store = Counting(headers=FULL_HEADERS)
    catalog.upsert_product(store, {"name": "Зефир", "base": "1 шт", "kcal": 30, "protein_g": 0.6, "fat_g": 0, "carbs_g": 6.7},
                           origin="миграция", today=DATE, id_factory=lambda p: p + "0000001")
    store.calls.clear()
    return store


def add(cached, record_id="r00000001", qty=2):
    return app.add_entries(cached, date=DATE, meal="Ужин", items=[{"record_id": record_id, "product_id": "p0000001", "quantity": qty, "unit": "штука"}])


def test_one_add_reads_each_tab_once_and_verifies_the_written_row():
    store = stocked(); shared = {}
    result = add(CachingStore(store, shared))
    assert result["totals"]["kcal"] == 60.0 and len(store.food_rows) == 1
    assert store.calls.count("read_food") == 1 and store.calls.count("read_products") == 1
    assert store.calls.count("read_food_rows") == 1                      # the read-back check still hits the real table
    assert "read_day_total" not in store.calls and store.calls.count("read_targets") == 1


def test_catalog_and_targets_are_shared_between_requests_but_the_diary_is_not():
    store = stocked(); shared = {}
    add(CachingStore(store, shared)); store.calls.clear()
    view = app.day_view(CachingStore(store, shared), DATE)
    assert view["totals"]["kcal"] == 60.0
    assert store.calls == ["read_food"]


def test_shared_cache_expires():
    store = stocked(); shared = {}; now = {"t": 0.0}
    app.day_view(CachingStore(store, shared, clock=lambda: now["t"]), DATE); store.calls.clear()
    now["t"] = 61.0
    app.day_view(CachingStore(store, shared, clock=lambda: now["t"]), DATE)
    assert "read_products" in store.calls and "read_targets" not in store.calls
    now["t"] = 700.0; store.calls.clear()
    app.day_view(CachingStore(store, shared, clock=lambda: now["t"]), DATE)
    assert "read_targets" in store.calls


def test_writes_keep_the_request_cache_consistent():
    store = stocked(); cached = CachingStore(store, {})
    add(cached); add(cached, "r00000002", 1)
    assert app.update_entry(cached, "r00000001", {"quantity": 1})["totals"]["kcal"] == 60.0
    assert app.delete_entry(cached, "r00000002")["totals"]["kcal"] == 30.0
    # Adds and edits are served from the cache; a delete drops it, because a spreadsheet renumbers
    # whatever is left and stale numbers would point at the wrong rows.
    assert store.calls.count("read_food") == 2
    fresh = app.day_view(CachingStore(store, {}), DATE)
    assert fresh["totals"]["kcal"] == 30.0 and [e["record_id"] for e in fresh["entries"]] == ["r00000001"]


def test_new_product_invalidates_the_shared_catalog():
    store = stocked(); shared = {}
    app.catalog_view(CachingStore(store, shared))
    card = {"name": "Кефир", "base": "100 г", "kcal": 57, "protein_g": 3, "fat_g": 3.2, "carbs_g": 4}
    app.add_entries(CachingStore(store, shared), date=DATE, meal="Ужин", items=[{"record_id": "r0000000a", "new_product": card, "quantity": 200, "unit": "г"}])
    names = [p["name"] for p in app.catalog_view(CachingStore(store, shared))["products"]]
    assert "Кефир" in names


def test_preload_fills_every_cache_from_one_snapshot():
    store = stocked(); add(CachingStore(store, {})); store.calls.clear()
    def snapshot():
        store.calls.append("read_snapshot")
        return {"food": MemoryStore.read_food(store), "products": MemoryStore.read_products(store),
                "sets": MemoryStore.read_sets(store), "targets": MemoryStore.read_targets(store)}
    store.read_snapshot = snapshot
    cached = CachingStore(store, {}); cached.preload()
    view = app.day_view(cached, DATE); catalog_view = app.catalog_view(cached)
    assert view["totals"]["kcal"] == 60.0 and catalog_view["products"][0]["name"] == "Зефир"
    assert store.calls == ["read_snapshot"]
    assert [name for name, _ in cached.timings] == ["read_snapshot"]


def test_preload_is_a_no_op_for_stores_without_snapshots():
    store = stocked(); cached = CachingStore(store, {}); cached.preload()
    assert app.day_view(cached, DATE)["totals"]["kcal"] == 0
