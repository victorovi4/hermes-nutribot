from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from nutricore import catalog
from nutricore.ids import new_id
from nutricore.store import food_numbers
from nutricore.textutil import date_sort_key as _date_sort_key
from nutricore.textutil import matches_query as _matches_product_query
from nutricore.textutil import norm as _norm
from nutricore.textutil import normalize_date as _date
from nutricore.units import nutrition_for, portion_text, resolve_unit

CANONICAL = {
    "date": {"дата"},
    "meal": {"приём пищи", "прием пищи"},
    "product": {"продукт / блюдо", "продукт/блюдо"},
    "portion": {"порция"},
    "kcal": {"ккал", "калории"},
    "protein_g": {"белки, г", "белки г"},
    "fat_g": {"жиры, г", "жиры г"},
    "carbs_g": {"углеводы, г", "углеводы г"},
    "source": {"источник / уточнение", "источник / допущение", "источник"},
    "symptoms": {"самочувствие / симптомы", "симптомы / примечание", "примечание"},
    "portion_status": {"статус порции"},
    "estimate_version": {"версия оценки / рецепт", "версия оценки/рецепт"},
    "consumed_time": {"время приёма (msk)", "время приема (msk)"},
    "record_id": {"id записи"},
    "product_id": {"id продукта"},
    "quantity": {"количество"},
    "unit": {"мерка"},
    "set_name": {"набор"},
    "group_id": {"группа"},
    "origin": {"откуда"},
}
REQUIRED = ("date", "meal", "product", "portion", "kcal", "protein_g", "fat_g", "carbs_g")
NUMERIC = ("kcal", "protein_g", "fat_g", "carbs_g")


def _number(value: Any, field: str) -> float:
    if isinstance(value, str):
        value = value.replace(" ", "").replace(",", ".")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return number


def _header_map(headers: list[str]) -> dict[str, int]:
    normalized = [_norm(h) for h in headers]
    result: dict[str, int] = {}
    for field, aliases in CANONICAL.items():
        for idx, value in enumerate(normalized):
            if value in {_norm(a) for a in aliases}:
                result[field] = idx
                break
    missing = [field for field in REQUIRED if field not in result]
    if missing:
        raise ValueError("missing required sheet columns: " + ", ".join(missing))
    return result


