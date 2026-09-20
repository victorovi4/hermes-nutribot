"""What only the local-file diary must promise: private file, real numbers, ids that never come back."""
from __future__ import annotations

import sqlite3
import threading

import pytest

from nutricore.schema import FOOD_HEADERS, FOOD_TAB
from nutricore.sqlite_store import SqliteStore
from nutricore.store import StorageNotConfigured, default_db_path, open_store
from tests.test_store_contract import food_row


@pytest.fixture
def store(tmp_path):
    store = SqliteStore(tmp_path / "nutribot.db")
    store.ensure_schema()
    return store


def test_the_file_is_private_and_reopenable(tmp_path):
    path = tmp_path / "diary.db"
    first = SqliteStore(path); first.ensure_schema()
    first.append_food([food_row()])
    first.close()
    assert oct(path.stat().st_mode)[-3:] == "600"
    again = SqliteStore(path)
    assert again.ensure_schema()["created_tabs"] == [] and len(again.read_food()[1]) == 1


def test_numbers_are_stored_as_numbers_even_when_written_as_text(store):
    store.append_food([food_row(extra={"ккал": "1 234,5", "Количество": "2,5"})])
    row = store.read_food()[1][0]
    assert row[FOOD_HEADERS.index("ккал")] == 1234.5 and row[FOOD_HEADERS.index("Количество")] == 2.5
    raw = sqlite3.connect(store.path).execute("SELECT typeof(c_kkal) FROM food").fetchone()[0]
    assert raw == "real"


def test_row_numbers_are_never_reused(store):
    numbers = store.append_food([food_row(record_id="r1"), food_row(record_id="r2")])
    store.delete_rows(FOOD_TAB, [numbers[1]])
    again = store.append_food([food_row(record_id="r3")])
    assert again[0] > numbers[1] and store.read_food_rows(numbers[1:]) == {}


def test_targets_round_trip_and_reject_unknown_keys(store):
    store.set_targets({"energy": "1 800–1 900 ккал/день", "protein": "от 120 г"})
    assert store.read_targets() == {"energy": "1 800–1 900 ккал/день", "protein": "от 120 г", "fat": "", "goal": ""}
    with pytest.raises(ValueError, match="calories"):
        store.set_targets({"calories": "2000"})


def test_two_writers_do_not_corrupt_the_diary(tmp_path):
    path = tmp_path / "diary.db"
    SqliteStore(path).ensure_schema()
    errors = []
    def writer(tag):
        own = SqliteStore(path)                       # a separate connection, as a second process would have
        try:
            for i in range(20):
                own.append_food([food_row(record_id=f"{tag}{i:03d}")])
        except Exception as exc:                      # pragma: no cover - only on a real failure
            errors.append(exc)
        finally:
            own.close()
    threads = [threading.Thread(target=writer, args=(tag,)) for tag in ("a", "b")]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert not errors
    ids = [r[FOOD_HEADERS.index("ID записи")] for r in SqliteStore(path).read_food()[1]]
    assert len(ids) == 40 and len(set(ids)) == 40


def test_factory_picks_the_backend_and_explains_what_is_missing(tmp_path, monkeypatch):
    env = {"NUTRI_STORAGE": "sqlite", "NUTRI_DB_PATH": str(tmp_path / "x.db")}
    assert isinstance(open_store(env), SqliteStore)
    assert isinstance(open_store({"HERMES_HOME": str(tmp_path)}), SqliteStore)          # sqlite is the default
    assert default_db_path({"HERMES_HOME": str(tmp_path)}) == tmp_path / "nutribot.db"
    with pytest.raises(StorageNotConfigured, match="NUTRI_SPREADSHEET_ID"):
        open_store({"NUTRI_STORAGE": "sheets"})
    with pytest.raises(StorageNotConfigured, match="NUTRI_STORAGE"):
        open_store({"NUTRI_STORAGE": "notion"})
