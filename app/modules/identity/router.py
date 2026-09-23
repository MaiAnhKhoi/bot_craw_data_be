from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import current_user
from app.core.exceptions import UnauthorizedError
from app.core.response import ApiResponse
from app.core.security import create_access_token, verify_password
from app.modules.identity.entity import User
from app.modules.identity.repository import UserRepository

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class UserResponse(BaseModel):
    id: int
    username: str
    full_name: str | None
    is_active: bool

    @classmethod
    def of(cls, user: User) -> UserResponse:
        return cls(id=user.id, username=user.username, full_name=user.full_name, is_active=user.is_active)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


@router.post("/login", response_model=ApiResponse[LoginResponse], summary="Đăng nhập")
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> ApiResponse[LoginResponse]:
    user = UserRepository(db).by_username(payload.username)
    if user is None or not verify_password(payload.password, user.password_hash):
        # Cùng một thông báo cho cả hai trường hợp: không tiết lộ tài khoản nào tồn tại.
        raise UnauthorizedError("Sai tên đăng nhập hoặc mật khẩu")
    if not user.is_active:
        raise UnauthorizedError("Tài khoản đã bị khoá")
    token, expires_in = create_access_token(user.username)
    return ApiResponse.ok(
        LoginResponse(access_token=token, expires_in=expires_in, user=UserResponse.of(user))
    )


@router.get("/me", response_model=ApiResponse[UserResponse], summary="Thông tin tài khoản đang đăng nhập")
def me(user: User = Depends(current_user)) -> ApiResponse[UserResponse]:
    return ApiResponse.ok(UserResponse.of(user))
