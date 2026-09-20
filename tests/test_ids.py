import re

from nutricore.ids import new_id


def test_new_id_has_prefix_and_eight_hex_chars():
    assert re.fullmatch(r"r[0-9a-f]{8}", new_id("r"))
    assert re.fullmatch(r"p[0-9a-f]{8}", new_id("p"))


def test_new_id_is_unique_enough():
    assert len({new_id("r") for _ in range(1000)}) == 1000
