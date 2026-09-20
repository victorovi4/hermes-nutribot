"""Two questions and the diary is ready: where to keep it, and whether to put the app in Telegram.

Everything else the wizard does itself — creates the storage, writes the settings, adds the bot's rules to
SOUL.md (with a copy of the old file). `doctor` re-checks all of it; `export` makes a spreadsheet file to look at.
"""
from __future__ import annotations

import os
import re
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable
from xml.sax.saxutils import escape

from nutricore.schema import FOOD_HEADERS, PRODUCT_HEADERS, SET_HEADERS
from nutricore.store import STORAGE_SHEETS, STORAGE_SQLITE, open_store

START_MARK = "<!-- nutribot:start -->"
END_MARK = "<!-- nutribot:end -->"
RULES_FILE = Path(__file__).with_name("rules_ru.md")
DEFAULT_TARGETS = {"energy": "1 800–2 000 ккал/день", "protein": "от 100 г/день", "fat": "50–70 г/день"}
APPS = {"1": "none", "2": "docker", "3": "cloud"}


class SetupError(RuntimeError):
    """Something the person has to fix before the wizard can go on."""


def _read_env(home: Path) -> dict[str, str]:
    path = home / ".env"
    if not path.exists():
        return {}
    pairs = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            pairs[key.strip()] = value.strip()
    return pairs


def _write_env(home: Path, values: dict[str, str]) -> None:
    """Set or replace keys in the profile's .env, leaving every other line untouched."""
    path = home / ".env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    for key, value in values.items():
        replaced = False
        for index, line in enumerate(lines):
            if line.split("=", 1)[0].strip() == key and not line.lstrip().startswith("#"):
                lines[index] = f"{key}={value}"
                replaced = True
                break
        if not replaced:
            lines.append(f"{key}={value}")
    path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def _backup(path: Path, tag: str) -> Path | None:
    if not path.exists():
        return None
    backups = path.parent / "backups"
    backups.mkdir(exist_ok=True)
    copy = backups / f"{path.name}.bak-{tag}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(path, copy)
    return copy


def write_rules(home: Path) -> Path:
    """Put the bot's rules into SOUL.md between markers, replacing an older block instead of doubling it."""
    soul = home / "SOUL.md"
    rules = RULES_FILE.read_text(encoding="utf-8").strip()
    block = f"{START_MARK}\n{rules}\n{END_MARK}"
    _backup(soul, "nutribot")
    text = soul.read_text(encoding="utf-8") if soul.exists() else ""
    if START_MARK in text and END_MARK in text:
        text = re.sub(re.escape(START_MARK) + r".*?" + re.escape(END_MARK), block, text, flags=re.S)
    else:
        text = (text.rstrip() + "\n\n" + block + "\n") if text.strip() else block + "\n"
    soul.write_text(text, encoding="utf-8")
    return soul


def create_google_spreadsheet(home: Path, title: str, targets: dict[str, str] | None = None) -> str:
    """Create a fresh diary spreadsheet with the person's own Google account (the token Hermes already has)."""
    os.environ.setdefault("HERMES_HOME", str(home))
    from googleapiclient.discovery import build

    from nutricore.google_store import _default_credentials

    service = build("sheets", "v4", credentials=_default_credentials(), cache_discovery=False)
    book = service.spreadsheets().create(body={
        "properties": {"title": title, "locale": "ru_RU"},
        "sheets": [{"properties": {"title": name}} for name in ("Питание", "Продукты", "Наборы", "Справочник", "Итоги по дням")],
    }, fields="spreadsheetId").execute()
    spreadsheet_id = book["spreadsheetId"]
    service.spreadsheets().values().batchUpdate(spreadsheetId=spreadsheet_id, body={
        "valueInputOption": "USER_ENTERED",
        "data": [
            {"range": "'Питание'!A1", "values": [list(FOOD_HEADERS)]},
            {"range": "'Продукты'!A1", "values": [list(PRODUCT_HEADERS)]},
            {"range": "'Наборы'!A1", "values": [list(SET_HEADERS)]},
            {"range": "'Справочник'!A1", "values": [["Параметр", "Значение / методика"]]},
            {"range": "'Итоги по дням'!A1", "values": [
                ["Дата", "ккал", "Белки, г", "Жиры, г", "Углеводы, г"],
                ['=SORT(UNIQUE(FILTER(\'Питание\'!A2:A;\'Питание\'!A2:A<>"")))',
                 '=ARRAYFORMULA(IF(A2:A="";"";SUMIF(\'Питание\'!A:A;A2:A;\'Питание\'!E:E)))',
                 '=ARRAYFORMULA(IF(A2:A="";"";SUMIF(\'Питание\'!A:A;A2:A;\'Питание\'!F:F)))',
                 '=ARRAYFORMULA(IF(A2:A="";"";SUMIF(\'Питание\'!A:A;A2:A;\'Питание\'!G:G)))',
                 '=ARRAYFORMULA(IF(A2:A="";"";SUMIF(\'Питание\'!A:A;A2:A;\'Питание\'!H:H)))']]},
        ],
    }).execute()
    names = {"energy": "Стартовая цель энергии", "protein": "Белок", "fat": "Жиры", "goal": "Первичная цель"}
    if targets:
        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id, range="'Справочник'!A:B", valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [[names.get(key, key), value] for key, value in targets.items() if value]}).execute()
    return spreadsheet_id


