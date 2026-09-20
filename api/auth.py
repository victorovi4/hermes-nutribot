"""Telegram Mini App sign-in: verify the initData signature, its age and the user id."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

MAX_AGE_SECONDS = 24 * 60 * 60


class AuthError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def verify_init_data(init_data: str, bot_token: str, allowed_ids: set[int], *, now: float | None = None) -> int:
    fields = dict(parse_qsl(init_data or "", keep_blank_values=True))
    received = fields.pop("hash", "")
    if not received or not fields or not bot_token:
        raise AuthError(401, "нет подписи Telegram")
    check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise AuthError(401, "подпись Telegram не сошлась")
    try:
        age = (now if now is not None else time.time()) - int(fields.get("auth_date", "0"))
        user_id = int(json.loads(fields.get("user", "{}"))["id"])
    except (ValueError, KeyError, TypeError):
        raise AuthError(401, "в данных Telegram нет пользователя") from None
    if age > MAX_AGE_SECONDS or age < -300:
        raise AuthError(401, "данные входа устарели, открой приложение заново")
    if user_id not in allowed_ids:
        raise AuthError(403, "этот дневник закрыт для других пользователей")
    return user_id
