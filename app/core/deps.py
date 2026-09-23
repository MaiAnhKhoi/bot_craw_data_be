"""Dependency dùng chung cho router."""
from __future__ import annotations

from fastapi import Depends, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import UnauthorizedError
from app.core.security import decode_access_token
from app.modules.identity.entity import User
from app.modules.identity.repository import UserRepository

_bearer = HTTPBearer(auto_error=False)


def _user_from_token(token: str, db: Session) -> User:
    username = decode_access_token(token)
    user = UserRepository(db).by_username(username)
    if user is None or not user.is_active:
        raise UnauthorizedError("Tài khoản không tồn tại hoặc đã bị khoá")
    return user


def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None or not creds.credentials:
        raise UnauthorizedError("Thiếu token đăng nhập")
    return _user_from_token(creds.credentials, db)


def current_user_sse(
    token: str | None = Query(None, description="Token đăng nhập (EventSource không gắn được header)"),
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """Biến thể cho endpoint SSE.

    `EventSource` của trình duyệt không cho gắn header Authorization, nên endpoint
    luồng sự kiện chấp nhận token qua query string. Chỉ dùng cho endpoint CHỈ ĐỌC.
    """
    raw = (creds.credentials if creds else None) or token
    if not raw:
        raise UnauthorizedError("Thiếu token đăng nhập")
    return _user_from_token(raw, db)
