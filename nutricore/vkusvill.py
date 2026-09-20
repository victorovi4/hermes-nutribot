"""Search the VkusVill catalog (their public MCP endpoint) and turn results into product cards."""
from __future__ import annotations

import html
import json
import re
import urllib.request
from typing import Any, Callable

MCP_URL = "https://mcp001.vkusvill.ru/mcp"
_NUTRITION_PROPERTY = "пищевая и энергетическая ценность"
_NUMBER = r"(\d+(?:[.,]\d+)?)"
_NUTRITION = re.compile(
    rf"белки\s*{_NUMBER}\s*г,\s*жиры\s*{_NUMBER}\s*г,\s*углеводы\s*{_NUMBER}\s*г[^;]*;\s*{_NUMBER}\s*ккал", re.IGNORECASE)
_PACKAGE = re.compile(rf",?\s*{_NUMBER}\s*(кг|г|мл|л)\s*$", re.IGNORECASE)
_TO_BASE = {"г": 1, "кг": 1000, "мл": 1, "л": 1000}


class VkusvillError(Exception):
    """The VkusVill catalog did not answer or answered with an error."""


def _float(text: str) -> float:
    return float(text.replace(",", "."))


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        # The endpoint answers 403 to the default Python user agent.
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                 "User-Agent": "nutribot-app/0.1 (personal food diary)"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _card(item: dict[str, Any]) -> dict[str, Any] | None:
    name = " ".join(html.unescape(str(item.get("name") or "")).replace(" ", " ").split())
    prop = next((p for p in item.get("properties") or [] if _NUTRITION_PROPERTY in str(p.get("name", "")).casefold()), None)
    if not name or prop is None:
        return None
    matches = _NUTRITION.findall(str(prop.get("value") or ""))
    if not matches:
        return None
    protein, fat, carbs, kcal = (_float(v) for v in matches[0])
    liquid = "100 мл" in str(prop.get("name"))
    package = _PACKAGE.search(name)
    amount = _float(package.group(1)) * _TO_BASE[package.group(2).lower()] if package else None
    if package and package.group(2).lower() in ("мл", "л"):
        liquid = True
    clean = name[:package.start()].rstrip(" ,") if package else name
    url = str(item.get("url") or "")
    source = f"ВкусВилл: {url}" + ("; значения зависят от поставщика, взят первый" if len(matches) > 1 else "")
    return {
        "name": name, "url": url, "package": amount,
        "new_product": {
            "name": clean if "вкусвилл" in clean.casefold() else f"{clean}, ВкусВилл",
            "base": "100 мл" if liquid else "100 г",
            "kcal": kcal, "protein_g": protein, "fat_g": fat, "carbs_g": carbs,
            "units": f"упаковка={amount:g}".replace(".", ",") if amount else "",
            "default_unit": "мл" if liquid else "г", "precision": "точно", "source": source,
        },
    }


def cards_from_search(response: dict[str, Any]) -> list[dict[str, Any]]:
    result = response.get("result") or {}
    if response.get("error") or result.get("isError"):
        raise VkusvillError("каталог ВкусВилла вернул ошибку")
    try:
        payload = json.loads(result["content"][0]["text"])
        items = payload["data"]["items"]
    except (KeyError, IndexError, TypeError, ValueError):
        raise VkusvillError("каталог ВкусВилла ответил в неожиданном формате") from None
    return [card for card in map(_card, items) if card]


def search(query: str, *, post: Callable[[str, dict], dict] = _post_json, limit: int = 10) -> list[dict[str, Any]]:
    query = " ".join(str(query or "").split())
    if not query:
        raise ValueError("пустой запрос")
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "vkusvill_products_search", "arguments": {"q": query, "mode": "short"}}}
    try:
        response = post(MCP_URL, payload)
    except VkusvillError:
        raise
    except Exception as exc:
        raise VkusvillError(f"каталог ВкусВилла не ответил: {type(exc).__name__}") from exc
    return cards_from_search(response)[:limit]
