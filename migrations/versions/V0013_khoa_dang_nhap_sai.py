"""V0013 - khoá tạm khi nhập sai mật khẩu nhiều lần.

Sắp mở hệ thống ra Internet qua Cloudflare Tunnel. Đo thật trước khi làm bản
này: KHÔNG có giới hạn nào, tốc độ dò đạt 20 lần/giây (bcrypt đã làm chậm sẵn).
Mật khẩu ngẫu nhiên 8 ký tự cần ~325 năm nên an toàn — nhưng mật khẩu kiểu
`sale2026` nằm trong vài nghìn cái phổ biến đầu, rụng trong dưới 5 phút.

Revision ID: V0013
Revises: V0012
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "V0013"
down_revision = "V0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    # Cột RIÊNG, không gộp vào `is_active`. `is_active=False` là quản trị chủ
    # động khoá và giữ tới khi có người mở; cột này là hệ thống tự khoá vì nhập
    # sai và TỰ HẾT sau vài phút. Gộp lại là mất khả năng phân biệt "bị kỷ luật"
    # với "gõ nhầm mật khẩu" — người xem bảng tài khoản sẽ hiểu sai.
    op.add_column("users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))

    # Bảng riêng cho tầng chặn theo IP: một IP rải thử lên nhiều tài khoản, có
    # khi lên cả tài khoản KHÔNG TỒN TẠI — không có dòng `users` nào để ghi.
    op.create_table(
        "login_ip_blocks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ip", sa.String(length=45), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("ip", name="uq_login_ip_blocks_ip"),
    )
    op.create_index("ix_login_ip_blocks_ip", "login_ip_blocks", ["ip"])


def downgrade() -> None:
    op.drop_index("ix_login_ip_blocks_ip", table_name="login_ip_blocks")
    op.drop_table("login_ip_blocks")
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_attempts")
