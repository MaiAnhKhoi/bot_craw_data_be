from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.modules.identity.entity import User


class UserResponse(BaseModel):
    id: int
    username: str
    full_name: str | None
    # admin | sale — giao diện dùng nó để ẩn bớt nút cho đỡ rối. Việc CHẶN thật
    # nằm ở `app/core/deps.py::require_admin`, không phải ở đây.
    role: str
    is_active: bool
    created_at: datetime

    @classmethod
    def of(cls, user: User) -> UserResponse:
        return cls(
            id=user.id,
            username=user.username,
            full_name=user.full_name,
            role=user.role,
            is_active=user.is_active,
            created_at=user.created_at,
        )
