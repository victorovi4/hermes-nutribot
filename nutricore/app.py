"""What the Telegram app needs from the diary: the day screen, the catalog, and edits by record id."""
from __future__ import annotations

import re
from datetime import date as _date_type, datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from nutricore import catalog, ledger
from nutricore.ids import new_id
from nutricore.schema import FOOD_TAB
from nutricore.store import food_numbers
from nutricore.textutil import norm, normalize_date
from nutricore.units import Product, nutrition_for

MEALS = ["До завтрака", "Завтрак", "Перекус после завтрака", "Обед", "Перекус после обеда", "Ужин"]
NUTRIENTS = ("kcal", "protein_g", "fat_g", "carbs_g")
ORIGIN = "приложение"
_BASE_UNIT = {"100 г": ("г", "граммы"), "100 мл": ("мл", "миллилитры")}
_NUMBER = r"\d[\d   ]*(?:[.,]\d+)?"


class ConflictError(Exception):
    """The row changed between reading and writing; the caller should reload and retry."""


def _to_float(text: str) -> float:
    return float(re.sub(r"[   ]", "", text).replace(",", "."))


def parse_range(text: str) -> list[float] | None:
    """«1 800–1 900 ккал/день (…)» → [1800.0, 1900.0]; a single number → [n, n]."""
    text = str(text or "")
    match = re.search(rf"({_NUMBER})\s*[–—-]\s*({_NUMBER})", text)
    if match:
        return [_to_float(match.group(1)), _to_float(match.group(2))]
    single = re.search(_NUMBER, text)
    return [_to_float(single.group(0))] * 2 if single else None


def _targets(store) -> dict[str, Any]:
    raw = store.read_targets()
    return {
        "kcal": parse_range(raw.get("energy", "")),
        "protein_g": parse_range(raw.get("protein", "")),
        "fat_g": parse_range(raw.get("fat", "")),
        "fat_min_only": "миним" in norm(raw.get("fat", "")),
    }


def unit_list(product: Product) -> list[dict[str, Any]]:
    """Units the app can offer for a product: its own measures first, then grams or millilitres."""
    base = _BASE_UNIT.get(product.base)
    units = [{"name": u.name, "amount": u.amount,
              "label": f"{u.name} · {_trim(u.amount)} {base[0]}" if base else u.name} for u in product.units]
    if base:
        units.append({"name": base[0], "amount": 1.0, "label": base[1]})
    return units


