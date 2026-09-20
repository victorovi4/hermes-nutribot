"""Product cards and sets ("стандарты") stored in the «Продукты» and «Наборы» tabs."""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Callable

from nutricore.ids import new_id
from nutricore.schema import BASES, PRODUCT_HEADERS
from nutricore.textutil import date_sort_key, matches_query, norm
from nutricore.units import Product, Unit, format_units, nutrition_for, parse_units, resolve_unit

PRECISIONS = ("точно", "оценка")
_BASE_UNIT = {"100 г": "г", "100 мл": "мл", "1 шт": ""}
_NUTRIENTS = ("kcal", "protein_g", "fat_g", "carbs_g")


@dataclass(frozen=True)
class SetItem:
    product_id: str
    product_name: str
    quantity: float
    unit: str
    order: int


def _index(headers: list[Any]) -> dict[str, int]:
    return {norm(header): position for position, header in enumerate(headers)}


def _cell(row: list[Any], index: dict[str, int], header: str) -> Any:
    position = index.get(norm(header))
    return row[position] if position is not None and position < len(row) else ""


def _number(value: Any, field: str) -> float:
    if isinstance(value, str):
        value = value.replace(" ", "").replace(",", ".")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be numeric") from None
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return number


def _split_aliases(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        parts = [str(item) for item in value]
    else:
        parts = str(value or "").split(";")
    return tuple(part.strip() for part in parts if part.strip())


def _product_from_row(headers: list[Any], row: list[Any]) -> Product:
    index = _index(headers)
    base = str(_cell(row, index, "Основа")).strip()
    return Product(
        id=str(_cell(row, index, "ID продукта")).strip(),
        name=str(_cell(row, index, "Название")).strip(),
        base=base,
        kcal=_number(_cell(row, index, "ккал") or 0, "ккал"),
        protein_g=_number(_cell(row, index, "Белки, г") or 0, "Белки, г"),
        fat_g=_number(_cell(row, index, "Жиры, г") or 0, "Жиры, г"),
        carbs_g=_number(_cell(row, index, "Углеводы, г") or 0, "Углеводы, г"),
        units=parse_units(str(_cell(row, index, "Мерки")), base),
        default_unit=str(_cell(row, index, "Мерка по умолчанию")).strip(),
        precision=str(_cell(row, index, "Точность")).strip() or "оценка",
        source=str(_cell(row, index, "Источник")).strip(),
        aliases=_split_aliases(_cell(row, index, "Другие названия")),
        hidden=bool(str(_cell(row, index, "Скрыт")).strip()),
    )


def _row_from_product(product: Product, *, created: str, updated: str, origin: str) -> list[Any]:
    values = {
        "ID продукта": product.id, "Название": product.name, "Основа": product.base,
        "ккал": product.kcal, "Белки, г": product.protein_g, "Жиры, г": product.fat_g, "Углеводы, г": product.carbs_g,
        "Мерки": format_units(product.units, product.base), "Мерка по умолчанию": product.default_unit,
        "Точность": product.precision, "Источник": product.source, "Другие названия": "; ".join(product.aliases),
        "Создан": created, "Обновлён": updated, "Откуда": origin, "Скрыт": "да" if product.hidden else "",
    }
    return [values[header] for header in PRODUCT_HEADERS]


def _load(store) -> list[tuple[int, Product, list[Any]]]:
    headers, rows = store.read_products()
    loaded = []
    for number, row in enumerate(rows, start=2):
        if not any(str(value or "").strip() for value in row):
            continue
        loaded.append((number, _product_from_row(headers, row), row))
    return loaded


def load_products(store) -> list[Product]:
    return [product for _number_, product, _row in _load(store)]


def get_product(store, product_id: str) -> Product | None:
    wanted = str(product_id or "").strip()
    return next((p for p in load_products(store) if p.id == wanted), None)


def find_products(store, query: str, limit: int = 10, stats: dict[str, dict] | None = None) -> list[Product]:
    stats = stats or {}
    found = [
        product for product in load_products(store)
        if not product.hidden and any(matches_query(name, query) for name in (product.name,) + product.aliases)
    ]
    found.sort(key=lambda p: (-stats.get(p.id, {}).get("times", 0), norm(p.name)))
    return found[:limit]


def _names(name: str, aliases: tuple[str, ...]) -> set[str]:
    return {norm(value) for value in (name,) + tuple(aliases) if norm(value)}


def _validated(data: dict[str, Any]) -> Product:
    name = " ".join(str(data.get("name") or "").split())
    if not name:
        raise ValueError("product name is required")
    base = str(data.get("base") or "").strip()
    if base not in BASES:
        raise ValueError(f"base must be one of: {', '.join(BASES)}")
    units = parse_units(str(data.get("units") or ""), base)
    if base == "1 шт" and not units:
        units = (Unit("штука", 1.0),)
    precision = str(data.get("precision") or "оценка").strip()
    if precision not in PRECISIONS:
        raise ValueError(f"precision must be one of: {', '.join(PRECISIONS)}")
    product = Product(
        id="", name=name, base=base,
        **{field: _number(data.get(field), field) for field in _NUTRIENTS},
        units=units, precision=precision, source=str(data.get("source") or "").strip(),
        aliases=_split_aliases(data.get("aliases")),
    )
    default_unit = str(data.get("default_unit") or "").strip() or (units[0].name if units else _BASE_UNIT[base])
    resolve_unit(product, default_unit)
    return replace(product, default_unit=default_unit)


def _merge_units(old: tuple[Unit, ...], new: tuple[Unit, ...]) -> tuple[Unit, ...]:
    merged = {unit.name.casefold(): unit for unit in old}
    merged.update({unit.name.casefold(): unit for unit in new})
    return tuple(merged.values())


def upsert_product(store, data: dict[str, Any], *, origin: str, today: str,
                   id_factory: Callable[[str], str] = new_id, dry_run: bool = False) -> tuple[Product, bool]:
    """Create a card, or reuse the one that already carries this name. Returns (product, created)."""
    incoming = _validated(data)
    wanted = _names(incoming.name, incoming.aliases)
    for number, existing, row in _load(store):
        if not wanted & _names(existing.name, existing.aliases):
            continue
        updated = existing
        if existing.precision == "оценка" and incoming.precision == "точно":
            same_base = existing.base == incoming.base
            updated = replace(
                existing, base=incoming.base, precision="точно",
                kcal=incoming.kcal, protein_g=incoming.protein_g, fat_g=incoming.fat_g, carbs_g=incoming.carbs_g,
                source=incoming.source or existing.source,
                # Amounts of old units are relative to the old base, so a new base replaces them.
                units=_merge_units(existing.units, incoming.units) if same_base else incoming.units,
                default_unit=existing.default_unit if same_base else incoming.default_unit,
            )
        elif existing.base == incoming.base and incoming.units:
            updated = replace(existing, units=_merge_units(existing.units, incoming.units))
        known = _names(updated.name, updated.aliases)
        extra = tuple(name for name in (incoming.name,) + incoming.aliases if norm(name) not in known)
        if extra:
            updated = replace(updated, aliases=updated.aliases + extra)
        if updated != existing and not dry_run:
            index = _index(PRODUCT_HEADERS)
            store.update_product(number, _row_from_product(
                updated, created=str(_cell(row, index, "Создан")), updated=today,
                origin=str(_cell(row, index, "Откуда")) or origin))
        return updated, False

    created = replace(incoming, id="" if dry_run else id_factory("p"))
    if not dry_run:
        store.append_products([_row_from_product(created, created=today, updated="", origin=origin)])
    return created, True


def create_many(store, datas: list[dict[str, Any]], *, origin: str, today: str,
                id_factory: Callable[[str], str] = new_id, dry_run: bool = False) -> list[tuple[Product, bool]]:
    """Bulk create with one read and one append. Cards that already exist are reused untouched."""
    known = [(_names(product.name, product.aliases), product) for _n, product, _row in _load(store)]
    results: list[tuple[Product, bool]] = []
    new_rows: list[list[Any]] = []
    for data in datas:
        incoming = _validated(data)
        wanted = _names(incoming.name, incoming.aliases)
        match = next((product for names, product in known if wanted & names), None)
        if match is not None:
            results.append((match, False))
            continue
        created = replace(incoming, id="" if dry_run else id_factory("p"))
        known.append((wanted, created))
        new_rows.append(_row_from_product(created, created=today, updated="", origin=origin))
        results.append((created, True))
    if new_rows and not dry_run:
        store.append_products(new_rows)
    return results


def usage_stats(food_headers: list[Any], food_rows: list[list[Any]]) -> dict[str, dict[str, Any]]:
    index = _index(food_headers)
    if norm("ID продукта") not in index:
        return {}
    stats: dict[str, dict[str, Any]] = {}
    for row in food_rows:
        product_id = str(_cell(row, index, "ID продукта")).strip()
        date = str(_cell(row, index, "Дата")).strip()
        if not product_id or not date:
            continue
        entry = stats.setdefault(product_id, {"times": 0, "last_quantity": 0.0, "last_unit": "", "last_date": ""})
        entry["times"] += 1
        if not entry["last_date"] or date_sort_key(date) >= date_sort_key(entry["last_date"]):
            quantity = _cell(row, index, "Количество")
            entry.update(last_date=date, last_unit=str(_cell(row, index, "Мерка")).strip(),
                         last_quantity=_number(quantity, "Количество") if quantity not in ("", None) else 0.0)
    return stats


def product_public(product: Product, stats: dict[str, Any] | None = None) -> dict[str, Any]:
    stats = stats or {}
    return {
        "product_id": product.id, "name": product.name, "base": product.base,
        "kcal": product.kcal, "protein_g": product.protein_g, "fat_g": product.fat_g, "carbs_g": product.carbs_g,
        "units": format_units(product.units, product.base), "default_unit": product.default_unit,
        "precision": product.precision, "times_logged": stats.get("times", 0),
        "last_quantity": stats.get("last_quantity", 0.0), "last_unit": stats.get("last_unit", ""),
    }


# --- sets ---------------------------------------------------------------
def load_sets(store) -> dict[str, list[SetItem]]:
    headers, rows = store.read_sets()
    index = _index(headers)
    sets: dict[str, list[SetItem]] = {}
    for row in rows:
        name = str(_cell(row, index, "Набор")).strip()
        if not name or str(_cell(row, index, "Скрыт")).strip():
            continue
        sets.setdefault(name, []).append(SetItem(
            product_id=str(_cell(row, index, "ID продукта")).strip(),
            product_name=str(_cell(row, index, "Продукт")).strip(),
            quantity=_number(_cell(row, index, "Количество") or 0, "Количество"),
            unit=str(_cell(row, index, "Мерка")).strip(),
            order=int(_number(_cell(row, index, "Порядок") or 0, "Порядок")),
        ))
    for items in sets.values():
        items.sort(key=lambda item: item.order)
    return sets


def find_sets(store, query: str = "") -> list[dict[str, Any]]:
    products = {product.id: product for product in load_products(store) if not product.hidden}
    result = []
    for name, items in load_sets(store).items():
        if norm(query) and not matches_query(name, query):
            continue
        total = dict.fromkeys(_NUTRIENTS, 0.0)
        public_items = []
        for item in items:
            entry = {"product_id": item.product_id, "product": item.product_name,
                     "quantity": item.quantity, "unit": item.unit}
            product = products.get(item.product_id)
            if product is None:
                entry["missing"] = True
            else:
                for field, value in nutrition_for(product, item.quantity, item.unit).items():
                    total[field] += value
            public_items.append(entry)
        result.append({"set_name": name, "items": public_items, **{f: round(v, 1) for f, v in total.items()}})
    return result


def save_set(store, name: str, items: list[dict[str, Any]], *, dry_run: bool = False) -> list[SetItem]:
    name = " ".join(str(name or "").split())
    if not name:
        raise ValueError("set_name is required")
    if not items:
        raise ValueError("a set needs at least one product")
    products = {product.id: product for product in load_products(store)}
    saved: list[SetItem] = []
    for order, item in enumerate(items, start=1):
        product = products.get(str(item.get("product_id") or "").strip())
        if product is None:
            raise ValueError(f"unknown product_id: {item.get('product_id')!r}")
        quantity = _number(item.get("quantity"), "quantity")
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        unit = resolve_unit(product, str(item.get("unit") or "")).name
        saved.append(SetItem(product.id, product.name, quantity, unit, order))
    if not dry_run:
        store.replace_set(name, [[name, i.product_id, i.product_name, i.quantity, i.unit, i.order, ""] for i in saved])
    return saved
