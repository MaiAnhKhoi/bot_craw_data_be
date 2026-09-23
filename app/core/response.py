"""Phong bì response thống nhất toàn hệ thống.

Mọi API trả cùng một cấu trúc — FE bóc `data` ở tầng axios interceptor, nên đổi
cấu trúc này là breaking change cho toàn bộ frontend (xem docs/API_CONTRACT.md).
"""
from __future__ import annotations

from pydantic import BaseModel

from app.core.request_context import current_request_id


class ApiError(BaseModel):
    code: str
    message: str


class ApiResponse[T](BaseModel):
    success: bool
    data: T | None = None
    error: ApiError | None = None
    request_id: str = "-"

    @classmethod
    def ok(cls, data: T | None = None) -> ApiResponse[T]:
        return cls(success=True, data=data, request_id=current_request_id())

    @classmethod
    def fail(cls, code: str, message: str) -> ApiResponse[None]:
        return cls(
            success=False,
            error=ApiError(code=code, message=message),
            request_id=current_request_id(),
        )
