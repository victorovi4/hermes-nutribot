"""Profile-scoped atomic nutrition ledger tool."""
from __future__ import annotations

import fcntl
import json
import os
import sys
from pathlib import Path

from tools.registry import tool_error

_PLUGIN_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from nutricore.ledger import execute
from nutricore.store import STORAGE_SHEETS, open_store, storage_kind


def _home() -> Path:
    return Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()


def _available() -> bool:
    """The tool shows up only when the diary is actually reachable: a local file needs nothing, Google needs a token."""
    if os.environ.get("HERMES_SAFE_MODE"):
        return False
    try:
        if storage_kind(os.environ) == STORAGE_SHEETS:
            return bool(os.environ.get("NUTRI_SPREADSHEET_ID")) and (_home() / "google_token.json").exists()
    except ValueError:
        return False
    return True


def _store_factory():
    return open_store(os.environ)


def handle(args, **kwargs):
    try:
        lock_path = _home() / "nutrition-log.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            result = execute(
                _store_factory(),
                action=args.get("action", ""),
                date=args.get("date", ""),
                items=args.get("items"),
                dry_run=bool(args.get("dry_run", False)),
                row_number=args.get("row_number"),
                query=args.get("query", ""),
                meal=args.get("meal", ""),
                date_from=args.get("date_from", ""),
                date_to=args.get("date_to", ""),
                limit=args.get("limit", 10),
                set_name=args.get("set_name", ""),
                targets=args.get("targets"),
                origin="бот",
            )
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        return tool_error(f"nutrition_log failed: {type(exc).__name__}: {exc}")


NEW_PRODUCT_SCHEMA = {
    "type": "object",
    "description": "Карточка нового продукта с известными КБЖУ на основу. Существующая карточка с тем же названием не дублируется",
    "properties": {
        "name": {"type": "string"},
        "base": {"type": "string", "enum": ["100 г", "100 мл", "1 шт"], "description": "На что даны КБЖУ"},
        "kcal": {"type": "number", "minimum": 0},
        "protein_g": {"type": "number", "minimum": 0},
        "fat_g": {"type": "number", "minimum": 0},
        "carbs_g": {"type": "number", "minimum": 0},
        "units": {"type": "string", "description": "Свои мерки в граммах или мл основы: «штука=25; упаковка=250». Для основы «1 шт» — только название штуки: «штука»"},
        "default_unit": {"type": "string"},
        "precision": {"type": "string", "enum": ["точно", "оценка"], "description": "точно — этикетка или карточка магазина"},
        "source": {"type": "string"},
    },
    "required": ["name", "base", "kcal", "protein_g", "fat_g", "carbs_g"],
    "additionalProperties": False,
}

ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "meal": {"type": "string", "description": "Приём пищи"},
        "product": {"type": "string", "description": "Продукт или блюдо"},
        "portion": {"type": "string", "description": "Порция с массой/объёмом и допущениями"},
        "kcal": {"type": "number", "minimum": 0},
        "protein_g": {"type": "number", "minimum": 0},
        "fat_g": {"type": "number", "minimum": 0},
        "carbs_g": {"type": "number", "minimum": 0},
        "source": {"type": "string", "description": "Источник данных или явное допущение"},
        "symptoms": {"type": "string", "description": "Необязательное нейтральное примечание"},
        "portion_status": {"type": "string", "description": "Статус порции: точная, стандартная или оценочная; стандартная только после подтверждения пользователя"},
        "estimate_version": {"type": "string", "description": "Версия оценки, формула пересчёта или версия рецепта"},
        "consumed_time": {"type": "string", "pattern": "^([01]\\d|2[0-3]):[0-5]\\d$", "description": "Ориентировочное время приёма в MSK, HH:MM"},
        "row_number": {"type": "integer", "minimum": 2, "description": "Точная строка только для исправления неоднозначного совпадения"},
        "product_id": {"type": "string", "description": "ID продукта из find_product. С ним нужны только meal, quantity и unit: название, порцию и КБЖУ посчитает инструмент"},
        "new_product": NEW_PRODUCT_SCHEMA,
        "quantity": {"type": "number", "exclusiveMinimum": 0, "description": "Сколько мерок съедено: 1, 0.5, 25"},
        "unit": {"type": "string", "description": "Мерка: г, мл или мерка из карточки продукта (штука, банка, ч. л.)"},
        "set_name": {"type": "string", "description": "Название набора («стандарта»), если позиция записана из набора; добавка на этот раз получает то же название"},
    },
    "required": ["meal"],
    "additionalProperties": False,
}

