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
    # Khoá TẠM do hệ thống tự đặt khi nhập sai nhiều lần — khác hẳn `is_active`.
    # `is_active=False` là quản trị chủ động khoá, giữ tới khi có người mở; cột
    # này tự hết sau vài phút. Giao diện phải phân biệt được hai thứ, nếu không
    # người xem tưởng nhân viên gõ nhầm mật khẩu là đã bị kỷ luật.
    locked_until: datetime | None
    failed_attempts: int
    created_at: datetime

    @classmethod
    def of(cls, user: User) -> UserResponse:
        return cls(
            id=user.id,
            username=user.username,
            full_name=user.full_name,
            role=user.role,
            is_active=user.is_active,
            locked_until=user.locked_until,
            failed_attempts=user.failed_attempts or 0,
            created_at=user.created_at,
        )
