from __future__ import annotations

import copy

import pytest

from nutricore import migrate
from tests.fakes import BASE_HEADERS, FULL_HEADERS, MemoryStore


def ids():
    counter = {"n": 0}
    def make(prefix):
        counter["n"] += 1
        return f"{prefix}{counter['n']:07d}"
    return make


def old_row(product, kcal, date="18.09.2026"):
    return [date, "Обед", product, "100 г", kcal, 1.0, 2.0, 3.0, "", "", "", "", ""]


def history():
    return MemoryStore(headers=FULL_HEADERS, food_rows=[old_row("Гречка", 97.0), old_row("Тунец", 90.0), [], old_row("Манго", 60.0, "17.09.2026")])


def test_assign_record_ids_fills_only_the_extra_columns():
    store = history(); before = copy.deepcopy(store.food_rows)
    report = migrate.assign_record_ids(store, dry_run=False, id_factory=ids())
    assert report == {"rows_total": 3, "rows_assigned": 3, "totals_equal": True}
    for old, new in zip(before, store.food_rows):
        assert new[:13] == old[:13]
    assert [row[13] for row in store.food_rows if row] == ["r0000001", "r0000002", "r0000003"]
    assert {row[19] for row in store.food_rows if row} == {"миграция"}
    assert store.food_rows[2] == []


def test_assign_record_ids_is_idempotent_and_supports_dry_run():
    store = history()
    assert migrate.assign_record_ids(store, dry_run=True, id_factory=ids())["rows_assigned"] == 3
    assert store.writes == []
    migrate.assign_record_ids(store, dry_run=False, id_factory=ids())
    writes = len(store.writes)
    assert migrate.assign_record_ids(store, dry_run=False, id_factory=ids())["rows_assigned"] == 0
    assert len(store.writes) == writes


def test_assign_record_ids_needs_the_new_schema():
    store = MemoryStore(headers=BASE_HEADERS, food_rows=[old_row("Гречка", 97.0)])
    with pytest.raises(RuntimeError, match="ensure-schema"):
        migrate.assign_record_ids(store, dry_run=False)


def test_totals_mismatch_is_reported():
    store = history()
    original = store.read_all_day_totals
    calls = {"n": 0}
    def drifting():
        calls["n"] += 1
        totals = original()
        if calls["n"] > 1:
            totals["18.09.2026"]["kcal"] += 5
        return totals
    store.read_all_day_totals = drifting
    assert migrate.assign_record_ids(store, dry_run=False, id_factory=ids())["totals_equal"] is False


def test_export_rows_lists_every_filled_row_with_its_number():
    rows = migrate.export_rows(history())
    assert [r["row_number"] for r in rows] == [2, 3, 5]
    assert rows[0]["product"] == "Гречка" and rows[0]["kcal"] == 97.0 and rows[2]["date"] == "17.09.2026"


# --- catalog from history ---------------------------------------------------
def linked_history():
    store = MemoryStore(headers=FULL_HEADERS, food_rows=[
        ["17.09.2026", "Завтрак", "Творог 9%", "150 г", 238.5, 25.1, 13.5, 3.0, "", "", "", "", ""],
        ["18.09.2026", "Перекус", "Зефир, тот же", "2 штуки по ≈30 ккал", 60.0, 1.2, 0.0, 13.4, "", "", "", "", ""],
        ["18.09.2026", "Обед", "Творог", "100 г", 300.0, 16.7, 9.0, 2.0, "", "", "", "", ""],
    ])
    migrate.assign_record_ids(store, dry_run=False, id_factory=ids())
    store.writes.clear()
    return store


DRAFT = [
    {"name": "Творог 9%", "aliases": ["Творог"], "base": "100 г", "kcal": 159, "protein_g": 16.7, "fat_g": 9, "carbs_g": 2,
     "precision": "точно", "source": "этикетка",
     "rows": [{"row_number": 2, "quantity": 150, "unit": "г"}, {"row_number": 4, "quantity": 100, "unit": "г"},
              {"row_number": 99, "quantity": 1, "unit": "г"}]},
    {"name": "Зефир", "base": "1 шт", "kcal": 30, "protein_g": 0.6, "fat_g": 0, "carbs_g": 6.7,
     "rows": [{"row_number": 3, "quantity": 2, "unit": "штука"}]},
]


def test_apply_catalog_creates_cards_and_links_rows_within_tolerance():
    store = linked_history(); before = copy.deepcopy(store.food_rows)
    report = migrate.apply_catalog(store, DRAFT, dry_run=False, today="19.09.2026", id_factory=ids())
    assert report["products_created"] == 2 and report["rows_linked"] == 2 and report["totals_equal"] is True
    assert [(s["row_number"], s["reason"].split(":")[0]) for s in report["rows_skipped"]] == [(4, "kcal_mismatch"), (99, "row_not_found")]
    assert [row[:14] for row in store.food_rows] == [row[:14] for row in before]          # A–M and record ids untouched
    assert store.food_rows[0][14:17] == ["p0000001", 150.0, "г"] and store.food_rows[1][14:17] == ["p0000002", 2.0, "штука"]
    assert store.food_rows[2][14] == ""
    assert [row[1] for row in store.product_rows] == ["Творог 9%", "Зефир"] and store.product_rows[0][14] == "миграция"
    appends = [w for w in store.writes if w[0] == "append_products"]
    assert len(appends) == 1                                                                # one bulk append, not one per card


def test_apply_catalog_dry_run_and_rerun():
    store = linked_history()
    dry = migrate.apply_catalog(store, DRAFT, dry_run=True, today="19.09.2026", id_factory=ids())
    assert dry["products_created"] == 2 and dry["rows_linked"] == 2 and store.writes == [] and store.product_rows == []
    migrate.apply_catalog(store, DRAFT, dry_run=False, today="19.09.2026", id_factory=ids())
    writes = len(store.writes)
    again = migrate.apply_catalog(store, DRAFT, dry_run=False, today="19.09.2026", id_factory=ids())
    assert again["products_created"] == 0 and again["rows_linked"] == 0 and again["rows_already_linked"] == 2
    assert len(store.writes) == writes and len(store.product_rows) == 2


def test_apply_catalog_merges_duplicate_names_inside_the_draft_and_reports_bad_units():
    store = linked_history()
    draft = [dict(DRAFT[1], rows=[{"row_number": 3, "quantity": 2, "unit": "ложка"}]),
             {"name": "зефир", "base": "1 шт", "kcal": 31, "protein_g": 0.6, "fat_g": 0, "carbs_g": 6.7, "rows": []}]
    report = migrate.apply_catalog(store, draft, dry_run=False, today="19.09.2026", id_factory=ids())
    assert report["products_created"] == 1 and len(store.product_rows) == 1
    assert report["rows_skipped"][0]["reason"].startswith("bad_unit")


def test_catalog_report_is_readable():
    store = linked_history()
    report = migrate.apply_catalog(store, DRAFT, dry_run=True, today="19.09.2026", id_factory=ids())
    text = migrate.render_catalog_report(report)
    assert "| Творог 9% | 100 г | 159 · 16,7 · 9 · 2 |" in text and "kcal_mismatch" in text and "Зефир" in text
