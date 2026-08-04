"""普通用户密码哈希与校验。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_SCHEME = "scrypt"
_N = 2**14
_R = 8
_P = 1
_SALT_BYTES = 16
_KEY_BYTES = 32


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(f"{raw}{padding}")


def hash_user_password(password: str) -> str:
    """使用带随机盐的 scrypt 保存密码。"""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_N,
        r=_R,
        p=_P,
        dklen=_KEY_BYTES,
    )
    return f"{_SCHEME}${_N}${_R}${_P}${_encode(salt)}${_encode(digest)}"


def verify_user_password(password: str, encoded: str) -> bool:
    """校验密码；格式损坏时按失败处理。"""
    try:
        scheme, raw_n, raw_r, raw_p, raw_salt, raw_digest = encoded.split("$", 5)
        if scheme != _SCHEME:
            return False
        expected = _decode(raw_digest)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_decode(raw_salt),
            n=int(raw_n),
            r=int(raw_r),
            p=int(raw_p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)
