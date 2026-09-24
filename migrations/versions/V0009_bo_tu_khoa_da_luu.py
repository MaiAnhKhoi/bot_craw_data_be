"""V0009 - bộ từ khoá có tên, để gọi lại chính xác mà không phải gõ tay.

Bộ nhớ đệm bản dịch (`keyword_translations`) đã khoá theo hàm băm của chính bộ
từ khoá, và hàm băm đó bền với đảo thứ tự, hoa/thường, khoảng trắng thừa. Đo thật:

    gõ y hệt / đảo thứ tự / khác hoa thường / thừa khoảng trắng  -> TRÚNG đệm, miễn phí
    thiếu một từ  ·  sai một chữ                                  -> KHÁC khoá, TỐN AI lại

Chỗ hỏng không nằm ở cơ chế đệm mà ở chỗ người dùng phải GÕ LẠI TAY một bộ mười
từ khoá tiếng Việt sau vài tuần. Sai một chữ là trả tiền lại cho toàn bộ các nước.

Bảng này chỉ giữ nguyên văn bộ từ khoá kèm một cái tên. Nó KHÔNG lưu bản dịch —
bản dịch vẫn ở `keyword_translations`, nối qua `source_hash`.

Revision ID: V0009
Revises: V0008
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "V0009"
down_revision = "V0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "keyword_sets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        # Tên đã chuẩn hoá. Có cột riêng thì UNIQUE mới chặn được "Trái cây" và
        # "trái cây" cùng tồn tại — so bằng lower() lúc truy vấn thì không.
        sa.Column("name_key", sa.String(length=200), nullable=False),
        sa.Column("keywords", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source_hash", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name_key", name="uq_keyword_sets_name"),
    )
    op.create_index("ix_keyword_sets_name_key", "keyword_sets", ["name_key"])
    # Dùng để đếm "bộ này đã dịch sẵn bao nhiêu nước" bằng một phép JOIN sang
    # keyword_translations.
    op.create_index("ix_keyword_sets_source_hash", "keyword_sets", ["source_hash"])


def downgrade() -> None:
    op.drop_index("ix_keyword_sets_source_hash", table_name="keyword_sets")
    op.drop_index("ix_keyword_sets_name_key", table_name="keyword_sets")
    op.drop_table("keyword_sets")
