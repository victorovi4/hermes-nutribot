"""The wizard: two questions, then it sets everything up itself — and never damages what is already there."""
from __future__ import annotations

import json

import pytest

from nutricore import setup_wizard
from nutricore.schema import FOOD_HEADERS
from nutricore.sqlite_store import SqliteStore


class Answers:
    """Stands in for the person at the keyboard."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked = []

    def __call__(self, question, default=""):
        self.asked.append(question)
        return self.answers.pop(0) if self.answers else default


def profile(tmp_path):
    home = tmp_path / "profile"
    (home / "plugins").mkdir(parents=True)
    (home / "SOUL.md").write_text("# Мой бот\n\nТы — помощник.\n", encoding="utf-8")
    (home / ".env").write_text("TELEGRAM_BOT_TOKEN=123\n", encoding="utf-8")
    return home


def env_values(home):
    return dict(line.split("=", 1) for line in (home / ".env").read_text(encoding="utf-8").splitlines() if "=" in line)


def test_local_diary_setup_writes_settings_rules_and_goals(tmp_path):
    home = profile(tmp_path)
    ask = Answers("1", "2000-2200", "от 120", "55-70", "1")        # своя база, цели, без приложения
    result = setup_wizard.run(home, ask=ask, printer=lambda *a: None)
    assert result["storage"] == "sqlite" and result["app"] == "none"
    values = env_values(home)
    assert values["NUTRI_STORAGE"] == "sqlite" and "NUTRI_SPREADSHEET_ID" not in values
    store = SqliteStore(home / "nutribot.db")
    assert store.read_food()[0] == FOOD_HEADERS
    assert store.read_targets()["energy"] == "2000-2200" and store.read_targets()["protein"] == "от 120"
    soul = (home / "SOUL.md").read_text(encoding="utf-8")
    assert "Ты — помощник." in soul and setup_wizard.START_MARK in soul and "Продукты и мерки" in soul
    assert list((home / "backups").glob("SOUL.md.bak-nutribot-*"))


def test_second_run_replaces_the_block_instead_of_doubling_it(tmp_path):
    home = profile(tmp_path)
    setup_wizard.run(home, ask=Answers("1", "", "", "", "1"), printer=lambda *a: None)
    setup_wizard.run(home, ask=Answers("1", "", "", "", "1"), printer=lambda *a: None)
    soul = (home / "SOUL.md").read_text(encoding="utf-8")
    assert soul.count(setup_wizard.START_MARK) == 1 and soul.count("Ты — помощник.") == 1


def test_google_without_a_token_fails_cleanly(tmp_path):
    home = profile(tmp_path)
    with pytest.raises(setup_wizard.SetupError, match="Google"):
        setup_wizard.run(home, ask=Answers("2"), printer=lambda *a: None)
    assert "NUTRI_STORAGE" not in env_values(home)
    assert setup_wizard.START_MARK not in (home / "SOUL.md").read_text(encoding="utf-8")


def test_google_setup_creates_a_spreadsheet(tmp_path):
    home = profile(tmp_path)
    (home / "google_token.json").write_text("{}", encoding="utf-8")
    created = {}

    def create_spreadsheet(title):
        created["title"] = title
        return "SHEET123"

    ask = Answers("2", "1800-1900", "", "", "3")                   # Google-таблица, цели, приложение в облаке
    result = setup_wizard.run(home, ask=ask, printer=lambda *a: None, create_spreadsheet=create_spreadsheet)
    assert result["storage"] == "sheets" and result["spreadsheet_id"] == "SHEET123" and result["app"] == "cloud"
    assert "Дневник питания" in created["title"]
    values = env_values(home)
    assert values["NUTRI_STORAGE"] == "sheets" and values["NUTRI_SPREADSHEET_ID"] == "SHEET123"


def test_the_wizard_refuses_a_local_diary_with_the_cloud_app(tmp_path):
    home = profile(tmp_path)
    ask = Answers("1", "", "", "", "3", "2")                       # своя база + облако → предупредит и переспросит
    result = setup_wizard.run(home, ask=ask, printer=lambda *a: None)
    assert result["app"] == "docker"
    assert any("Яндекс Облак" in q for q in ask.asked)


def test_doctor_reports_each_check(tmp_path):
    home = profile(tmp_path)
    bad = setup_wizard.doctor(home)
    assert bad["ok"] is False and any(not c["ok"] and "NUTRI_STORAGE" in c["what"] for c in bad["checks"])
    setup_wizard.run(home, ask=Answers("1", "", "", "", "1"), printer=lambda *a: None)
    good = setup_wizard.doctor(home)
    assert good["ok"] is True and all(c["ok"] for c in good["checks"])
    assert all(not c["detail"].startswith("блок правил") for c in good["checks"])   # no scary text on a passing check
    assert [c["what"] for c in good["checks"]][:3] == ["NUTRI_STORAGE", "хранилище открывается", "схема дневника"]


def test_export_writes_a_readable_file(tmp_path):
    home = profile(tmp_path)
    setup_wizard.run(home, ask=Answers("1", "", "", "", "1"), printer=lambda *a: None)
    store = SqliteStore(home / "nutribot.db")
    store.append_food([[("18.09.2026" if h == "Дата" else "Зефир" if h == "Продукт / блюдо" else 60.0 if h == "ккал" else "") for h in FOOD_HEADERS]])
    path = setup_wizard.export(home)
    assert path.exists() and path.suffix == ".xlsx"
    import zipfile
    with zipfile.ZipFile(path) as book:
        names = book.namelist()
        assert "xl/workbook.xml" in names and "xl/worksheets/sheet1.xml" in names
        sheet = book.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert "Зефир" in book.read("xl/sharedStrings.xml").decode("utf-8") and "<row" in sheet
