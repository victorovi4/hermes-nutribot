from __future__ import annotations

import copy

import pytest

from nutricore import ledger as ledger_mod
from tests.fakes import FULL_HEADERS, MemoryStore, item


def test_log_food_appends_numeric_values_and_reads_back():
    store = MemoryStore()
    result = ledger_mod.execute(store, action="log_food", date="12.09.2026", items=[item()])
    assert result["success"] is True
    assert result["status"] == "written"
    assert result["rows"] == [2]
    row = store.food_rows[0]
    assert row[0:4] == ["12.09.2026", "Завтрак", "Творог 9%", "175 г"]
    assert row[4:8] == [278.25, 29.23, 15.75, 3.5]
    assert row[10:13] == ["точная", "этикетка v1", "09:15"]
    assert result["verified_rows"][0]["product"] == "Творог 9%"
    assert result["day_total"]["kcal"] == pytest.approx(278.25)


def test_exact_retry_is_idempotent_no_duplicate():
    existing = [["12.09.2026", "Завтрак", "Творог 9%", "175 г", 278.25, 29.23, 15.75, 3.5, "по этикетке", ""]]
    store = MemoryStore(existing)
    result = ledger_mod.execute(store, action="log_food", date="12.09.2026", items=[item()])
    assert result["status"] == "already_recorded"
    assert store.writes == []
    assert len(store.food_rows) == 1


def test_exact_retry_accepts_sheets_time_rendering_with_seconds():
    existing = [["12.09.2026", "Завтрак", "Творог 9%", "175 г", 278.25, 29.23, 15.75, 3.5, "по этикетке", "", "точная", "этикетка v1", "09:15:00"]]
    store = MemoryStore(existing)
    result = ledger_mod.execute(store, action="log_food", date="12.09.2026", items=[item()])
    assert result["status"] == "already_recorded"
    assert store.writes == []


def test_correct_food_updates_single_matching_row():
    existing = [["12.09.2026", "Завтрак", "Творог 9%", "175 г", 278.25, 29.23, 15.75, 3.5, "оценка", "", "нестандартная", "рецепт v1", "08:55"]]
    store = MemoryStore(existing)
    corrected = item(portion="200 г", kcal=318.0)
    result = ledger_mod.execute(store, action="correct_food", date="12.09.2026", items=[corrected])
    assert result["status"] == "updated"
    assert result["rows"] == [2]
    assert store.food_rows[0][3] == "200 г"
    assert store.food_rows[0][4] == 318.0


def test_correction_preserves_metadata_when_fields_are_omitted():
    existing = [["12.09.2026", "Завтрак", "Творог 9%", "175 г", 278.25, 29.23, 15.75, 3.5, "оценка", "", "нестандартная", "рецепт v1", "08:55"]]
    store = MemoryStore(existing)
    corrected = item(portion="200 г", kcal=318.0)
    for key in ("portion_status", "estimate_version", "consumed_time"):
        corrected.pop(key)
    result = ledger_mod.execute(store, action="correct_food", date="12.09.2026", items=[corrected])
    assert result["success"] is True
    assert store.food_rows[0][10:13] == ["нестандартная", "рецепт v1", "08:55"]


def test_correction_refuses_ambiguous_match():
    row = ["12.09.2026", "Завтрак", "Творог 9%", "100 г", 159, 16.7, 9, 2, "", ""]
    store = MemoryStore([row, list(row)])
    result = ledger_mod.execute(store, action="correct_food", date="12.09.2026", items=[item()])
    assert result["success"] is False
    assert result["error"] == "ambiguous_match"
    assert result["candidate_rows"] == [2, 3]
    assert store.writes == []


def test_dry_run_plans_without_writing():
    store = MemoryStore()
    result = ledger_mod.execute(store, action="log_food", date="12.09.2026", items=[item()], dry_run=True)
    assert result["success"] is True
    assert result["status"] == "dry_run"
    assert result["planned"][0]["operation"] == "append"
    assert store.writes == []


def test_live_header_order_is_respected():
    headers = ["Продукт / блюдо", "Дата", "ккал", "Приём пищи", "Порция", "Белки, г", "Жиры, г", "Углеводы, г", "Источник / уточнение", "Самочувствие / симптомы"]
    store = MemoryStore(headers=headers)
    ledger_mod.execute(store, action="log_food", date="12.09.2026", items=[item()])
    assert store.food_rows[0][0] == "Творог 9%"
    assert store.food_rows[0][1] == "12.09.2026"
    assert store.food_rows[0][2] == 278.25


