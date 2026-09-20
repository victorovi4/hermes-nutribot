from __future__ import annotations

from nutricore import google_store as mod


class Req:
    def __init__(self, value): self.value = value
    def execute(self): return self.value


class Values:
    def __init__(self):
        self.calls = []
        self.food = [["Дата", "Приём пищи", "Продукт / блюдо", "Порция", "ккал", "Белки, г", "Жиры, г", "Углеводы, г", "Источник / уточнение", "Самочувствие / симптомы", "Статус порции", "Версия оценки / рецепт", "Время приёма (MSK)"]]
    def get(self, **kw):
        self.calls.append(("get", kw))
        r = kw["range"]
        if "Питание" in r:
            rows=[list(row) for row in self.food]
            if kw.get("valueRenderOption") == "FORMATTED_VALUE":
                rows=[rows[0]]+[[str(v).replace(".", ",") if isinstance(v, float) else v for v in row] for row in rows[1:]]
            return Req({"values": rows})
        if "Итоги по дням" in r: return Req({"values": [["Дата", "ккал", "Белки", "Жиры", "Углеводы"], ["12.09.2026", "100,5", "10", "4", "8"]]})
        if "Справочник" in r: return Req({"values": [["Параметр", "Значение"], ["Стартовая цель энергии", "1800–1900 ккал"], ["Белок", "120–130 г"]]})
        if "Стандартные порции" in r: return Req({"values": [["Категория", "Продукт"], ["Добавка", "Псиллиум"]]})
        raise AssertionError(r)
    def append(self, **kw):
        self.calls.append(("append", kw))
        self.food.extend(kw["body"]["values"])
        return Req({"updates": {"updatedRange": f"'Питание'!A{len(self.food) - len(kw['body']['values']) + 1}:T{len(self.food)}"}})
    def update(self, **kw):
        self.calls.append(("update", kw))
        row = int(kw["range"].split("A")[1].split(":")[0])
        while len(self.food) < row: self.food.append([])
        self.food[row - 1] = kw["body"]["values"][0]
        return Req({"updatedCells": len(kw["body"]["values"][0])})
    def batchGet(self, **kw):
        self.calls.append(("batchGet", kw))
        ranges=[]
        for r in kw["ranges"]:
            row=int(r.split("A")[1].split(":")[0])
            values=list(self.food[row-1])
            if kw.get("valueRenderOption") == "FORMATTED_VALUE":
                values = [str(v).replace(".", ",") if isinstance(v, float) else v for v in values]
            ranges.append({"range":r,"values":[values]})
        return Req({"valueRanges": ranges})


class Spreadsheets:
    def __init__(self, values): self._values=values
    def values(self): return self._values


class Service:
    def __init__(self): self.values_api=Values(); self._s=Spreadsheets(self.values_api)
    def spreadsheets(self): return self._s


def test_store_reads_food_and_appends_numeric_rows():
    service=Service(); store=mod.GoogleSheetStore(service=service, spreadsheet_id="fixed")
    headers, rows=store.read_food()
    assert headers[0] == "Дата" and rows == []
    nums=store.append_food([["12.09.2026", "Завтрак", "Творог", "100 г", 159.0, 16.7, 9.0, 2.0, "", "", "точная", "этикетка", "09:00"]])
    assert nums == [2]
    call=next(c for c in service.values_api.calls if c[0]=="append")
    assert call[1]["valueInputOption"] == "USER_ENTERED"
    assert isinstance(call[1]["body"]["values"][0][4], float)
    assert call[1]["range"] == "'Питание'!A:T"
    read_call=next(c for c in service.values_api.calls if c[0]=="get" and "Питание" in c[1]["range"])
    assert read_call[1]["range"] == "'Питание'!A:T"


