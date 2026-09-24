"""V0012 - vai trò tài khoản: admin đặt lệnh quét, sale chỉ đọc.

Công cụ này từ đầu chỉ có MỘT tài khoản `admin` dùng chung. Chia mật khẩu đó cho
cả phòng sale nghĩa là: không biết ai làm gì, ai đổi mật khẩu là cả phòng đứng,
và người nghỉ việc vẫn vào được.

Revision ID: V0012
Revises: V0011
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "V0012"
down_revision = "V0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `server_default='sale'` chứ không phải 'admin': quên khai vai trò lúc tạo
    # tài khoản thì người đó được ÍT quyền hơn mong đợi, không phải nhiều hơn.
    op.add_column(
        "users",
        sa.Column("role", sa.String(length=8), nullable=False, server_default="sale"),
    )
    # Nhưng những tài khoản ĐANG CÓ phải thành `admin`.
    #
    # Trước bản này chỉ tồn tại đúng một tài khoản, và nó là tài khoản quản trị
    # sinh tự động lúc khởi động (`app/main.py::_seed_admin`). Để nó rơi về mặc
    # định `sale` là khoá luôn người dùng duy nhất ra khỏi chính hệ thống của họ
    # — không ai tạo được tài khoản mới, không ai đặt được lệnh quét nữa.
    op.execute("UPDATE users SET role = 'admin'")


def downgrade() -> None:
    op.drop_column("users", "role")
