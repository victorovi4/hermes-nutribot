"""One-off migrations of the nutrition spreadsheet. Never touches diary columns A–M.

CLI (run with a Python that has the Google client libraries, e.g. the Hermes venv):
    HERMES_HOME=~/.hermes/profiles/nutrition PYTHONPATH=core python -m nutricore.migrate \\
        {ensure-schema|assign-ids|export-rows|copy|apply-catalog} [--spreadsheet ID] [--dry-run] [--out PATH]\n        [--title TEXT] [--draft PATH] [--report PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Callable

from nutricore import catalog
from nutricore.ids import new_id
from nutricore.ledger import _header_map, _row_dict
from nutricore.store import food_numbers
from nutricore.units import fmt_number, nutrition_for

TOLERANCE = 0.1


def totals_equal(before: dict[str, dict[str, float]], after: dict[str, dict[str, float]]) -> bool:
    if set(before) != set(after):
        return False
    return all(abs(before[date][field] - after[date][field]) <= TOLERANCE for date in before for field in before[date])


def _filled(row: list[Any]) -> bool:
    return any(str(value or "").strip() for value in row[:13])


def assign_record_ids(store, *, dry_run: bool, id_factory: Callable[[str], str] = new_id) -> dict[str, Any]:
    """Give every diary row a stable «ID записи» and «Откуда = миграция». Idempotent."""
    headers, rows = store.read_food()
    mapping = _header_map(headers)
    if "record_id" not in mapping or "origin" not in mapping:
        raise RuntimeError("в таблице нет колонки «ID записи»: сначала ensure-schema")
    before = store.read_all_day_totals()
    updates = []
    total = 0
    for number, row in zip(food_numbers(store, rows), rows):
        if not _filled(row):
            continue
        total += 1
        current = row[mapping["record_id"]] if mapping["record_id"] < len(row) else ""
        if str(current or "").strip():
            continue
        updates.append((number, {headers[mapping["record_id"]]: id_factory("r"), headers[mapping["origin"]]: "миграция"}))
    if updates and not dry_run:
        store.write_food_cells_many(updates)
    after = before if dry_run or not updates else store.read_all_day_totals()
    return {"rows_total": total, "rows_assigned": len(updates), "totals_equal": totals_equal(before, after)}


def export_rows(store) -> list[dict[str, Any]]:
    headers, rows = store.read_food()
    mapping = _header_map(headers)
    return [_row_dict(number, row, mapping) for number, row in zip(food_numbers(store, rows), rows) if _filled(row)]


def _kcal_matches(calculated: float, recorded: float) -> bool:
    return abs(calculated - recorded) <= max(3.0, 0.05 * abs(recorded))


def apply_catalog(store, draft: list[dict[str, Any]], *, dry_run: bool, today: str,
                  id_factory: Callable[[str], str] = new_id) -> dict[str, Any]:
    """Create product cards from a reviewed draft and link history rows to them.

    Only «ID продукта», «Количество» and «Мерка» are written, and only when the card reproduces the
    calories already recorded in the row (within 5 % or 3 kcal). Texts and numbers of history never change.
    """
    headers, rows = store.read_food()
    mapping = _header_map(headers)
    if any(field not in mapping for field in ("product_id", "quantity", "unit")):
        raise RuntimeError("в таблице нет колонок продукта: сначала ensure-schema")
    before = store.read_all_day_totals()
    by_number = dict(zip(food_numbers(store, rows), rows))
    cards = [{key: value for key, value in entry.items() if key != "rows"} for entry in draft]
    results = catalog.create_many(store, cards, origin="миграция", today=today, id_factory=id_factory, dry_run=dry_run)

    links, skipped, already = [], [], 0
    taken: set[int] = set()
    summary: dict[str, dict[str, Any]] = {}
    for entry, (product, created) in zip(draft, results):
        info = summary.setdefault(catalog.norm(product.name), {"product": product, "created": created, "linked": 0})
        info["created"] = info["created"] or created
        for link in entry.get("rows") or []:
            number = int(link["row_number"])
            row = by_number.get(number, [])
            if not _filled(row):
                skipped.append({"row_number": number, "product": product.name, "reason": "row_not_found"})
                continue
            if str(row[mapping["product_id"]] if mapping["product_id"] < len(row) else "").strip() or number in taken:
                already += 1
                continue
            try:
                calculated = nutrition_for(product, link["quantity"], str(link["unit"]))["kcal"]
            except (ValueError, TypeError, KeyError) as exc:
                skipped.append({"row_number": number, "product": product.name, "reason": f"bad_unit: {exc}"})
                continue
            recorded = float(row[mapping["kcal"]] or 0)
            if not _kcal_matches(calculated, recorded):
                skipped.append({"row_number": number, "product": product.name,
                                "reason": f"kcal_mismatch: по карточке {fmt_number(calculated)}, в строке {fmt_number(recorded)}"})
                continue
            taken.add(number)
            info["linked"] += 1
            links.append((number, {headers[mapping["product_id"]]: product.id,
                                   headers[mapping["quantity"]]: float(link["quantity"]),
                                   headers[mapping["unit"]]: str(link["unit"])}))
    if links and not dry_run:
        store.write_food_cells_many(links)
    after = before if dry_run or not links else store.read_all_day_totals()
    return {
        "products_created": sum(1 for _product, created in results if created),
        "products_existing": sum(1 for _product, created in results if not created),
        "rows_linked": len(links), "rows_already_linked": already, "rows_skipped": skipped,
        "totals_equal": totals_equal(before, after),
        "products": [{"name": v["product"].name, "base": v["product"].base, "kcal": v["product"].kcal,
                      "protein_g": v["product"].protein_g, "fat_g": v["product"].fat_g, "carbs_g": v["product"].carbs_g,
                      "units": catalog.format_units(v["product"].units, v["product"].base),
                      "precision": v["product"].precision, "aliases": list(v["product"].aliases),
                      "created": v["created"], "rows_linked": v["linked"]} for v in summary.values()],
    }


def render_catalog_report(result: dict[str, Any]) -> str:
    lines = [
        "# Каталог продуктов из истории дневника", "",
        f"Новых карточек: {result['products_created']} · уже были: {result['products_existing']} · "
        f"строк привязано: {result['rows_linked']} · уже привязаны: {result['rows_already_linked']} · "
        f"пропущено: {len(result['rows_skipped'])} · итоги по дням совпали: {'да' if result['totals_equal'] else 'НЕТ'}", "",
        "| Продукт | Основа | ккал · Б · Ж · У | Мерки | Точность | Строк | Другие названия |", "|---|---|---|---|---|---|---|",
    ]
    for p in sorted(result["products"], key=lambda item: catalog.norm(item["name"])):
        numbers = " · ".join(fmt_number(p[field]) for field in ("kcal", "protein_g", "fat_g", "carbs_g"))
        lines.append(f"| {p['name']} | {p['base']} | {numbers} | {p['units'] or '—'} | {p['precision']} | "
                     f"{p['rows_linked']} | {'; '.join(p['aliases']) or '—'} |")
    if result["rows_skipped"]:
        lines += ["", "## Строки, которые не привязаны", "", "| Строка | Продукт | Причина |", "|---|---|---|"]
        lines += [f"| {s['row_number']} | {s['product']} | {s['reason']} |" for s in result["rows_skipped"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    from nutricore.google_store import GoogleSheetStore

    parser = argparse.ArgumentParser(prog="nutricore.migrate")
    parser.add_argument("command", choices=["ensure-schema", "assign-ids", "export-rows", "copy", "apply-catalog"])
    parser.add_argument("--spreadsheet", default=os.environ.get("NUTRI_SPREADSHEET_ID", ""))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--draft", default="")
    parser.add_argument("--report", default="")
    args = parser.parse_args(argv)
    if not args.spreadsheet:
        parser.error("нужен номер таблицы: --spreadsheet или переменная NUTRI_SPREADSHEET_ID")
    store = GoogleSheetStore(spreadsheet_id=args.spreadsheet)

    if args.command == "copy":
        result: Any = {"copied_to": store.copy_spreadsheet(args.title or "Дневник питания — копия")}
    elif args.command == "ensure-schema":
        result = {"dry_run": True} if args.dry_run else store.ensure_schema()
    elif args.command == "assign-ids":
        result = assign_record_ids(store, dry_run=args.dry_run)
    elif args.command == "apply-catalog":
        from datetime import datetime
        from zoneinfo import ZoneInfo

        with open(args.draft, encoding="utf-8") as handle:
            draft = json.load(handle)
        today = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y")
        result = apply_catalog(store, draft, dry_run=args.dry_run, today=today)
        if args.report:
            with open(args.report, "w", encoding="utf-8") as handle:
                handle.write(render_catalog_report(result))
    else:
        result = export_rows(store)

    text = json.dumps(result, ensure_ascii=False, indent=1)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text)
        print(f"written: {args.out}")
    else:
        print(text)
    if args.command in ("assign-ids", "apply-catalog") and not result["totals_equal"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
