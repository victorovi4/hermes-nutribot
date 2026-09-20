"""Text and date helpers shared by the diary ledger and the product catalog."""
from __future__ import annotations

import re
from typing import Any


def norm(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().replace("ё", "е").split())


def normalize_date(value: str) -> str:
    value = str(value or "").strip()
    if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", value):
        return value
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", value)
    if match:
        return f"{match.group(3)}.{match.group(2)}.{match.group(1)}"
    raise ValueError("date must be DD.MM.YYYY or YYYY-MM-DD")


def date_sort_key(value: str) -> tuple[int, int, int]:
    day, month, year = normalize_date(value).split(".")
    return int(year), int(month), int(day)


def matches_query(name: Any, query: str) -> bool:
    haystack = norm(name)
    tokens = re.findall(r"[\w]+", norm(query), flags=re.UNICODE)
    if not tokens:
        return False
    for token in tokens:
        if token in haystack:
            continue
        # A one-character suffix trim covers common Russian inflections
        # (e.g. "яйцо" → "яйца") without inventing product names.
        if len(token) >= 4 and token[:-1] in haystack:
            continue
        return False
    return True