def _item_values(item: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {
        "meal": str(item.get("meal") or "").strip(),
        "product": str(item.get("product") or "").strip(),
        "portion": str(item.get("portion") or "").strip(),
    }
    for field in ("source", "symptoms", "portion_status", "estimate_version", "consumed_time"):
        if field in item:
            values[field] = str(item.get(field) or "").strip()
    for field in ("meal", "product", "portion"):
        if not values[field]:
            raise ValueError(f"{field} is required")
    for field in NUMERIC:
        values[field] = _number(item.get(field), field)
    if "row_number" in item and item["row_number"] is not None:
        values["row_number"] = int(item["row_number"])
    return values


def _make_row(headers: list[str], mapping: dict[str, int], date: str, item: dict[str, Any]) -> list[Any]:
    row: list[Any] = [""] * len(headers)
    values = {"date": date, **item}
    for field, idx in mapping.items():
        if field in values:
            row[idx] = values[field]
    return row


def _merge_row(existing: list[Any], headers: list[str], mapping: dict[str, int], date: str, item: dict[str, Any]) -> list[Any]:
    row: list[Any] = list(existing) + [""] * max(0, len(headers) - len(existing))
    row = row[:len(headers)]
    values = {"date": date, **item}
    for field, value in values.items():
        if field in mapping and field != "row_number":
            row[mapping[field]] = value
    return row


def _same_value(left: Any, right: Any, numeric: bool = False) -> bool:
    if numeric:
        try:
            return abs(_number(left, "value") - _number(right, "value")) < 1e-6
        except ValueError:
            return False
    return _norm(left) == _norm(right)


def _same_row(left: list[Any], right: list[Any], mapping: dict[str, int]) -> bool:
    for field in REQUIRED + ("source", "symptoms"):
        idx = mapping.get(field)
        if idx is None:
            continue
        lval = left[idx] if idx < len(left) else ""
        rval = right[idx] if idx < len(right) else ""
        if not _same_value(lval, rval, field in NUMERIC):
            return False
    return True


def _row_dict(row_number: int, row: list[Any], mapping: dict[str, int]) -> dict[str, Any]:
    result: dict[str, Any] = {"row_number": row_number}
    for field, idx in mapping.items():
        result[field] = row[idx] if idx < len(row) else ""
    return result


def _public_row(row_number: int, row: list[Any], mapping: dict[str, int]) -> dict[str, Any]:
    """Expose only nutrition fields, with a stable public name for notes."""
    internal = _row_dict(row_number, row, mapping)
    return {
        "row_number": internal["row_number"],
        "date": internal.get("date", ""),
        "meal": internal.get("meal", ""),
        "product": internal.get("product", ""),
        "portion": internal.get("portion", ""),
        "kcal": internal.get("kcal", ""),
        "protein_g": internal.get("protein_g", ""),
        "fat_g": internal.get("fat_g", ""),
        "carbs_g": internal.get("carbs_g", ""),
        "source": internal.get("source", ""),
        "consumed_time": internal.get("consumed_time", ""),
        "note": internal.get("symptoms", ""),
    }


def _matches_date(row: list[Any], mapping: dict[str, int], date: str | None) -> bool:
    return not date or _same_value(row[mapping["date"]] if mapping["date"] < len(row) else "", date)


PRODUCT_COLUMNS = ("record_id", "product_id", "quantity", "unit", "origin")
RECORD_ID = re.compile(r"r[0-9a-f]{8}")


def _is_product_item(item: dict[str, Any]) -> bool:
    return bool(item.get("product_id") or item.get("new_product"))


def _today_msk() -> str:
    return datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y")


def _product_item_values(item: dict[str, Any], product) -> dict[str, Any]:
    """Diary fields of a «product × quantity × unit» item; numbers come from the card, not from the caller."""
    meal = str(item.get("meal") or "").strip()
    if not meal:
        raise ValueError("meal is required")
    quantity = _number(item.get("quantity"), "quantity")
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    unit_name = str(item.get("unit") or "").strip()
    if not unit_name:
        raise ValueError("unit is required for a product item")
    unit = resolve_unit(product, unit_name).name
    values: dict[str, Any] = {
        "meal": meal,
        "product": product.name,
        "portion": portion_text(product, quantity, unit),
        **nutrition_for(product, quantity, unit),
        "source": str(item.get("source") or product.source).strip(),
        "portion_status": "точная" if product.precision == "точно" else "оценочная",
        "product_id": product.id,
        "quantity": quantity,
        "unit": unit,
    }
    for field in ("symptoms", "estimate_version", "consumed_time"):
        if field in item:
            values[field] = str(item.get(field) or "").strip()
    if "row_number" in item and item["row_number"] is not None:
        values["row_number"] = int(item["row_number"])
    return values


def execute(
    store: Any,
    *,
    action: str,
    date: str = "",
    items: list[dict[str, Any]] | None = None,
    dry_run: bool = False,
    row_number: int | None = None,
    query: str = "",
    meal: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 10,
    origin: str = "бот",
    set_name: str = "",
    targets: dict[str, Any] | None = None,
    id_factory: Callable[[str], str] = new_id,
    today: str = "",
    summary: bool = True,
) -> dict[str, Any]:
    """Execute one bounded, verified nutrition-ledger operation."""
    try:
        normalized_date = _date(date) if str(date or "").strip() else ""
        if not normalized_date and action in ("log_food", "correct_food"):
            normalized_date = _today_msk()      # a diary entry without a date is not an entry
        headers, existing = store.read_food()
        numbers = food_numbers(store, existing)
        by_number = dict(zip(numbers, existing))
        mapping = _header_map(headers)

        if action == "get_row":
            if not isinstance(row_number, int) or isinstance(row_number, bool) or row_number < 2:
                return {"success": False, "error": "validation_error", "message": "row_number must be an integer >= 2"}
            row = by_number.get(row_number)
            if row is None or not any(str(value or "").strip() for value in row):
                return {"success": False, "status": "not_found", "row_number": row_number}
            return {"success": True, "status": "read", "row": _public_row(row_number, row, mapping)}

        if action == "search_food":
            needle = _norm(query)
            if not needle:
                return {"success": False, "error": "validation_error", "message": "query is required"}
            try:
                result_limit = int(limit)
            except (TypeError, ValueError):
                return {"success": False, "error": "validation_error", "message": "limit must be an integer"}
            if not 1 <= result_limit <= 100:
                return {"success": False, "error": "validation_error", "message": "limit must be between 1 and 100"}
            start = _date(date_from) if str(date_from or "").strip() else ""
            end = _date(date_to) if str(date_to or "").strip() else ""
            if start and end and _date_sort_key(start) > _date_sort_key(end):
                return {"success": False, "error": "validation_error", "message": "date_from must be on or before date_to"}
            found = []
            for number, row in zip(numbers, existing):
                row_date = str(row[mapping["date"]] if mapping["date"] < len(row) else "").strip()
                product = row[mapping["product"]] if mapping["product"] < len(row) else ""
                row_meal = row[mapping["meal"]] if mapping["meal"] < len(row) else ""
                if not _matches_product_query(product, needle) or (normalized_date and not _same_value(row_date, normalized_date)):
                    continue
                if meal and _norm(meal) != _norm(row_meal):
                    continue
                if start and _date_sort_key(row_date) < _date_sort_key(start):
                    continue
                if end and _date_sort_key(row_date) > _date_sort_key(end):
                    continue
                found.append(_public_row(number, row, mapping))
                if len(found) >= result_limit:
                    break
            return {"success": True, "status": "read", "results": found, "count": len(found)}

        if action == "get_day_entries":
            if not normalized_date:
                return {"success": False, "error": "validation_error", "message": "date is required"}
            entries = [_public_row(number, row, mapping) for number, row in zip(numbers, existing) if _matches_date(row, mapping, normalized_date)]
            return {"success": True, "status": "read", "date": normalized_date, "entries": entries, "day_total": store.read_day_total(normalized_date)}

        if action == "day_status":
            return {
                "success": True,
                "status": "read",
                "date": normalized_date,
                "day_total": store.read_day_total(normalized_date),
                "targets": store.read_targets(),
            }

        if action == "find_standard":
            query = str((items or [{}])[0].get("product") or "").strip()
            return {"success": True, "status": "read", "standards": store.find_standards(query)}

        if action == "find_product":
            if not _norm(query):
                return {"success": False, "error": "validation_error", "message": "query is required"}
            try:
                result_limit = max(1, min(int(limit), 100))
            except (TypeError, ValueError):
                return {"success": False, "error": "validation_error", "message": "limit must be an integer"}
            stats = catalog.usage_stats(headers, existing)
            found = catalog.find_products(store, query, limit=result_limit, stats=stats)
            return {"success": True, "status": "read", "count": len(found),
                    "products": [catalog.product_public(product, stats.get(product.id)) for product in found]}

        if action == "set_targets":
            wanted = {key: str(value).strip() for key, value in (targets or {}).items() if str(value).strip()}
            if not wanted:
                return {"success": False, "error": "validation_error", "message": "targets is required"}
            store.set_targets(wanted)
            return {"success": True, "status": "saved", "targets": store.read_targets()}

        if action == "find_set":
            # Models tend to put the name into set_name here; accept both.
            return {"success": True, "status": "read", "sets": catalog.find_sets(store, query or set_name)}

        if action == "save_set":
            saved = catalog.save_set(store, set_name, items or [], dry_run=dry_run)
            return {"success": True, "status": "dry_run" if dry_run else "saved", "set_name": " ".join(set_name.split()),
                    "items": [{"product_id": i.product_id, "product": i.product_name,
                               "quantity": i.quantity, "unit": i.unit} for i in saved]}

        if action not in {"log_food", "correct_food"}:
            return {"success": False, "error": "unsupported_action"}
        if not items:
            return {"success": False, "error": "items_required"}

        today = today or _today_msk()
        products_by_id: dict[str, Any] | None = None
        groups: dict[str, str] = {}
        prepared: list[dict[str, Any]] = []
        # Cards to create once every item has passed validation: (values of the item, card data).
        pending_cards: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for raw in items:
            if not _is_product_item(raw):
                values = _item_values(raw)
            else:
                if any(column not in mapping for column in PRODUCT_COLUMNS):
                    return {"success": False, "error": "schema_outdated",
                            "message": "в таблице нет колонок продукта; сначала обновить схему"}
                if raw.get("product_id"):
                    if products_by_id is None:
                        products_by_id = {p.id: p for p in catalog.load_products(store)}
                    product = products_by_id.get(str(raw["product_id"]).strip())
                    if product is None:
                        raise ValueError(f"unknown product_id: {raw['product_id']!r}")
                else:
                    product, is_new = catalog.upsert_product(
                        store, raw["new_product"], origin=origin, today=today, id_factory=id_factory, dry_run=True)
                values = _product_item_values(raw, product)
                if not raw.get("product_id") and is_new:
                    pending_cards.append((values, raw["new_product"]))
            if raw.get("record_id"):
                if not RECORD_ID.fullmatch(str(raw["record_id"])):
                    raise ValueError("record_id must look like r + 8 hex characters")
                values["given_record_id"] = str(raw["record_id"])
            item_set = " ".join(str(raw.get("set_name") or "").split())
            if item_set and "set_name" in mapping:
                values["set_name"] = item_set
                values["group_id"] = str(raw.get("group_id") or "").strip() or groups.setdefault(_norm(item_set), id_factory("g"))
            prepared.append(values)
        planned: list[dict[str, Any]] = []
        appends: list[tuple[dict[str, Any], list[Any]]] = []
        updates: list[tuple[int, dict[str, Any], list[Any]]] = []
        duplicates: list[int] = []

        for item in prepared:
            desired = _make_row(headers, mapping, normalized_date, item)
            candidates = []
            for offset, row in zip(numbers, existing):
                if (
                    _same_value(row[mapping["date"]] if mapping["date"] < len(row) else "", normalized_date)
                    and _same_value(row[mapping["meal"]] if mapping["meal"] < len(row) else "", item["meal"])
                    and _same_value(row[mapping["product"]] if mapping["product"] < len(row) else "", item["product"])
                ):
                    candidates.append(offset)

            if action == "log_food":
                # A caller-supplied record id is the idempotency key, so identical content is not a duplicate.
                exact = [] if item.get("given_record_id") else [
                    n for n in candidates if _same_row(existing[n - 2], desired, mapping)]
                if exact:
                    duplicates.append(exact[0])
                    planned.append({"operation": "noop", "row_number": exact[0], "product": item["product"]})
                else:
                    appends.append((item, desired))
                    planned.append({"operation": "append", "product": item["product"],
                                    "portion": item["portion"], "kcal": item["kcal"]})
                continue

            explicit = item.get("row_number")
            if explicit is not None:
                candidates = [explicit] if explicit in by_number else []
                expected_id = item.get("given_record_id")
                if candidates and expected_id and "record_id" in mapping:
                    current = by_number[explicit]
                    if str(current[mapping["record_id"]] if mapping["record_id"] < len(current) else "") != expected_id:
                        return {"success": False, "error": "conflict", "row_number": explicit}
            if not candidates:
                return {"success": False, "error": "match_not_found", "product": item["product"]}
            if len(candidates) > 1:
                return {"success": False, "error": "ambiguous_match", "product": item["product"], "candidate_rows": candidates}
            row_number = candidates[0]
            desired = _merge_row(by_number[row_number], headers, mapping, normalized_date, item)
            updates.append((row_number, item, desired))
            planned.append({"operation": "update", "row_number": row_number, "product": item["product"]})

        if dry_run:
            return {"success": True, "status": "dry_run", "date": normalized_date, "planned": planned}

        created_products: list[dict[str, str]] = []
        for values, card in pending_cards:
            product, is_new = catalog.upsert_product(store, card, origin=origin, today=today, id_factory=id_factory)
            values["product_id"] = product.id
            if is_new:
                created_products.append({"product_id": product.id, "name": product.name})
        for item, desired in appends:
            if "product_id" in mapping and item.get("product_id"):
                desired[mapping["product_id"]] = item["product_id"]
            if "record_id" in mapping:
                desired[mapping["record_id"]] = item.get("given_record_id") or id_factory("r")
            if "origin" in mapping:
                desired[mapping["origin"]] = origin
        for _row_number, item, desired in updates:
            if "product_id" in mapping and item.get("product_id"):
                desired[mapping["product_id"]] = item["product_id"]

        changed_rows: list[int] = []
        expected: dict[int, list[Any]] = {}
        for row_number, _item, desired in updates:
            store.update_food(row_number, desired)
            changed_rows.append(row_number)
            expected[row_number] = desired
        if appends:
            rows = [row for _item, row in appends]
            appended_numbers = store.append_food(rows)
            for number, row in zip(appended_numbers, rows):
                changed_rows.append(number)
                expected[number] = row

        verified_rows: list[dict[str, Any]] = []
        if changed_rows:
            actual = store.read_food_rows(changed_rows)
            for number in changed_rows:
                if number not in actual or not _same_row(actual[number], expected[number], mapping):
                    return {"success": False, "error": "verification_failed", "row_number": number}
                verified_rows.append(_row_dict(number, actual[number], mapping))

        if updates and not appends:
            status = "updated"
        elif appends:
            status = "written"
        else:
            status = "already_recorded"
        return {
            "success": True,
            "status": status,
            "date": normalized_date,
            "rows": changed_rows or duplicates,
            "skipped_duplicate_rows": duplicates,
            "verified_rows": verified_rows,
            "created_products": created_products,
            # The app builds its own day view, so it skips these two extra reads.
            **({"day_total": store.read_day_total(normalized_date), "targets": store.read_targets()} if summary else {}),
        }
    except (ValueError, TypeError, KeyError) as exc:
        return {"success": False, "error": "validation_error", "message": str(exc)}
