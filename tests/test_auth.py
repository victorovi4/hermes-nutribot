from __future__ import annotations

import hashlib
import hmac
import json
from urllib.parse import urlencode

import pytest

from api import auth

TOKEN = "123456:TEST-TOKEN"
NOW = 1_790_000_000


def signed(user_id=111222333, auth_date=NOW - 60, token=TOKEN, **extra):
    fields = {"auth_date": str(auth_date), "query_id": "AAE", "user": json.dumps({"id": user_id, "first_name": "Тест"}, ensure_ascii=False), **extra}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_valid_init_data_returns_the_user_id():
    assert auth.verify_init_data(signed(), TOKEN, {111222333}, now=NOW) == 111222333
    assert auth.verify_init_data(signed(signature="abc"), TOKEN, {111222333}, now=NOW) == 111222333


@pytest.mark.parametrize("init_data,status", [
    ("", 401), ("garbage", 401), (signed(token="999:OTHER"), 401), (signed() + "0", 401),
    (signed(auth_date=NOW - 90_000), 401), (signed(user_id=1), 403),
])
def test_bad_init_data_is_rejected(init_data, status):
    with pytest.raises(auth.AuthError) as error:
        auth.verify_init_data(init_data, TOKEN, {111222333}, now=NOW)
    assert error.value.status == status
