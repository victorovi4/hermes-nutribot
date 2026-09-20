from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

BASES = ("100 г", "100 мл", "1 шт")
_BASE_UNIT = {"100 г": "г", "100 мл": "мл", "1 шт": ""}
UNIT_FORMS = {
    "штука": ("штука", "штуки", "штук"), "банка": ("банка", "банки", "банок"),
    "упаковка": ("упаковка", "упаковки", "упаковок"), "пачка": ("пачка", "пачки", "пачек"),
    "бутылка": ("бутылка", "бутылки", "бутылок"), "чашка": ("чашка", "чашки", "чашек"),
    "стакан": ("стакан", "стакана", "стаканов"), "порция": ("порция", "порции", "порций"),
    "ломтик": ("ломтик", "ломтика", "ломтиков"), "кусок": ("кусок", "куска", "кусков"),
    "ягода": ("ягода", "ягоды", "ягод"), "яйцо": ("яйцо", "яйца", "яиц"),
    "трубочка": ("трубочка", "трубочки", "трубочек"), "лепёшка": ("лепёшка", "лепёшки", "лепёшек"),
    "ч. л.": ("ч. л.", "ч. л.", "ч. л."), "ст. л.": ("ст. л.", "ст. л.", "ст. л."),
    "долька": ("долька", "дольки", "долек"), "набор": ("набор", "набора", "наборов"),
    "бутерброд": ("бутерброд", "бутерброда", "бутербродов"),
}


@dataclass(frozen=True)
class Unit:
    name: str
    amount: float


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    base: str
    kcal: float
    protein_g: float
    fat_g: float
    carbs_g: float
    units: tuple[Unit, ...] = ()
    default_unit: str = ""
    precision: str = "оценка"
    source: str = ""
    aliases: tuple[str, ...] = ()
    hidden: bool = False


def fmt_number(value: float) -> str:
    text = f"{float(value):.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def _to_float(text: str) -> float:
    return float(str(text).strip().replace(" ", "").replace(",", "."))


def parse_units(text: str, base: str) -> tuple[Unit, ...]:
    if base not in BASES:
        raise ValueError(f"unknown base: {base!r}")
    units: list[Unit] = []
    for chunk in str(text or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, sep, raw = chunk.partition("=")
        name = name.strip()
        if not name:
            raise ValueError(f"unit without a name: {chunk!r}")
        if not sep:
            if base != "1 шт":
                raise ValueError(f"unit {name!r} needs an amount for base {base!r}")
            units.append(Unit(name, 1.0))
            continue
        try:
            amount = _to_float(raw)
        except ValueError:
            raise ValueError(f"bad amount for unit {name!r}: {raw!r}") from None
        if amount <= 0:
            raise ValueError(f"amount for unit {name!r} must be positive")
        units.append(Unit(name, amount))
    return tuple(units)


def format_units(units: Iterable[Unit], base: str) -> str:
    if base == "1 шт":
        return "; ".join(u.name for u in units)
    return "; ".join(f"{u.name}={fmt_number(u.amount)}" for u in units)


def resolve_unit(product: Product, unit_name: str) -> Unit:
    wanted = str(unit_name or "").strip().casefold()
    base_unit = _BASE_UNIT[product.base]
    if base_unit and wanted == base_unit:
        return Unit(base_unit, 1.0)
    for unit in product.units:
        if unit.name.casefold() == wanted:
            return unit
    raise ValueError(f"unknown unit {unit_name!r} for product {product.name!r}")


def nutrition_for(product: Product, quantity: float, unit_name: str) -> dict[str, float]:
    quantity = float(quantity)
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    unit = resolve_unit(product, unit_name)
    amount = quantity * unit.amount
    factor = amount if product.base == "1 шт" else amount / 100.0
    return {
        "kcal": round(product.kcal * factor, 1),
        "protein_g": round(product.protein_g * factor, 1),
        "fat_g": round(product.fat_g * factor, 1),
        "carbs_g": round(product.carbs_g * factor, 1),
    }


def _plural(quantity: float, forms: tuple[str, str, str]) -> str:
    if float(quantity) != int(quantity):
        return forms[1]
    n = int(quantity)
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def portion_text(product: Product, quantity: float, unit_name: str) -> str:
    unit = resolve_unit(product, unit_name)
    base_unit = _BASE_UNIT[product.base]
    if base_unit and unit.name == base_unit:
        return f"{fmt_number(quantity)} {base_unit}"
    forms = UNIT_FORMS.get(unit.name.casefold())
    head = f"{fmt_number(quantity)} {_plural(quantity, forms)}" if forms else f"{fmt_number(quantity)} × {unit.name}"
    if not base_unit:
        return head
    return f"{head} ({fmt_number(float(quantity) * unit.amount)} {base_unit})"
