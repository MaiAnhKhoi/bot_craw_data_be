from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    full_name: Mapped[str | None] = mapped_column(String(128))
    # `admin` đặt lệnh quét và quản lý tài khoản · `sale` chỉ đọc dữ liệu và chăm
    # sóc lead.
    #
    # Vì sao sale KHÔNG được tạo job: hệ thống chỉ có MỘT worker chạy tuần tự
    # trên MỘT IP văn phòng. Một job đặt sai (chọn 40 nước) chiếm worker cả ngày
    # và làm tăng rủi ro Google chặn IP — mà bị chặn là CẢ CÔNG TY mất dùng, chứ
    # không riêng người gây ra. Nút gợi ý từ khoá còn gọi AI tốn tiền thật.
    #
    # Mặc định `sale` chứ không phải `admin`: quên khai vai trò lúc tạo tài
    # khoản thì người đó được ÍT quyền hơn mong đợi, chứ không phải nhiều hơn.
    role: Mapped[str] = mapped_column(String(8), default="sale", server_default="sale")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # Số lần nhập sai LIÊN TIẾP. Đăng nhập đúng một lần là về 0.
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Mốc hết khoá tạm. NULL = không bị khoá.
    #
    # Khác hẳn `is_active`: `is_active=False` là quản trị CHỦ ĐỘNG khoá, giữ tới
    # khi có người mở; còn cột này là hệ thống tự khoá vì nhập sai nhiều, và nó
    # TỰ HẾT sau vài phút. Gộp hai thứ vào một cột là mất luôn khả năng phân biệt
    # "bị kỷ luật" với "gõ nhầm mật khẩu".
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
