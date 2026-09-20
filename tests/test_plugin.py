from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("nutrition_plugin", PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)])
mod = importlib.util.module_from_spec(spec)
sys.modules["nutrition_plugin"] = mod
spec.loader.exec_module(mod)


class Store:
    def read_food(self):
        return ["Дата","Приём пищи","Продукт / блюдо","Порция","ккал","Белки, г","Жиры, г","Углеводы, г","Источник / уточнение","Самочувствие / симптомы"], [["12.09.2026","Завтрак","Творог","100 г",159,16.7,9,2,"",""]]
    def append_food(self, rows): return [2]
    def update_food(self, row_number, row): raise AssertionError
    def read_food_rows(self, rows): return {2:["12.09.2026","Завтрак","Творог","100 г",159,16.7,9,2,"",""]}
    def read_day_total(self, date): return {"kcal":159,"protein_g":16.7,"fat_g":9,"carbs_g":2}
    def read_targets(self): return {}
    def find_standards(self, query): return []


def test_handler_returns_compact_json_and_supports_dry_run(monkeypatch):
    monkeypatch.setattr(mod, "_store_factory", lambda: Store())
    out=json.loads(mod.handle({
        "action":"log_food", "date":"2026-09-12", "dry_run":True,
        "items":[{"meal":"Завтрак","product":"Творог","portion":"100 г","kcal":159,"protein_g":16.7,"fat_g":9,"carbs_g":2}],
    }))
    assert out["success"] is True
    assert out["status"] == "dry_run"


def test_schema_exposes_only_personal_ledger_and_read_actions():
    props=mod.SCHEMA["parameters"]["properties"]
    assert "spreadsheet_id" not in props
    assert set(props["action"]["enum"]) == {"log_food","correct_food","day_status","find_standard","get_row","search_food","get_day_entries","find_product","find_set","save_set","set_targets"}
    assert {"row_number", "query", "meal", "date_from", "date_to", "limit"} <= set(props)
    assert mod.SCHEMA["parameters"]["required"] == ["action"]


def test_handler_passes_read_only_arguments(monkeypatch):
    monkeypatch.setattr(mod, "_store_factory", lambda: Store())
    out=json.loads(mod.handle({"action":"get_row", "row_number":2}))
    assert out["success"] is True
    assert out["row"]["row_number"] == 2


def test_item_schema_accepts_product_items_and_requires_only_the_meal():
    item_schema = mod.SCHEMA["parameters"]["properties"]["items"]["items"]
    assert item_schema["required"] == ["meal"]
    assert {"product_id", "new_product", "quantity", "unit", "set_name"} <= set(item_schema["properties"])
    card = item_schema["properties"]["new_product"]
    assert card["properties"]["base"]["enum"] == ["100 г", "100 мл", "1 шт"]
    assert card["required"] == ["name", "base", "kcal", "protein_g", "fat_g", "carbs_g"]
    assert "set_name" in mod.SCHEMA["parameters"]["properties"]


def test_handler_passes_set_name_and_bot_origin(monkeypatch):
    seen = {}
    def fake_execute(store, **kw):
        seen.update(kw)
        return {"success": True}
    monkeypatch.setattr(mod, "_store_factory", lambda: Store())
    monkeypatch.setattr(mod, "execute", fake_execute)
    out = mod.handle({"action": "save_set", "set_name": "Каша", "items": []})
    assert out == '{"success":true}' and seen["set_name"] == "Каша" and seen["origin"] == "бот"


def test_tool_availability_follows_the_configured_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("NUTRI_STORAGE", raising=False)
    monkeypatch.delenv("NUTRI_SPREADSHEET_ID", raising=False)
    assert mod._available() is True                                    # a local diary needs nothing
    monkeypatch.setenv("NUTRI_STORAGE", "sheets")
    assert mod._available() is False                                   # …but Google needs an id and a token
    monkeypatch.setenv("NUTRI_SPREADSHEET_ID", "abc")
    assert mod._available() is False
    (tmp_path / "google_token.json").write_text("{}", encoding="utf-8")
    assert mod._available() is True
    monkeypatch.setenv("HERMES_SAFE_MODE", "1")
    assert mod._available() is False


def test_store_factory_opens_the_configured_diary(monkeypatch, tmp_path):
    from nutricore.sqlite_store import SqliteStore
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("NUTRI_STORAGE", "sqlite")
    monkeypatch.delenv("NUTRI_DB_PATH", raising=False)
    store = mod._store_factory()
    assert isinstance(store, SqliteStore) and store.path == tmp_path / "nutribot.db"


def test_plugin_registers_its_command_and_tool():
    calls = {"cli": [], "tools": []}

    class Ctx:
        manifest = type("M", (), {"name": "nutribot"})()

        def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=""):
            calls["cli"].append((name, setup_fn, handler_fn))

        def register_tool(self, **kw):
            calls["tools"].append(kw["name"])

    mod.register(Ctx())
    assert calls["tools"] == ["nutrition_log"]
    (name, setup_fn, handler_fn) = calls["cli"][0]
    assert name == "nutribot" and callable(setup_fn) and callable(handler_fn)

    import argparse
    parser = argparse.ArgumentParser()
    setup_fn(parser)
    assert parser.parse_args([]).command == "setup"
    assert parser.parse_args(["doctor"]).command == "doctor"
    assert parser.parse_args(["export", "/tmp/x.xlsx"]).path == "/tmp/x.xlsx"


def test_doctor_command_reports_and_returns_a_code(tmp_path, monkeypatch, capsys):
    import argparse
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("NUTRI_STORAGE", raising=False)
    code = mod._cli(argparse.Namespace(command="doctor", path=""))
    out = capsys.readouterr().out
    assert code == 1 and "✗" in out and "NUTRI_STORAGE" in out


def test_manifest_parses_and_matches_the_registered_tool():
    """A colon inside an unquoted YAML description once broke the whole plugin — never again."""
    from pathlib import Path

    import pytest

    yaml = pytest.importorskip("yaml", reason="Hermes читает манифест через PyYAML; в тестовой среде он не обязателен")
    manifest = yaml.safe_load((Path(__file__).resolve().parents[1] / "plugin.yaml").read_text(encoding="utf-8"))
    assert manifest["name"] == "nutribot" and manifest["provides_tools"] == [mod.SCHEMA["name"]]
    assert isinstance(manifest["description"], str) and manifest["version"]
