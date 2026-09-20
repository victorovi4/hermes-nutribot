"""One storage interface, two backends: a Google spreadsheet or a local SQLite file.

Everything above (the diary, the catalog, the app) talks to a store object and never knows which one it is.
The choice comes from the environment, so the same code serves a user with Google and a user without it.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

STORAGE_SHEETS = "sheets"
STORAGE_SQLITE = "sqlite"
STORAGES = (STORAGE_SQLITE, STORAGE_SHEETS)


@runtime_checkable
class Store(Protocol):
    """What a storage backend must be able to do. See tests/test_store_contract.py — it is the real contract."""

    def ensure_schema(self) -> dict: ...

    # --- the diary ------------------------------------------------------
    def read_food(self) -> tuple[list[Any], list[list[Any]]]: ...
    def food_row_numbers(self) -> list[int]: ...        # the number of every row of read_food(), in the same order
    def append_food(self, rows: list[list[Any]]) -> list[int]: ...
    def update_food(self, row_number: int, row: list[Any]) -> None: ...
    def read_food_rows(self, row_numbers: list[int]) -> dict[int, list[Any]]: ...
    def delete_rows(self, tab: str, row_numbers: list[int]) -> None: ...
    def write_food_cells(self, row_number: int, cells: Mapping[str, Any]) -> None: ...
    def write_food_cells_many(self, updates) -> None: ...
    def read_day_total(self, date: str) -> dict[str, float]: ...
    def read_all_day_totals(self) -> dict[str, dict[str, float]]: ...

    # --- the catalog ----------------------------------------------------
    def read_products(self) -> tuple[list[Any], list[list[Any]]]: ...
    def append_products(self, rows: list[list[Any]]) -> list[int]: ...
    def update_product(self, row_number: int, row: list[Any]) -> None: ...
    def read_sets(self) -> tuple[list[Any], list[list[Any]]]: ...
    def replace_set(self, name: str, rows: list[list[Any]]) -> None: ...

    # --- goals and the whole picture ------------------------------------
    def read_targets(self) -> dict[str, str]: ...
    def set_targets(self, targets: Mapping[str, str]) -> None: ...
    def find_standards(self, query: str) -> list[list[Any]]: ...
    def read_snapshot(self) -> dict[str, Any]: ...


class StorageNotConfigured(RuntimeError):
    """The environment does not say where the diary lives, or says it incompletely."""


def food_numbers(store, rows) -> list[int]:
    """Row numbers for the rows just read. A spreadsheet numbers by position; a database keeps ids."""
    reader = getattr(store, "food_row_numbers", None)
    if reader is not None:
        numbers = list(reader())
        if len(numbers) == len(rows):
            return numbers
    return list(range(2, len(rows) + 2))


def default_db_path(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    home = Path(env.get("HERMES_HOME") or "~/.hermes").expanduser()
    return home / "nutribot.db"


def storage_kind(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    kind = (env.get("NUTRI_STORAGE") or "").strip().lower() or STORAGE_SQLITE
    if kind not in STORAGES:
        raise StorageNotConfigured(
            f"NUTRI_STORAGE={kind!r}: дневник может жить только в «{STORAGE_SQLITE}» (своя база) или «{STORAGE_SHEETS}» (Google-таблица)")
    return kind


def open_store(env: Mapping[str, str] | None = None, *, fast: bool = False, **kwargs) -> Store:
    """Open the diary the environment points at. Called by the bot, the app and the setup wizard alike.

    ``fast=True`` asks for the cloud-friendly variant: for Google that is the light REST client and
    one request per read; for the local file it changes nothing (it is already fast).
    """
    env = os.environ if env is None else env
    kind = storage_kind(env)
    if kind == STORAGE_SQLITE:
        from nutricore.sqlite_store import SqliteStore

        return SqliteStore(Path(env.get("NUTRI_DB_PATH") or default_db_path(env)).expanduser(), **kwargs)

    from nutricore.google_store import GoogleSheetStore, fast_service

    spreadsheet_id = (env.get("NUTRI_SPREADSHEET_ID") or "").strip()
    if not spreadsheet_id:
        raise StorageNotConfigured(
            "не задан NUTRI_SPREADSHEET_ID — номер Google-таблицы дневника. Запусти «hermes nutribot setup», он создаст её сам")
    if fast:
        kwargs.setdefault("service", fast_service())
        kwargs.setdefault("single_read", True)
    return GoogleSheetStore(spreadsheet_id=spreadsheet_id, **kwargs)
