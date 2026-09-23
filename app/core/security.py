"""Băm mật khẩu + JWT."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.core.config import get_settings
from app.core.exceptions import UnauthorizedError


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(subject: str) -> tuple[str, int]:
    """Trả (token, số giây còn hiệu lực)."""
    s = get_settings()
    expire_seconds = s.access_token_expire_minutes * 60
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expire_seconds)).timestamp()),
    }
    return jwt.encode(payload, s.jwt_secret_key, algorithm=s.jwt_algorithm), expire_seconds


def decode_access_token(token: str) -> str:
    """Trả `sub` (username). Ném UnauthorizedError nếu token hỏng/hết hạn."""
    s = get_settings()
    try:
        payload = jwt.decode(token, s.jwt_secret_key, algorithms=[s.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("Phiên đăng nhập đã hết hạn") from exc
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("Token không hợp lệ") from exc
    sub = payload.get("sub")
    if not sub:
        raise UnauthorizedError("Token không hợp lệ")
    return str(sub)