def run(home: Path, *, ask: Callable[..., str] | None = None, printer: Callable[..., None] = print,
        create_spreadsheet: Callable[[str], str] | None = None) -> dict[str, Any]:
    """Walk the person through setup. Returns what was chosen; raises SetupError on anything they must fix."""
    home = Path(home).expanduser()
    ask = ask or (lambda question, default="": input(f"{question} " + (f"[{default}] " if default else "")).strip())
    say = printer

    say("\nНутрибот — настройка\n")
    say("Где хранить дневник?")
    say("  1) Своя база рядом с Hermes — ничего не нужно, работает быстро (по умолчанию)")
    say("  2) Google-таблица — дневник видно таблицей и можно править руками")
    storage = STORAGE_SHEETS if str(ask("Выбор 1 или 2:", "1")).strip() == "2" else STORAGE_SQLITE

    env: dict[str, str] = {"NUTRI_STORAGE": storage}
    if storage == STORAGE_SHEETS and not (home / "google_token.json").exists():
        raise SetupError("Google не подключён к этому профилю Hermes: нет google_token.json. "
                         "Подключи Google (hermes auth) или выбери хранение в своей базе.")

    say("\nЦели. Пустой ответ — оставить значение по умолчанию.")
    targets = {
        "energy": str(ask("Калории в день:", DEFAULT_TARGETS["energy"])).strip() or DEFAULT_TARGETS["energy"],
        "protein": str(ask("Белок в день:", DEFAULT_TARGETS["protein"])).strip() or DEFAULT_TARGETS["protein"],
        "fat": str(ask("Жиры в день:", DEFAULT_TARGETS["fat"])).strip() or DEFAULT_TARGETS["fat"],
    }

    say("\nПриложение в Telegram (экран дня, статистика, добавление в два касания)?")
    say("  1) Не нужно — хватит чата с ботом (по умолчанию)")
    say("  2) На своём сервере — docker compose, нужен домен")
    say("  3) В Яндекс Облаке — нужен аккаунт облака")
    app = APPS.get(str(ask("Выбор 1, 2 или 3:", "1")).strip(), "none")
    if app == "cloud" and storage == STORAGE_SQLITE:
        app = "docker" if str(ask(
            "Яндекс Облако не достанет до базы на твоей машине — там приложению нужна Google-таблица. "
            "Оставить бота без приложения (1) или поднять приложение на своём сервере (2)?", "1")).strip() == "2" else "none"

    # Nothing is written until every answer is in: a half-finished setup is worse than none.
    spreadsheet_id = ""
    if storage == STORAGE_SHEETS:
        maker = create_spreadsheet or (lambda title: create_google_spreadsheet(home, title, targets))
        say("\nСоздаю таблицу дневника…")
        spreadsheet_id = maker(f"Дневник питания — {datetime.now().strftime('%d.%m.%Y')}")
        env["NUTRI_SPREADSHEET_ID"] = spreadsheet_id
        say(f"Таблица готова: https://docs.google.com/spreadsheets/d/{spreadsheet_id}")
    _write_env(home, env)
    if storage == STORAGE_SQLITE:
        store = open_store({**os.environ, **env, "HERMES_HOME": str(home)})
        store.ensure_schema()
        store.set_targets(targets)
    write_rules(home)

    say("\nГотово.")
    say("Напиши боту в чат: «записал 2 зефира по 30 ккал» — он заведёт продукт и запишет.")
    if app == "docker":
        say("Приложение: docs/app-docker.md — один файл настроек и `docker compose up -d`.")
    elif app == "cloud":
        say("Приложение: docs/app-yandex-cloud.md — заполнить deploy/cloud.env и запустить deploy/deploy_cloud.sh.")
    say("Проверка в любой момент: hermes nutribot doctor")
    return {"storage": storage, "spreadsheet_id": spreadsheet_id, "app": app, "targets": targets}


