"""V0008 - trạng thái chăm sóc của người bán hàng.

Công cụ đang dừng ở chỗ XUẤT FILE. Sale gọi xong không có chỗ ghi lại "đã gọi /
quan tâm / loại", nên lần quét sau không ai biết ai đã được đụng tới — và với
15-30k lead thì gọi trùng là chuyện chắc chắn xảy ra.

Ba cột này CỐ Ý tách khỏi hai trạng thái sẵn có, vì chúng trả lời ba câu hỏi
khác nhau và một lead có thể mang cả ba cùng lúc:

    status          worker đã quét xong địa điểm này chưa      (pending/done/failed)
    liveness_label  doanh nghiệp còn hoạt động không           (ACTIVE/SUSPECT/DEAD)
    contact_status  BÊN MÌNH đã liên hệ tới đâu                (new/called/interested/rejected)

`server_default = 'new'` để mọi dòng đã quét trước đây được điền sẵn, không phải
NULL — "chưa liên hệ" là một trạng thái thật, không phải thiếu dữ liệu.

Revision ID: V0008
Revises: V0007
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "V0008"
down_revision = "V0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "places",
        sa.Column("contact_status", sa.String(length=12), nullable=False, server_default="new"),
    )
    op.add_column("places", sa.Column("contact_note", sa.Text(), nullable=True))
    op.add_column(
        "places", sa.Column("contact_at", sa.DateTime(timezone=True), nullable=True)
    )
    # Lọc "chỉ xem lead chưa gọi" là thao tác hằng ngày của sale -> phải có chỉ mục.
    op.create_index("ix_places_contact_status", "places", ["contact_status"])


def downgrade() -> None:
    op.drop_index("ix_places_contact_status", table_name="places")
    op.drop_column("places", "contact_at")
    op.drop_column("places", "contact_note")
    op.drop_column("places", "contact_status")