def _trim(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def _cell(row: list[Any], mapping: dict[str, int], field: str) -> Any:
    index = mapping.get(field)
    return row[index] if index is not None and index < len(row) else ""


def _num(value: Any) -> float:
    try:
        return float(str(value).replace(",", ".")) if value not in ("", None) else 0.0
    except ValueError:
        return 0.0


def _entry(row: list[Any], mapping: dict[str, int], products: dict[str, Product]) -> dict[str, Any]:
    product_id = str(_cell(row, mapping, "product_id")).strip()
    product = products.get(product_id)
    record_id = str(_cell(row, mapping, "record_id")).strip()
    return {
        "record_id": record_id,
        "editable": bool(record_id),
        "date": str(_cell(row, mapping, "date")).strip(),
        "meal": str(_cell(row, mapping, "meal")).strip(),
        "product": str(_cell(row, mapping, "product")).strip(),
        "portion": str(_cell(row, mapping, "portion")).strip(),
        **{field: round(_num(_cell(row, mapping, field)), 1) for field in NUTRIENTS},
        "estimated": "оцен" in norm(_cell(row, mapping, "portion_status")),
        "product_id": product_id if product else "",
        "quantity": _num(_cell(row, mapping, "quantity")) if product else 0.0,
        "unit": str(_cell(row, mapping, "unit")).strip() if product else "",
        "unit_list": unit_list(product) if product else [],
        "set_name": str(_cell(row, mapping, "set_name")).strip(),
        "group_id": str(_cell(row, mapping, "group_id")).strip(),
    }


def _totals(entries: list[dict[str, Any]]) -> dict[str, float]:
    return {field: round(sum(entry[field] for entry in entries), 1) for field in NUTRIENTS}


def _read(store):
    headers, rows = store.read_food()
    return headers, rows, ledger._header_map(headers)


def _day_entries(rows, mapping, products, date: str) -> list[dict[str, Any]]:
    return [_entry(row, mapping, products) for row in rows
            if str(_cell(row, mapping, "date")).strip() == date and str(_cell(row, mapping, "product")).strip()]


def day_view(store, date: str) -> dict[str, Any]:
    date = normalize_date(date)
    _headers, rows, mapping = _read(store)
    products = {p.id: p for p in catalog.load_products(store)}
    entries = _day_entries(rows, mapping, products, date)
    return {"date": date, "meals": list(MEALS), "entries": entries, "totals": _totals(entries), "targets": _targets(store)}


def catalog_view(store) -> dict[str, Any]:
    headers, rows, _mapping = _read(store)
    stats = catalog.usage_stats(headers, rows)
    products = []
    for product in catalog.load_products(store):
        if product.hidden:
            continue
        usage = stats.get(product.id, {})
        units = unit_list(product)
        first = units[0]
        per_unit = nutrition_for(product, 1 if product.units else 100, first["name"])["kcal"]
        products.append({
            **catalog.product_public(product, usage),
            "last_date": usage.get("last_date", ""),
            "unit_list": units,
            "unit_kcal": per_unit,
            "unit_label": first["name"] if product.units else f"100 {first['name']}",
        })
    products.sort(key=lambda p: (-p["times_logged"], norm(p["name"])))
    return {"products": products, "sets": catalog.find_sets(store, ""), "meals": list(MEALS)}


MAX_STATS_DAYS = 62


def _to_date(text: str) -> _date_type:
    day, month, year = normalize_date(text).split(".")
    return _date_type(int(year), int(month), int(day))


def stats_view(store, date_from: str, date_to: str, *, today: str = "") -> dict[str, Any]:
    """Day-by-day totals for a period and how each finished day did against the targets."""
    first, last = _to_date(date_from), _to_date(date_to)
    if first > last:
        raise ValueError("начало периода позже конца")
    if (last - first).days + 1 > MAX_STATS_DAYS:
        raise ValueError(f"период длиннее {MAX_STATS_DAYS} дней")
    current = _to_date(today) if today else _now_msk().date()
    _headers, rows, mapping = _read(store)
    targets = _targets(store)

    sums: dict[str, dict[str, float]] = {}
    for row in rows:
        day = str(_cell(row, mapping, "date")).strip()
        if not day or not str(_cell(row, mapping, "product")).strip():
            continue
        entry = sums.setdefault(day, {**dict.fromkeys(NUTRIENTS, 0.0), "entries": 0})
        for field in NUTRIENTS:
            entry[field] += _num(_cell(row, mapping, field))
        entry["entries"] += 1

    days = []
    counted: list[dict[str, Any]] = []
    for offset in range((last - first).days + 1):
        moment = first + timedelta(days=offset)
        label = moment.strftime("%d.%m.%Y")
        found = sums.get(label)
        item: dict[str, Any] = {"date": label, "logged": bool(found), "today": moment == current, "future": moment > current,
                                "entries": int(found["entries"]) if found else 0,
                                **{field: round(found[field], 1) if found else 0.0 for field in NUTRIENTS},
                                "kcal_state": None, "kcal_delta": 0.0, "protein_ok": None, "fat_ok": None}
        if found:
            corridor = targets["kcal"]
            if corridor:
                low, high = corridor
                state = "below" if item["kcal"] < low else "above" if item["kcal"] > high else "in"
                item["kcal_state"] = state
                item["kcal_delta"] = round(item["kcal"] - low, 1) if state == "below" else round(item["kcal"] - high, 1) if state == "above" else 0.0
            if targets["protein_g"]:
                item["protein_ok"] = item["protein_g"] >= targets["protein_g"][0]
            if targets["fat_g"]:
                low, high = targets["fat_g"]
                item["fat_ok"] = item["fat_g"] >= low if targets["fat_min_only"] else low <= item["fat_g"] <= high
            if not item["today"]:                       # an unfinished day would drag every average down
                counted.append(item)
        days.append(item)

    summary = {
        "days_logged": sum(1 for d in days if d["logged"]),
        "days_counted": len(counted),
        **{f"kcal_{state}": sum(1 for d in counted if d["kcal_state"] == state) for state in ("in", "below", "above")},
        "protein_ok": sum(1 for d in counted if d["protein_ok"]),
        "fat_ok": sum(1 for d in counted if d["fat_ok"]),
        "avg": {field: round(sum(d[field] for d in counted) / len(counted), 1) for field in NUTRIENTS} if counted else None,
    }
    return {"from": first.strftime("%d.%m.%Y"), "to": last.strftime("%d.%m.%Y"), "targets": targets, "days": days, "summary": summary}


def _now_msk() -> datetime:
    return datetime.now(ZoneInfo("Europe/Moscow"))


def _raise_for(result: dict[str, Any]) -> None:
    if result.get("success"):
        return
    if result.get("error") == "conflict":
        raise ConflictError("запись изменилась, обнови экран")
    if result.get("error") == "validation_error":
        raise ValueError(result.get("message") or "validation_error")
    raise RuntimeError(result.get("message") or result.get("error") or "diary write failed")


def add_entries(store, *, date: str, meal: str, items: list[dict[str, Any]], set_name: str = "",
                remember_set: bool = False, id_factory: Callable[[str], str] = new_id, today: str = "") -> dict[str, Any]:
    """Append product entries. Each item may carry a client-made record_id: a repeated request adds nothing."""
    date = normalize_date(date)
    if not items:
        raise ValueError("items are required")
    for item in items:
        if item.get("record_id") and not ledger.RECORD_ID.fullmatch(str(item["record_id"])):
            raise ValueError("record_id must look like r + 8 hex characters")
        if not (item.get("product_id") or item.get("new_product")):
            raise ValueError("the app adds products only: product_id or new_product is required")
    _headers, rows, mapping = _read(store)
    known = {str(_cell(row, mapping, "record_id")).strip() for row in rows}
    now = _now_msk()
    fresh = [
        {"meal": meal, "quantity": item.get("quantity"), "unit": item.get("unit"), "set_name": set_name,
         "consumed_time": now.strftime("%H:%M"), "record_id": item.get("record_id") or id_factory("r"),
         **({"product_id": item["product_id"]} if item.get("product_id") else {"new_product": item["new_product"]})}
        for item in items if not (item.get("record_id") and item["record_id"] in known)
    ]
    wanted = [item.get("record_id") for item in items if item.get("record_id")] + \
             [item["record_id"] for item in fresh if item["record_id"] not in {i.get("record_id") for i in items}]
    if fresh:
        _raise_for(ledger.execute(store, action="log_food", date=date, items=fresh, origin=ORIGIN, summary=False,
                                  id_factory=id_factory, today=today or now.strftime("%d.%m.%Y")))
    if remember_set and set_name:
        view_products = {p.name: p.id for p in catalog.load_products(store)}
        catalog.save_set(store, set_name, [
            {"product_id": item.get("product_id") or view_products.get(" ".join(str(item["new_product"]["name"]).split())),
             "quantity": item.get("quantity"), "unit": item.get("unit")} for item in items])
    day = day_view(store, date)
    by_id = {entry["record_id"]: entry for entry in day["entries"]}
    return {"entries": [by_id[i] for i in wanted if i in by_id], "totals": day["totals"], "day": day}


def _locate(store, record_id: str):
    record_id = str(record_id or "").strip()
    headers, rows, mapping = _read(store)
    if "record_id" not in mapping:
        raise RuntimeError("в таблице нет колонки «ID записи»")
    for number, row in zip(food_numbers(store, rows), rows):
        if record_id and str(_cell(row, mapping, "record_id")).strip() == record_id:
            return number, row, mapping
    raise LookupError(f"запись {record_id} не найдена")


def update_entry(store, record_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    number, row, mapping = _locate(store, record_id)
    date = str(_cell(row, mapping, "date")).strip()
    meal = str(changes.get("meal") or _cell(row, mapping, "meal")).strip()
    product_id = str(_cell(row, mapping, "product_id")).strip()
    if product_id:
        item = {"meal": meal, "product_id": product_id,
                "quantity": changes.get("quantity", _cell(row, mapping, "quantity")),
                "unit": changes.get("unit") or _cell(row, mapping, "unit")}
    else:
        if "quantity" in changes or "unit" in changes:
            raise ValueError("у свободной записи нет мерок: меняются текст порции и числа")
        item = {"meal": meal, "product": str(_cell(row, mapping, "product")),
                "portion": str(changes.get("portion") or _cell(row, mapping, "portion")),
                **{field: changes.get(field, _cell(row, mapping, field)) for field in NUTRIENTS}}
    item.update(row_number=number, record_id=record_id)
    _raise_for(ledger.execute(store, action="correct_food", date=date, items=[item], origin=ORIGIN, summary=False))
    day = day_view(store, date)
    entry = next((e for e in day["entries"] if e["record_id"] == record_id), None)
    return {"entry": entry, "totals": day["totals"], "day": day}


def delete_entry(store, record_id: str) -> dict[str, Any]:
    number, row, mapping = _locate(store, record_id)
    date = str(_cell(row, mapping, "date")).strip()
    # Re-read that single row right before deleting: row numbers shift when someone else deletes above it.
    current = store.read_food_rows([number]).get(number, [])
    if str(_cell(current, mapping, "record_id")).strip() != str(record_id).strip():
        raise ConflictError("запись изменилась, обнови экран")
    store.delete_rows(FOOD_TAB, [number])
    day = day_view(store, date)
    return {"deleted": record_id, "totals": day["totals"], "day": day}


def save_meal_as_set(store, name: str, record_ids: list[str]) -> dict[str, Any]:
    items = []
    for record_id in record_ids:
        _number, row, mapping = _locate(store, record_id)
        product_id = str(_cell(row, mapping, "product_id")).strip()
        if not product_id:
            raise ValueError(f"«{_cell(row, mapping, 'product')}» — свободная запись, в набор идут только продукты из каталога")
        items.append({"product_id": product_id, "quantity": _cell(row, mapping, "quantity"), "unit": _cell(row, mapping, "unit")})
    catalog.save_set(store, name, items)
    clean = " ".join(str(name).split())
    return next(s for s in catalog.find_sets(store, "") if norm(s["set_name"]) == norm(clean))