def doctor(home: Path) -> dict[str, Any]:
    """Go through everything the bot needs and say which part is not ready."""
    home = Path(home).expanduser()
    env = {**os.environ, **_read_env(home), "HERMES_HOME": str(home)}
    checks: list[dict[str, Any]] = []

    def check(what: str, ok: bool, detail: str = "") -> bool:
        checks.append({"what": what, "ok": bool(ok), "detail": detail})
        return bool(ok)

    storage = (env.get("NUTRI_STORAGE") or "").strip()
    if not check("NUTRI_STORAGE", storage in (STORAGE_SQLITE, STORAGE_SHEETS),
                 "не задано, где хранить дневник — запусти «hermes nutribot setup»" if not storage else f"хранилище: {storage}"):
        return {"ok": False, "checks": checks}
    if storage == STORAGE_SHEETS:
        check("NUTRI_SPREADSHEET_ID", bool(env.get("NUTRI_SPREADSHEET_ID")), "не задан номер таблицы")
        check("Google подключён", (home / "google_token.json").exists(), "нет google_token.json")

    try:
        store = open_store(env)
        check("хранилище открывается", True, "")
    except Exception as exc:
        check("хранилище открывается", False, f"{type(exc).__name__}: {exc}")
        return {"ok": all(c["ok"] for c in checks), "checks": checks}

    try:
        headers, rows = store.read_food()
        check("схема дневника", list(headers) == list(FOOD_HEADERS), f"записей: {len(rows)}")
    except Exception as exc:
        check("схема дневника", False, f"{type(exc).__name__}: {exc}")

    try:
        targets = store.read_targets()
        check("цели заданы", any(targets.values()), "цели пустые — бот не покажет, сколько осталось" if not any(targets.values()) else targets.get("energy", ""))
    except Exception as exc:
        check("цели заданы", False, f"{type(exc).__name__}: {exc}")

    soul = home / "SOUL.md"
    check("правила в SOUL.md", soul.exists() and START_MARK in soul.read_text(encoding="utf-8"),
          "блок правил не найден — запусти «hermes nutribot setup»")
    return {"ok": all(c["ok"] for c in checks), "checks": checks}


# --- export -----------------------------------------------------------------
def _column_name(index: int) -> str:
    name = ""
    while index >= 0:
        name = chr(ord("A") + index % 26) + name
        index = index // 26 - 1
    return name


def _sheet_xml(rows: Iterable[list[Any]], strings: dict[str, int]) -> str:
    body = []
    for row_number, row in enumerate(rows, start=1):
        cells = []
        for column, value in enumerate(row):
            reference = f"{_column_name(column)}{row_number}"
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                cells.append(f'<c r="{reference}"><v>{value}</v></c>')
            elif str(value or ""):
                text = str(value)
                index = strings.setdefault(text, len(strings))
                cells.append(f'<c r="{reference}" t="s"><v>{index}</v></c>')
        body.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    return ('<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{"".join(body)}</sheetData></worksheet>')


def export(home: Path, path: Path | None = None) -> Path:
    """Write the diary and the catalog into an .xlsx file — to keep, to look at, or to open in Яндекс Документах."""
    home = Path(home).expanduser()
    store = open_store({**os.environ, **_read_env(home), "HERMES_HOME": str(home)})
    snapshot = store.read_snapshot()
    tabs = [("Питание", snapshot["food"]), ("Продукты", snapshot["products"]), ("Наборы", snapshot["sets"])]
    strings: dict[str, int] = {}
    sheets = [(title, _sheet_xml([list(headers)] + [list(row) for row in rows], strings)) for title, (headers, rows) in tabs]
    path = Path(path) if path else home / f"nutribot-export-{datetime.now().strftime('%Y%m%d-%H%M')}.xlsx"

    shared = "".join(f"<si><t xml:space=\"preserve\">{escape(text)}</t></si>" for text in strings)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as book:
        book.writestr("[Content_Types].xml",
                      '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                      '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                      '<Default Extension="xml" ContentType="application/xml"/>'
                      '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                      + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                                for i in range(1, len(sheets) + 1)) +
                      '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/></Types>')
        book.writestr("_rels/.rels",
                      '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                      '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        book.writestr("xl/workbook.xml",
                      '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                      + "".join(f'<sheet name="{escape(title)}" sheetId="{i}" r:id="rId{i}"/>' for i, (title, _xml) in enumerate(sheets, start=1))
                      + "</sheets></workbook>")
        book.writestr("xl/_rels/workbook.xml.rels",
                      '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                      + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                                for i in range(1, len(sheets) + 1))
                      + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/></Relationships>')
        for index, (_title, xml) in enumerate(sheets, start=1):
            book.writestr(f"xl/worksheets/sheet{index}.xml", xml)
        book.writestr("xl/sharedStrings.xml",
                      f'<?xml version="1.0" encoding="UTF-8"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                      f'count="{len(strings)}" uniqueCount="{len(strings)}">{shared}</sst>')
    os.chmod(path, 0o600)
    return path
