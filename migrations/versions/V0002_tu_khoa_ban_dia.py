"""V0002 - từ khoá bản địa theo quốc gia + ngôn ngữ/quốc gia tìm kiếm theo từng truy vấn.

Hai việc:

1. `job_queries.hl` / `job_queries.gl` — trước đây cả job dùng chung một cặp
   ngôn ngữ/quốc gia. Sai ngay khi job trải nhiều nước: đo thực tế cho thấy tìm
   "xuất nhập khẩu trái cây Bangkok" với gl=vn trả về 2 cửa hàng ở TP.HCM, tức là
   Google bỏ qua luôn chữ Bangkok. Giá trị mặc định 'vi'/'vn' giữ nguyên hành vi
   cũ cho các job đã tạo trước khi nâng cấp.

2. `keyword_translations` — bộ nhớ đệm từ khoá bản địa do AI sinh, khoá theo
   (bộ từ khoá gốc, quốc gia). Nhờ đó hỏi lại cùng một bộ từ khoá không tốn thêm
   lượt gọi AI, và bản người dùng sửa tay được giữ lại.

Revision ID: V0002
Revises: V0001
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "V0002"
down_revision = "V0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default để các dòng đã có sẵn được điền giá trị, không phải NULL.
    op.add_column(
        "job_queries",
        sa.Column("hl", sa.String(length=8), nullable=False, server_default="vi"),
    )
    op.add_column(
        "job_queries",
        sa.Column("gl", sa.String(length=8), nullable=False, server_default="vn"),
    )

    op.create_table(
        "keyword_translations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_hash", sa.String(length=40), nullable=False),
        sa.Column("source_keywords", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("country_code", sa.String(length=4), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False, server_default="en"),
        sa.Column("keywords", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("edited_by_user", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_hash", "country_code", name="uq_keyword_translations"),
    )
    op.create_index("ix_keyword_translations_source_hash", "keyword_translations", ["source_hash"])
    op.create_index("ix_keyword_translations_country_code", "keyword_translations", ["country_code"])


def downgrade() -> None:
    op.drop_index("ix_keyword_translations_country_code", table_name="keyword_translations")
    op.drop_index("ix_keyword_translations_source_hash", table_name="keyword_translations")
    op.drop_table("keyword_translations")
    op.drop_column("job_queries", "gl")
    op.drop_column("job_queries", "hl")
