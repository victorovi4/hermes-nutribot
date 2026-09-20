import pytest
from nutricore.units import Product, Unit, parse_units, format_units, nutrition_for, portion_text, fmt_number

CHEDDAR = Product("p1", "Красный чеддер", "100 г", 354, 24, 28.8, 0)
SHARMEL = Product("p2", "Зефир «Шармэль»", "100 г", 368, 2.4, 10, 65.6, units=(Unit("штука", 25), Unit("упаковка", 250)))
BALTIKA = Product("p3", "Балтика 0", "100 мл", 30, 0, 0, 5, units=(Unit("банка", 450),))
ZEFIR = Product("p4", "Зефир", "1 шт", 30, 0.6, 0, 6.7, units=(Unit("штука", 1),))
EGG = Product("p5", "Куриное яйцо", "1 шт", 70, 6.4, 5, 0.4, units=(Unit("яйцо", 1),))
BAR = Product("p6", "Батончик", "100 г", 400, 10, 20, 50, units=(Unit("батончик", 40),))

def test_parse_and_format_units():
    assert parse_units("штука=25; упаковка=250", "100 г") == (Unit("штука", 25.0), Unit("упаковка", 250.0))
    assert parse_units("штука", "1 шт") == (Unit("штука", 1.0),)
    assert parse_units("", "100 г") == ()
    assert parse_units("ч. л.=2,5", "100 г") == (Unit("ч. л.", 2.5),)
    assert format_units((Unit("штука", 25), Unit("ч. л.", 2.5)), "100 г") == "штука=25; ч. л.=2,5"
    assert format_units((Unit("штука", 1),), "1 шт") == "штука"

def test_parse_units_rejects_garbage():
    for bad in ("штука=abc", "=25", "штука=0", "штука=-3"):
        with pytest.raises(ValueError):
            parse_units(bad, "100 г")

def test_nutrition_for():
    assert nutrition_for(CHEDDAR, 25, "г") == {"kcal": 88.5, "protein_g": 6.0, "fat_g": 7.2, "carbs_g": 0.0}
    assert nutrition_for(ZEFIR, 2, "штука") == {"kcal": 60.0, "protein_g": 1.2, "fat_g": 0.0, "carbs_g": 13.4}
    assert nutrition_for(BALTIKA, 1, "банка") == {"kcal": 135.0, "protein_g": 0.0, "fat_g": 0.0, "carbs_g": 22.5}
    assert nutrition_for(SHARMEL, 2, "штука")["kcal"] == 184.0
    assert nutrition_for(BALTIKA, 300, "мл")["kcal"] == 90.0

def test_unit_errors():
    with pytest.raises(ValueError, match="ложка"):
        nutrition_for(ZEFIR, 1, "ложка")
    with pytest.raises(ValueError):
        nutrition_for(ZEFIR, 10, "г")          # у штучного продукта нет граммов
    with pytest.raises(ValueError):
        nutrition_for(CHEDDAR, 0, "г")         # количество > 0

def test_portion_text():
    assert portion_text(CHEDDAR, 25, "г") == "25 г"
    assert portion_text(CHEDDAR, 1.25, "г") == "1,25 г"
    assert portion_text(SHARMEL, 2, "штука") == "2 штуки (50 г)"
    assert portion_text(SHARMEL, 5, "штука") == "5 штук (125 г)"
    assert portion_text(BALTIKA, 1, "банка") == "1 банка (450 мл)"
    assert portion_text(ZEFIR, 1, "штука") == "1 штука"
    assert portion_text(EGG, 0.5, "яйцо") == "0,5 яйца"
    assert portion_text(BAR, 2, "батончик") == "2 × батончик (80 г)"

def test_fmt_number():
    assert [fmt_number(v) for v in (2, 2.0, 0.5, 1.25, 1234.5)] == ["2", "2", "0,5", "1,25", "1234,5"]


def test_more_unit_forms():
    roll = Product("p7", "Набор роллов", "100 г", 260, 9.1, 7.7, 39.5, units=(Unit("набор", 530), Unit("долька", 12)))
    assert portion_text(roll, 0.5, "набор") == "0,5 набора (265 г)"
    assert portion_text(roll, 3, "долька") == "3 дольки (36 г)"
