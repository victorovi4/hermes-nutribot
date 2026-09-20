"""Fewer spreadsheet reads for the app: a per-request diary cache and short-lived shared catalog caches.

Google allows 60 reads a minute per user, and every read costs a few hundred milliseconds, so one screen
must not read the same tab twice. The diary itself is never shared between requests: the bot writes to it too.
The read-back check after a write (`read_food_rows`) always goes to the real spreadsheet.
"""
from __future__ import annotations

import copy
import time
from typing import Any, Callable

from nutricore.schema import FOOD_TAB

CATALOG_TTL = 60.0
TARGETS_TTL = 600.0


class CachingStore:
    def __init__(self, store, shared: dict[str, Any], clock: Callable[[], float] = time.monotonic):
        self._store = store
        self._shared = shared
        self._clock = clock
        self._food: tuple[list[Any], list[list[Any]]] | None = None
        self._numbers: list[Any] | None = None
        self.timings: list[tuple[str, float]] = []      # (store method, seconds) for every real spreadsheet call

    def _timed(self, name: str, call: Callable[[], Any]):
        started = time.perf_counter()
        try:
            return call()
        finally:
            self.timings.append((name, time.perf_counter() - started))

    def __getattr__(self, name):                       # everything not cached goes straight to the real store
        target = getattr(self._store, name)
        if not callable(target):
            return target
        return lambda *args, **kwargs: self._timed(name, lambda: target(*args, **kwargs))

    def preload(self) -> None:
        """Fill the diary and the catalog caches with one spreadsheet request, when the store can do that."""
        reader = getattr(self._store, "read_snapshot", None)
        if reader is None:
            return
        snapshot = self._timed("read_snapshot", reader)
        self._food = snapshot["food"]
        self._numbers = None
        now = self._clock()
        for key in ("products", "sets", "targets"):
            self._shared[key] = (now, snapshot[key])

    # --- shared, time-limited -------------------------------------------
    def _shared_read(self, key: str, ttl: float, loader: Callable[[], Any]):
        entry = self._shared.get(key)
        if entry is None or self._clock() - entry[0] > ttl:
            entry = (self._clock(), loader())
            self._shared[key] = entry
        return copy.deepcopy(entry[1])

    def read_products(self):
        return self._shared_read("products", CATALOG_TTL, lambda: self._timed("read_products", self._store.read_products))

    def read_sets(self):
        return self._shared_read("sets", CATALOG_TTL, lambda: self._timed("read_sets", self._store.read_sets))

    def read_targets(self):
        return self._shared_read("targets", TARGETS_TTL, lambda: self._timed("read_targets", self._store.read_targets))

    def append_products(self, rows):
        self._shared.pop("products", None)
        return self._store.append_products(rows)

    def update_product(self, row_number, row):
        self._shared.pop("products", None)
        return self._store.update_product(row_number, row)

    def replace_set(self, name, rows):
        self._shared.pop("sets", None)
        return self._store.replace_set(name, rows)

    # --- the diary, cached for one request only -------------------------
    def read_food(self):
        if self._food is None:
            self._food = self._timed("read_food", self._store.read_food)
            self._numbers = None
        headers, rows = self._food
        return list(headers), copy.deepcopy(rows)

    def food_row_numbers(self) -> list[Any]:
        if self._numbers is None:
            from nutricore.store import food_numbers

            self._numbers = food_numbers(self._store, self.read_food()[1])
        return list(self._numbers)

    def _pad(self, row):
        width = len(self._food[0])
        return (list(row) + [""] * width)[:width]

    def append_food(self, rows):
        numbers = self._store.append_food(rows)
        if self._food is not None and self._numbers is not None and len(numbers) == len(rows):
            self._food[1].extend(self._pad(row) for row in rows)
            self._numbers.extend(numbers)
        else:
            self._food = self._numbers = None           # read again next time rather than guess
        return numbers

    def update_food(self, row_number, row):
        self._store.update_food(row_number, row)
        if self._food is not None and self._numbers is not None and row_number in self._numbers:
            self._food[1][self._numbers.index(row_number)] = self._pad(row)
        else:
            self._food = self._numbers = None

    def write_food_cells(self, row_number, cells):
        self._food = self._numbers = None
        return self._store.write_food_cells(row_number, cells)

    def write_food_cells_many(self, updates):
        self._food = self._numbers = None
        return self._store.write_food_cells_many(updates)

    def delete_rows(self, tab, row_numbers):
        self._store.delete_rows(tab, row_numbers)
        if tab == FOOD_TAB:
            self._food = self._numbers = None           # a spreadsheet renumbers what is left; re-read

