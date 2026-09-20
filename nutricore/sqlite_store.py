"""The diary in a local SQLite file — the no-account option, and the fast one.

Same interface as the Google backend (see nutricore/store.py and tests/test_store_contract.py), same row
shape: (headers, rows) with values in the order of schema.FOOD_HEADERS. Row numbers are database ids:
they survive deletes and are never reused, which is exactly what the diary's «ID записи» logic expects.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Mapping

from nutricore.schema import (
    FOOD_EXTRA_HEADERS,
    FOOD_HEADERS,
    FOOD_TAB,
    NUMERIC_FOOD_HEADERS,
    NUMERIC_PRODUCT_HEADERS,
    NUMERIC_SET_HEADERS,
    PRODUCT_HEADERS,
    PRODUCTS_TAB,
    SET_HEADERS,
    SETS_TAB,
)
from nutricore.textutil import norm

SCHEMA_VERSION = 1
TARGET_KEYS = ("energy", "protein", "fat", "goal")
_NUTRIENT_COLUMNS = ("ккал", "Белки, г", "Жиры, г", "Углеводы, г")
_NUTRIENT_FIELDS = ("kcal", "protein_g", "fat_g", "carbs_g")


def _column(header: str) -> str:
    """A safe column name for a human header: «Белки, г» → c_belki_g. The mapping lives here only."""
    table = str.maketrans({"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
                           "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
                           "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh",
                           "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya"})
    slug = norm(header).translate(table)
    slug = "".join(ch if ch.isalnum() else "_" for ch in slug).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return "c_" + slug


FOOD_COLUMNS = [_column(h) for h in FOOD_HEADERS]
PRODUCT_COLUMNS = [_column(h) for h in PRODUCT_HEADERS]
SET_COLUMNS = [_column(h) for h in SET_HEADERS]
_NUMERIC_FOOD = {i for i, h in enumerate(FOOD_HEADERS) if norm(h) in {norm(n) for n in NUMERIC_FOOD_HEADERS}}
_NUMERIC_PRODUCT = {i for i, h in enumerate(PRODUCT_HEADERS) if norm(h) in {norm(n) for n in NUMERIC_PRODUCT_HEADERS}}
_NUMERIC_SET = {i for i, h in enumerate(SET_HEADERS) if norm(h) in {norm(n) for n in NUMERIC_SET_HEADERS}}
_TABLES = {FOOD_TAB: ("food", FOOD_HEADERS, FOOD_COLUMNS), PRODUCTS_TAB: ("products", PRODUCT_HEADERS, PRODUCT_COLUMNS),
           SETS_TAB: ("sets", SET_HEADERS, SET_COLUMNS)}


def _number(value: Any) -> Any:
    """Numeric columns are stored as numbers; «1 234,5» from a person still becomes 1234.5."""
    if value in ("", None):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(" ", "").replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


class SqliteStore:
    def __init__(self, path: str | Path, timeout: float = 15.0):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._timeout = timeout
        self._local = threading.local()
        self.spreadsheet_id = ""                     # so code written for the Google backend can still ask

    # --- plumbing -------------------------------------------------------
    @property
    def _db(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            new_file = not self.path.exists()
            connection = sqlite3.connect(self.path, timeout=self._timeout, isolation_level=None)
            if new_file:
                os.chmod(self.path, 0o600)           # a food diary is private
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA foreign_keys=ON")
            self._local.connection = connection
        return connection

    def _write(self, statements: Iterable[tuple[str, tuple]]) -> None:
        db = self._db
        db.execute("BEGIN IMMEDIATE")                # one writer at a time; readers keep reading (WAL)
        try:
            for sql, args in statements:
                db.execute(sql, args)
        except Exception:
            db.execute("ROLLBACK")
            raise
        db.execute("COMMIT")

    def close(self) -> None:
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            connection.close()
            self._local.connection = None

    # --- schema ---------------------------------------------------------
    def ensure_schema(self) -> dict:
        def create(table: str, columns: list[str]) -> str:
            body = ", ".join(f"{name} TEXT" if name not in _numeric_names(table) else f"{name} REAL" for name in columns)
            return f"CREATE TABLE IF NOT EXISTS {table} (n INTEGER PRIMARY KEY AUTOINCREMENT, {body})"

        def _numeric_names(table: str) -> set[str]:
            mapping = {"food": (_NUMERIC_FOOD, FOOD_COLUMNS), "products": (_NUMERIC_PRODUCT, PRODUCT_COLUMNS),
                       "sets": (_NUMERIC_SET, SET_COLUMNS)}
            numeric, columns = mapping[table]
            return {columns[i] for i in numeric}

        before = self._db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        existing = {row[0] for row in before}
        self._write([
            (create("food", FOOD_COLUMNS), ()),
            (create("products", PRODUCT_COLUMNS), ()),
            (create("sets", SET_COLUMNS), ()),
            ("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)", ()),
            ("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)", ()),
            (f"CREATE INDEX IF NOT EXISTS food_by_date ON food ({FOOD_COLUMNS[0]})", ()),
            (f"CREATE INDEX IF NOT EXISTS food_by_record ON food ({FOOD_COLUMNS[FOOD_HEADERS.index('ID записи')]})", ()),
            ("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)),
        ])
        created = [name for name in ("food", "products", "sets", "settings") if name not in existing]
        return {"created_tabs": created, "added_food_headers": FOOD_EXTRA_HEADERS if "food" in created else []}

    # --- generic table access -------------------------------------------
    def _read_table(self, table: str, headers: list[str], columns: list[str]):
        rows = self._db.execute(f"SELECT n, {', '.join(columns)} FROM {table} ORDER BY n").fetchall()
        return list(headers), [[("" if value is None else value) for value in row[1:]] for row in rows]

    def _numbers(self, table: str) -> list[int]:
        return [row[0] for row in self._db.execute(f"SELECT n FROM {table} ORDER BY n").fetchall()]

    def _values(self, row: list[Any], headers: list[str], numeric: set[int]) -> list[Any]:
        padded = list(row) + [""] * (len(headers) - len(row))
        return [_number(v) if i in numeric else ("" if v is None else str(v)) for i, v in enumerate(padded[:len(headers)])]

    def _append(self, table: str, headers: list[str], columns: list[str], numeric: set[int], rows) -> list[int]:
        placeholders = ", ".join("?" * len(columns))
        sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
        db = self._db
        db.execute("BEGIN IMMEDIATE")
        try:
            numbers = []
            for row in rows:
                cursor = db.execute(sql, tuple(self._values(row, headers, numeric)))
                numbers.append(int(cursor.lastrowid))
        except Exception:
            db.execute("ROLLBACK")
            raise
        db.execute("COMMIT")
        return numbers

    def _update(self, table: str, headers: list[str], columns: list[str], numeric: set[int], number: int, row) -> None:
        assignments = ", ".join(f"{name} = ?" for name in columns)
        self._write([(f"UPDATE {table} SET {assignments} WHERE n = ?", tuple(self._values(row, headers, numeric)) + (int(number),))])

    # --- the diary ------------------------------------------------------
    def read_food(self):
        return self._read_table("food", FOOD_HEADERS, FOOD_COLUMNS)

    def food_row_numbers(self) -> list[int]:
        return self._numbers("food")

    def append_food(self, rows) -> list[int]:
        return self._append("food", FOOD_HEADERS, FOOD_COLUMNS, _NUMERIC_FOOD, rows)

    def update_food(self, row_number: int, row) -> None:
        self._update("food", FOOD_HEADERS, FOOD_COLUMNS, _NUMERIC_FOOD, row_number, row)

    def read_food_rows(self, row_numbers) -> dict[int, list[Any]]:
        wanted = [int(n) for n in row_numbers]
        if not wanted:
            return {}
        marks = ", ".join("?" * len(wanted))
        rows = self._db.execute(f"SELECT n, {', '.join(FOOD_COLUMNS)} FROM food WHERE n IN ({marks})", tuple(wanted)).fetchall()
        return {int(row[0]): [("" if value is None else value) for value in row[1:]] for row in rows}

    def delete_rows(self, tab: str, row_numbers) -> None:
        table = _TABLES[tab][0] if tab in _TABLES else tab
        numbers = sorted({int(n) for n in row_numbers})
        if numbers:
            marks = ", ".join("?" * len(numbers))
            self._write([(f"DELETE FROM {table} WHERE n IN ({marks})", tuple(numbers))])

    def write_food_cells(self, row_number: int, cells: Mapping[str, Any]) -> None:
        self.write_food_cells_many([(row_number, cells)])

    def write_food_cells_many(self, updates) -> None:
        allowed = {norm(h): h for h in FOOD_EXTRA_HEADERS}
        bad = sorted({name for _n, cells in updates for name in cells if norm(name) not in allowed})
        if bad:
            raise ValueError(f"only extra diary columns can be written cell-wise, got: {', '.join(bad)}")
        statements = []
        for number, cells in updates:
            names, values = [], []
            for name, value in cells.items():
                index = FOOD_HEADERS.index(allowed[norm(name)])
                names.append(f"{FOOD_COLUMNS[index]} = ?")
                values.append(_number(value) if index in _NUMERIC_FOOD else ("" if value is None else str(value)))
            statements.append((f"UPDATE food SET {', '.join(names)} WHERE n = ?", tuple(values) + (int(number),)))
        self._write(statements)

    def _totals(self, date: str | None) -> dict[str, dict[str, float]]:
        columns = ", ".join(f"SUM(COALESCE({FOOD_COLUMNS[FOOD_HEADERS.index(h)]}, 0))" for h in _NUTRIENT_COLUMNS)
        date_column = FOOD_COLUMNS[0]
        sql = f"SELECT {date_column}, {columns} FROM food WHERE {date_column} <> '' GROUP BY {date_column}"
        args: tuple = ()
        if date is not None:
            sql = f"SELECT {date_column}, {columns} FROM food WHERE {date_column} = ? GROUP BY {date_column}"
            args = (date,)
        return {str(row[0]): {field: round(float(row[i + 1] or 0.0), 4) for i, field in enumerate(_NUTRIENT_FIELDS)}
                for row in self._db.execute(sql, args).fetchall()}

    def read_day_total(self, date: str) -> dict[str, float]:
        return self._totals(date).get(date, {field: 0.0 for field in _NUTRIENT_FIELDS})

    def read_all_day_totals(self) -> dict[str, dict[str, float]]:
        return self._totals(None)

    # --- the catalog ----------------------------------------------------
    def read_products(self):
        return self._read_table("products", PRODUCT_HEADERS, PRODUCT_COLUMNS)

    def append_products(self, rows) -> list[int]:
        return self._append("products", PRODUCT_HEADERS, PRODUCT_COLUMNS, _NUMERIC_PRODUCT, rows)

    def update_product(self, row_number: int, row) -> None:
        self._update("products", PRODUCT_HEADERS, PRODUCT_COLUMNS, _NUMERIC_PRODUCT, row_number, row)

    def read_sets(self):
        return self._read_table("sets", SET_HEADERS, SET_COLUMNS)

    def replace_set(self, name: str, rows) -> None:
        clean = " ".join(str(name or "").split())
        keep = [row for row in self._db.execute(f"SELECT n, {SET_COLUMNS[0]} FROM sets").fetchall()
                if norm(row[1]) == norm(clean)]
        if keep:
            marks = ", ".join("?" * len(keep))
            self._write([(f"DELETE FROM sets WHERE n IN ({marks})", tuple(int(row[0]) for row in keep))])
        if rows:
            self._append("sets", SET_HEADERS, SET_COLUMNS, _NUMERIC_SET, rows)

    # --- goals and the whole picture ------------------------------------
    def read_targets(self) -> dict[str, str]:
        stored = {row[0]: row[1] for row in self._db.execute("SELECT key, value FROM settings").fetchall()}
        return {key: stored.get(key, "") for key in TARGET_KEYS}

    def set_targets(self, targets: Mapping[str, str]) -> None:
        unknown = [key for key in targets if key not in TARGET_KEYS]
        if unknown:
            raise ValueError(f"unknown target keys: {', '.join(unknown)}; expected {', '.join(TARGET_KEYS)}")
        self._write([("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
                     for key, value in targets.items()])

    def find_standards(self, query: str) -> list[list[Any]]:
        """Kept for compatibility with the Google diary's old «Стандартные порции» tab; sets replaced it."""
        return []

    def read_snapshot(self) -> dict[str, Any]:
        return {"food": self.read_food(), "products": self.read_products(),
                "sets": self.read_sets(), "targets": self.read_targets()}