SCHEMA = {
    "name": "nutrition_log",
    "description": "Атомарно читает или изменяет только личный дневник питания Мити. get_row, search_food и get_day_entries строго read-only; log_food и correct_food — единственные действия, меняющие таблицу. Проверяет живые заголовки, предотвращает точные дубли, записывает числовые КБЖУ и возвращает формульный итог дня. Если продукт есть в каталоге (find_product) или известны его КБЖУ на 100 г, 100 мл или за штуку — передавай в позиции product_id либо new_product вместе с quantity и unit: название, порцию и числа посчитает инструмент. find_set и save_set работают с наборами («стандартами»).",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["log_food", "correct_food", "day_status", "find_standard", "get_row", "search_food", "get_day_entries", "find_product", "find_set", "save_set", "set_targets"]},
            "date": {"type": "string", "description": "Дата DD.MM.YYYY или YYYY-MM-DD; обязательна для day_status и get_day_entries, опциональный фильтр для search_food"},
            "items": {"type": "array", "items": ITEM_SCHEMA, "minItems": 1, "maxItems": 12},
            "set_name": {"type": "string", "description": "Название набора для save_set"},
            "targets": {"type": "object", "description": "Цели для set_targets: energy («1 800–1 900 ккал/день»), protein, fat, goal",
                        "properties": {key: {"type": "string"} for key in ("energy", "protein", "fat", "goal")},
                        "additionalProperties": False},
            "dry_run": {"type": "boolean", "default": False, "description": "Рассчитать план log_food/correct_food без записи"},
            "row_number": {"type": "integer", "minimum": 2, "description": "Номер строки для get_row; при отсутствии возвращается not_found без записи"},
            "query": {"type": "string", "description": "Непустая часть названия продукта для search_food; регистр и ё не учитываются"},
            "meal": {"type": "string", "description": "Необязательный точный фильтр приёма пищи для search_food"},
            "date_from": {"type": "string", "description": "Начало диапазона DD.MM.YYYY или YYYY-MM-DD для search_food"},
            "date_to": {"type": "string", "description": "Конец диапазона DD.MM.YYYY или YYYY-MM-DD для search_food"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10, "description": "Максимум результатов search_food"},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}


def _cli_setup(parser) -> None:
    """Shape of `hermes nutribot …` — Hermes hands us the subparser."""
    parser.add_argument("command", nargs="?", default="setup", choices=["setup", "doctor", "export"],
                        help="setup — настроить дневник, doctor — проверить, export — выгрузить в .xlsx")
    parser.add_argument("path", nargs="?", default="", help="куда выгружать (для export)")


def _cli(args) -> int:
    """`hermes nutribot <setup|doctor|export>` — everything a new user needs after installing the plugin."""
    from nutricore import setup_wizard

    command = str(getattr(args, "command", "") or "setup").strip().lower()
    home = _home()
    if command == "setup":
        try:
            setup_wizard.run(home)
        except setup_wizard.SetupError as exc:
            print(f"\n{exc}")
            return 1
        return 0
    if command == "doctor":
        report = setup_wizard.doctor(home)
        for check in report["checks"]:
            print(("  ✓ " if check["ok"] else "  ✗ ") + check["what"] + (f" — {check['detail']}" if check["detail"] else ""))
        print("\nВсё на месте." if report["ok"] else "\nЧто-то не готово: посмотри строки с ✗ или запусти «hermes nutribot setup».")
        return 0 if report["ok"] else 1
    if command == "export":
        target = str(getattr(args, "path", "") or "")
        path = setup_wizard.export(home, Path(target).expanduser() if target else None)
        print(f"Дневник выгружен: {path}")
        return 0
    print("Команды: setup — настроить дневник, doctor — проверить, export [файл] — выгрузить в .xlsx")
    return 2


def register(ctx):
    ctx.register_cli_command(
        "nutribot",
        "Нутрибот: setup — настроить дневник, doctor — проверить, export — выгрузить в .xlsx",
        _cli_setup, handler_fn=_cli,
        description="Дневник питания: мастер настройки, проверка и выгрузка")
    ctx.register_tool(
        name="nutrition_log",
        toolset="nutrition",
        schema=SCHEMA,
        handler=handle,
        check_fn=_available,
    )
