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
from app.modules.identity.response import UserResponse
from app.modules.identity.service import UserService

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


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


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


@router.post(
    "/change-password",
    response_model=ApiResponse[dict],
    summary="Tự đổi mật khẩu của tài khoản đang đăng nhập",
)
def change_password(
    payload: ChangePasswordRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> ApiResponse[dict]:
    """Ai đăng nhập cũng gọi được — đây là mật khẩu CỦA CHÍNH HỌ.

    BẮT BUỘC hỏi mật khẩu hiện tại. Máy văn phòng hay để đăng nhập sẵn; không
    hỏi thì ai ngồi vào máy bỏ trống cũng đổi được mật khẩu và chiếm tài khoản.
    """
    UserService(db).change_own_password(user, payload.current_password, payload.new_password)
    return ApiResponse.ok({"updated": True})
