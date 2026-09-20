from __future__ import annotations

import json

import pytest

from nutricore import vkusvill

PROP = "Пищевая и энергетическая ценность в 100 г"
ITEMS = [
    {"id": 188, "name": "Творог 9%, 400&nbsp;г", "unit": "шт", "url": "https://vkusvill.ru/goods/tvorog-9-188/",
     "properties": [{"name": PROP, "value": 'белки 16 г, жиры 9 г, углеводы 3 г; 157 ккал Поставщики:ООО "Н-ГРУПП"'}]},
    {"id": 2, "name": "Кефир 3,2% в бутылке, 900 г", "unit": "шт", "url": "https://vkusvill.ru/goods/kefir-2/",
     "properties": [{"name": PROP, "value": 'ЗАО "СЕРНУРСКИЙ СЫРЗАВОД": белки 3 г, жиры 3.2 г, углеводы 4 г; 56.8 ккал<br>ОАО "Х": белки 3 г, жиры 3.2 г, углеводы 4 г; 58 ккал'}]},
    {"id": 3, "name": "Морс клюквенный, 1 л", "unit": "шт", "url": "https://vkusvill.ru/goods/mors-3/",
     "properties": [{"name": "Пищевая и энергетическая ценность в 100 мл", "value": "белки 0 г, жиры 0 г, углеводы 11 г; 44 ккал"}]},
    {"id": 4, "name": "Пакет бумажный", "unit": "шт", "url": "https://vkusvill.ru/goods/paket-4/", "properties": []},
]


def mcp_response(items):
    text = json.dumps({"ok": True, "data": {"meta": {"total": len(items)}, "items": items}}, ensure_ascii=False)
    return {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": text}], "isError": False}}


def test_cards_from_search_parses_nutrition_and_package():
    cards = vkusvill.cards_from_search(mcp_response(ITEMS))
    assert [c["name"] for c in cards] == ["Творог 9%, 400 г", "Кефир 3,2% в бутылке, 900 г", "Морс клюквенный, 1 л"]
    curd = cards[0]["new_product"]
    assert curd == {"name": "Творог 9%, ВкусВилл", "base": "100 г", "kcal": 157.0, "protein_g": 16.0, "fat_g": 9.0, "carbs_g": 3.0,
                    "units": "упаковка=400", "default_unit": "г", "precision": "точно",
                    "source": "ВкусВилл: https://vkusvill.ru/goods/tvorog-9-188/"}
    kefir = cards[1]["new_product"]
    assert kefir["kcal"] == 56.8 and kefir["fat_g"] == 3.2 and "зависят от поставщика" in kefir["source"]
    mors = cards[2]["new_product"]
    assert mors["base"] == "100 мл" and mors["units"] == "упаковка=1000" and mors["default_unit"] == "мл"


def test_search_posts_a_tool_call_and_limits_results():
    seen = {}
    def post(url, payload):
        seen.update(url=url, payload=payload)
        return mcp_response(ITEMS)
    cards = vkusvill.search("творог", post=post, limit=2)
    assert len(cards) == 2 and seen["url"] == vkusvill.MCP_URL
    assert seen["payload"]["params"] == {"name": "vkusvill_products_search", "arguments": {"q": "творог", "mode": "short"}}


def test_search_errors_are_reported():
    with pytest.raises(ValueError):
        vkusvill.search("  ")
    def broken(url, payload):
        return {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "oops"}], "isError": True}}
    with pytest.raises(vkusvill.VkusvillError):
        vkusvill.search("творог", post=broken)


def test_nutrition_with_sugar_and_salt_details_is_parsed():
    item = {"name": "Трубочки с белковым кремом, 5 шт", "url": "https://vkusvill.ru/goods/t/", "properties": [{"name": PROP,
            "value": "ИП Степанова: белки 7 г, жиры 17.6 г, углеводы 54.2 г, в том числе сахара (общие) - 44.4 г, соль - 0.7 г; 403.2 ккал<br>ИП Каганович: белки 7 г, жиры 18 г, углеводы 55 г; 410 ккал"}]}
    card = vkusvill.cards_from_search(mcp_response([item]))[0]["new_product"]
    assert (card["kcal"], card["protein_g"], card["fat_g"], card["carbs_g"]) == (403.2, 7.0, 17.6, 54.2)
    assert card["units"] == "" and card["name"] == "Трубочки с белковым кремом, 5 шт, ВкусВилл"
