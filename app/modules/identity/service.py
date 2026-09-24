"""Quản lý tài khoản. Transaction nằm ở đây; router không commit.

Mọi phép chặn trong file này đều nhằm một việc: KHÔNG ĐỂ HỆ THỐNG TỰ KHOÁ CỬA.
Công cụ chạy trong mạng nội bộ, không có màn khôi phục mật khẩu, không có kênh
nào khác để vào. Hạ vai trò hay khoá nhầm tài khoản quản trị cuối cùng là phải
chui vào Docker gõ SQL mới cứu được — nên phải chặn ngay tại đây.
"""
from __future__ import annotations

from app.core.exceptions import AppError, NotFoundError
from app.core.pagination import Page, PageParams
from app.core.security import hash_password, verify_password
from app.modules.identity.entity import User
from app.modules.identity.repository import UserRepository

VAI_TRO = ("admin", "sale")
DAI_MAT_KHAU_TOI_THIEU = 8


def _kiem_mat_khau(mat_khau: str) -> str:
    """Luật mật khẩu cố ý CHỈ có độ dài.

    Bắt buộc ký tự hoa/thường/số/đặc biệt nghe có vẻ chặt hơn, nhưng thực tế nó
    đẩy người dùng tới `Sale@2024` — dễ đoán hơn hẳn một câu dài. Ở đây chỉ chặn
    mật khẩu quá ngắn, phần còn lại tin người dùng.
    """
    mat_khau = (mat_khau or "").strip()
    if len(mat_khau) < DAI_MAT_KHAU_TOI_THIEU:
        raise AppError(
            f"Mật khẩu phải dài ít nhất {DAI_MAT_KHAU_TOI_THIEU} ký tự",
            code="VALIDATION_ERROR",
            status_code=422,
        )
    return mat_khau


def _kiem_vai_tro(vai_tro: str) -> str:
    if vai_tro not in VAI_TRO:
        raise AppError(
            f"Vai trò phải là {' hoặc '.join(VAI_TRO)}",
            code="VALIDATION_ERROR",
            status_code=422,
        )
    return vai_tro


class UserService:
    def __init__(self, db) -> None:  # noqa: ANN001 — Session
        self.db = db
        self.repo = UserRepository(db)

    def list(self, params: PageParams):  # noqa: ANN201
        from app.modules.identity.response import UserResponse

        rows, total = self.repo.page(params)
        return Page.build([UserResponse.of(u) for u in rows], params, total)

    def create(self, username: str, password: str, full_name: str | None, role: str) -> User:
        username = " ".join((username or "").split()).lower()
        if not username:
            raise AppError("Thiếu tên đăng nhập", code="VALIDATION_ERROR", status_code=422)
        if self.repo.by_username(username) is not None:
            raise AppError(
                f"Tên đăng nhập '{username}' đã có người dùng",
                code="VALIDATION_ERROR",
                status_code=409,
            )
        user = self.repo.add(
            User(
                username=username,
                password_hash=hash_password(_kiem_mat_khau(password)),
                full_name=(full_name or "").strip() or None,
                role=_kiem_vai_tro(role),
            )
        )
        self.db.commit()
        self.db.refresh(user)
        return user

    def update(
        self,
        user_id: int,
        nguoi_goi: User,
        full_name: str | None = None,
        role: str | None = None,
        is_active: bool | None = None,
    ) -> User:
        user = self.repo.by_id(user_id)
        if user is None:
            raise NotFoundError(f"Không tìm thấy tài khoản {user_id}")

        # Tự khoá mình hoặc tự hạ vai trò mình: chặn thẳng, không cần xét gì
        # thêm. Kể cả còn quản trị khác thì đây gần như luôn là bấm nhầm, và hậu
        # quả tức thì là người đang thao tác bị đá ra khỏi màn hình họ đang mở.
        if user.id == nguoi_goi.id:
            if is_active is False:
                raise AppError(
                    "Không thể tự khoá tài khoản của chính mình",
                    code="VALIDATION_ERROR",
                    status_code=422,
                )
            if role is not None and role != user.role:
                raise AppError(
                    "Không thể tự đổi vai trò của chính mình",
                    code="VALIDATION_ERROR",
                    status_code=422,
                )

        # Hạ vai trò / khoá tài khoản quản trị CUỐI CÙNG đang hoạt động.
        mat_quyen_admin = (role is not None and role != "admin") or is_active is False
        if user.role == "admin" and mat_quyen_admin:
            if self.repo.dem_admin_dang_hoat_dong(tru_id=user.id) == 0:
                raise AppError(
                    "Đây là tài khoản quản trị duy nhất còn hoạt động. "
                    "Hãy tạo hoặc mở khoá một quản trị khác trước.",
                    code="VALIDATION_ERROR",
                    status_code=422,
                )

        if full_name is not None:
            user.full_name = full_name.strip() or None
        if role is not None:
            user.role = _kiem_vai_tro(role)
        if is_active is not None:
            user.is_active = is_active
        self.db.commit()
        self.db.refresh(user)
        return user

    def set_password(self, user_id: int, new_password: str) -> None:
        """Quản trị đặt lại mật khẩu cho người khác.

        KHÔNG hỏi mật khẩu cũ: người quên mật khẩu thì không có mật khẩu cũ để
        đưa, mà đó chính là lý do duy nhất chức năng này tồn tại.
        """
        user = self.repo.by_id(user_id)
        if user is None:
            raise NotFoundError(f"Không tìm thấy tài khoản {user_id}")
        user.password_hash = hash_password(_kiem_mat_khau(new_password))
        self.db.commit()

    def change_own_password(self, user: User, current_password: str, new_password: str) -> None:
        """Người dùng tự đổi mật khẩu của mình.

        BẮT BUỘC hỏi mật khẩu hiện tại — khác hẳn `set_password`. Máy văn phòng
        hay để đăng nhập sẵn; không hỏi thì ai ngồi vào máy bỏ trống cũng đổi
        được mật khẩu và chiếm luôn tài khoản.
        """
        if not verify_password(current_password or "", user.password_hash):
            raise AppError(
                "Mật khẩu hiện tại không đúng", code="VALIDATION_ERROR", status_code=422
            )
        user.password_hash = hash_password(_kiem_mat_khau(new_password))
        self.db.commit()