def test_day_status_is_read_only():
    existing = [["12.09.2026", "Завтрак", "Творог 9%", "175 г", 278.25, 29.23, 15.75, 3.5, "", ""]]
    store = MemoryStore(existing)
    result = ledger_mod.execute(store, action="day_status", date="12.09.2026")
    assert result["success"] is True
    assert result["day_total"]["protein_g"] == pytest.approx(29.23)
    assert result["targets"]["energy"] == "1 800–1 900 ккал/день"
    assert store.writes == []


def test_get_row_returns_structured_real_values_without_writing():
    existing = [["12.09.2026", "Завтрак", "Колбаса еврейская", "50 г", 210, 8, 18, 1, "по этикетке", "без замечаний", "точная", "v1", "09:15"]]
    store = MemoryStore(existing)
    before = copy.deepcopy(store.food_rows)
    result = ledger_mod.execute(store, action="get_row", date="", row_number=2)
    assert result == {
        "success": True, "status": "read", "row": {
            "row_number": 2, "date": "12.09.2026", "meal": "Завтрак",
            "product": "Колбаса еврейская", "portion": "50 г", "kcal": 210,
            "protein_g": 8, "fat_g": 18, "carbs_g": 1, "source": "по этикетке",
            "note": "без замечаний", "consumed_time": "09:15",
        },
    }
    assert store.food_rows == before
    assert store.writes == []


def test_get_row_not_found_never_creates_a_row():
    store = MemoryStore([["12.09.2026", "Завтрак", "Яйцо", "1 шт", 70, 6, 5, 0.5]])
    before = copy.deepcopy(store.food_rows)
    assert ledger_mod.execute(store, action="get_row", row_number=57) == {"success": False, "status": "not_found", "row_number": 57}
    assert store.food_rows == before
    assert store.writes == []


def test_search_food_finds_partial_case_insensitive_historical_product_with_filters():
    rows = [
        ["14.09.2026", "Завтрак", "Яйцо куриное", "2 шт", 140, 12, 10, 1],
        ["15.09.2026", "Обед", "КОЛБАСА ЕВРЕЙСКАЯ полусухая", "50 г", 210, 8, 18, 1],
        ["15.09.2026", "Ужин", "Яйцо перепелиное", "5 шт", 70, 6, 5, 0.5],
    ]
    store = MemoryStore(rows)
    before = copy.deepcopy(store.food_rows)
    result = ledger_mod.execute(store, action="search_food", query="колбаса еврей", date_from="2026-09-15", meal="обед", limit=10)
    assert result["success"] is True
    assert result["count"] == 1
    assert result["results"][0]["row_number"] == 3
    assert result["results"][0]["product"] == "КОЛБАСА ЕВРЕЙСКАЯ полусухая"
    assert store.food_rows == before
    assert store.writes == []


def test_search_food_finds_russian_word_stem_in_historical_product():
    store = MemoryStore([["15.09.2026", "Завтрак", "Яйца куриные", "2 шт", 140, 12, 10, 1]])
    result = ledger_mod.execute(store, action="search_food", query="яйцо")
    assert result["count"] == 1
    assert result["results"][0]["product"] == "Яйца куриные"
    assert store.writes == []


def test_get_day_entries_returns_all_rows_and_formula_total_without_writing():
    rows = [
        ["15.09.2026", "Завтрак", "Яйцо", "2 шт", 140, 12, 10, 1],
        ["15.09.2026", "Обед", "Колбаса", "50 г", 210, 8, 18, 1],
        ["14.09.2026", "Ужин", "Творог", "100 г", 159, 16.7, 9, 2],
    ]
    store = MemoryStore(rows)
    before = copy.deepcopy(store.food_rows)
    result = ledger_mod.execute(store, action="get_day_entries", date="2026-09-15")
    assert result["success"] is True
    assert [entry["row_number"] for entry in result["entries"]] == [2, 3]
    assert result["day_total"] == {"kcal": 350.0, "protein_g": 20.0, "fat_g": 28.0, "carbs_g": 2.0}
    assert store.food_rows == before
    assert store.writes == []


def test_header_map_knows_the_extra_columns():
    mapping = ledger_mod._header_map(FULL_HEADERS)
    assert [mapping[k] for k in ("record_id", "product_id", "quantity", "unit", "set_name", "group_id", "origin")] == list(range(13, 20))