def test_store_reads_day_total_and_targets():
    store=mod.GoogleSheetStore(service=Service(), spreadsheet_id="fixed")
    assert store.read_day_total("12.09.2026") == {"kcal":100.5,"protein_g":10.0,"fat_g":4.0,"carbs_g":8.0}
    targets=store.read_targets()
    assert targets["energy"] == "1800–1900 ккал"
    assert targets["protein"] == "120–130 г"


def test_readback_keeps_formatted_text_and_unformatted_numeric_cells():
    service=Service(); store=mod.GoogleSheetStore(service=service, spreadsheet_id="fixed")
    service.values_api.food.append(["12.09.2026", "Завтрак", "Творог", "100 г", 159.5, 16.7, 9.0, 2.0, "", "", "точная", "v1", "09:00"])
    row=store.read_food_rows([2])[2]
    assert row[0] == "12.09.2026"
    assert row[4:8] == [159.5, 16.7, 9.0, 2.0]
    calls=[c for c in service.values_api.calls if c[0]=="batchGet"]
    assert [c[1]["valueRenderOption"] for c in calls] == ["FORMATTED_VALUE", "UNFORMATTED_VALUE"]


def test_read_food_uses_unformatted_macros_for_duplicate_detection():
    service=Service(); service.values_api.food.append(["12.09.2026", "Завтрак", "Творог", "100 г", 159.5, 16.7, 9.0, 2.0, "", "", "точная", "v1", "09:00"])
    store=mod.GoogleSheetStore(service=service, spreadsheet_id="fixed")
    _, rows=store.read_food()
    assert rows[0][4:8] == [159.5, 16.7, 9.0, 2.0]
    calls=[c for c in service.values_api.calls if c[0]=="get" and "Питание" in c[1]["range"]]
    assert [c[1]["valueRenderOption"] for c in calls] == ["FORMATTED_VALUE", "UNFORMATTED_VALUE"]


def test_find_standards_filters_case_insensitively():
    store=mod.GoogleSheetStore(service=Service(), spreadsheet_id="fixed")
    found=store.find_standards("псил")
    assert len(found)==1 and found[0][1] == "Псиллиум"


EXTRA = ["ID записи", "ID продукта", "Количество", "Мерка", "Набор", "Группа", "Откуда"]
WIDE_ROW = ["12.09.2026", "Перекус", "Зефир", "2 штуки", 60.0, 1.2, 0.0, 13.4, "", "", "оценочная", "", "16:00",
            "r0000001", "p0000001", 2.5, "штука", "", "", "бот"]


def wide_service():
    service = Service()
    service.values_api.food[0] = service.values_api.food[0] + EXTRA
    service.values_api.food.append(list(WIDE_ROW))
    return service


def test_wide_sheet_rows_are_twenty_cells_with_numeric_quantity():
    store = mod.GoogleSheetStore(service=wide_service(), spreadsheet_id="fixed")
    headers, rows = store.read_food()
    assert len(headers) == 20 and len(rows[0]) == 20
    assert rows[0][15] == 2.5 and rows[0][4:8] == [60.0, 1.2, 0.0, 13.4]
    assert rows[0][13] == "r0000001" and rows[0][19] == "бот"
    assert store.read_food_rows([2])[2][15] == 2.5


def test_narrow_sheet_rows_are_padded_to_header_width():
    service = Service()
    service.values_api.food.append(["12.09.2026", "Завтрак", "Творог", "100 г", 159.5, 16.7, 9.0, 2.0])
    store = mod.GoogleSheetStore(service=service, spreadsheet_id="fixed")
    _, rows = store.read_food()
    assert len(rows[0]) == 13


def test_update_and_append_use_the_wide_range():
    service = wide_service(); store = mod.GoogleSheetStore(service=service, spreadsheet_id="fixed")
    store.update_food(2, list(WIDE_ROW))
    update = next(c for c in service.values_api.calls if c[0] == "update")
    assert update[1]["range"] == "'Питание'!A2:T2"
    assert store.append_food([list(WIDE_ROW), list(WIDE_ROW)]) == [3, 4]
