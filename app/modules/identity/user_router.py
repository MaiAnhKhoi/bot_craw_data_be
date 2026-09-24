"""Quản lý tài khoản — CHỈ quản trị.

Tách khỏi `router.py` (đăng nhập, thông tin của chính mình) vì hai nhóm này có
luật truy cập ngược nhau: bên kia ai cũng gọi được, bên này chặn ở cửa bằng
`require_admin`. Trộn vào một file là sớm muộn có endpoint mới bị quên bọc.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_admin
from app.core.pagination import Page, PageParams, page_params
from app.core.response import ApiResponse
from app.modules.identity.entity import User
from app.modules.identity.response import UserResponse
from app.modules.identity.service import VAI_TRO, UserService

router = APIRouter(prefix="/users", tags=["users"])


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    full_name: str | None = Field(None, max_length=128)
    role: str = Field("sale", description=" | ".join(VAI_TRO))


class UserUpdateRequest(BaseModel):
    """Bỏ trống trường nào là GIỮ NGUYÊN trường đó.

    Không dùng `PUT` thay cả bản ghi: giao diện có ba nút riêng (đổi họ tên, đổi
    vai trò, khoá/mở khoá), mỗi nút chỉ biết đúng phần của mình. Bắt nó gửi cả
    bản ghi là mời nó vô tình ghi đè hai phần kia bằng dữ liệu cũ đang cầm.
    """

    full_name: str | None = Field(None, max_length=128)
    role: str | None = Field(None, description=" | ".join(VAI_TRO))
    is_active: bool | None = None


class PasswordRequest(BaseModel):
    new_password: str = Field(min_length=1, max_length=128)


@router.get("", response_model=ApiResponse[Page[UserResponse]], summary="Danh sách tài khoản")
def list_users(
    params: PageParams = Depends(page_params),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> ApiResponse[Page[UserResponse]]:
    return ApiResponse.ok(UserService(db).list(params))


@router.post("", response_model=ApiResponse[UserResponse], summary="Thêm tài khoản")
def create_user(
    payload: UserCreateRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> ApiResponse[UserResponse]:
    user = UserService(db).create(
        payload.username, payload.password, payload.full_name, payload.role
    )
    return ApiResponse.ok(UserResponse.of(user))


@router.patch("/{user_id}", response_model=ApiResponse[UserResponse], summary="Sửa tài khoản")
def update_user(
    user_id: int,
    payload: UserUpdateRequest,
    db: Session = Depends(get_db),
    nguoi_goi: User = Depends(require_admin),
) -> ApiResponse[UserResponse]:
    user = UserService(db).update(
        user_id,
        nguoi_goi,
        full_name=payload.full_name,
        role=payload.role,
        is_active=payload.is_active,
    )
    return ApiResponse.ok(UserResponse.of(user))


@router.post(
    "/{user_id}/password",
    response_model=ApiResponse[dict],
    summary="Đặt lại mật khẩu cho một tài khoản",
)
def set_password(
    user_id: int,
    payload: PasswordRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> ApiResponse[dict]:
    """KHÔNG hỏi mật khẩu cũ — người quên mật khẩu thì không có cái cũ để đưa."""
    UserService(db).set_password(user_id, payload.new_password)
    return ApiResponse.ok({"updated": True})


@router.post(
    "/{user_id}/unlock",
    response_model=ApiResponse[UserResponse],
    summary="Mở khoá tài khoản bị chặn do nhập sai mật khẩu",
)
def unlock_user(
    user_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> ApiResponse[UserResponse]:
    """Chỉ xoá khoá TẠM. Tài khoản bị quản trị khoá hẳn (`is_active=false`) thì
    phải mở bằng `PATCH /users/{id}` — hai việc khác nhau, không gộp."""
    return ApiResponse.ok(UserResponse.of(UserService(db).unlock(user_id)))
