"""V0006 - chỉ mục cho cột sắp xếp và cho nhịp đập của luồng realtime.

Số đo trên 300.000 dòng (bảng test, cùng cấu hình Postgres):

    Sắp xếp mặc định (liveness), trang 1     84 ms  ->  0,43 ms   (200x)
    Câu `pulse()` của SSE                    70 ms  ->  2,4  ms   (29x)

Bài học chung của cả hai: chậm là do THIẾU CHỈ MỤC, không phải do kỹ thuật phân
trang sai hay thiếu một tầng cache. Đã cân nhắc và loại Keyset/Cursor (mất khả
năng nhảy trang, mà giao diện có ô số trang), Page Directory (một bảng phụ phải
đồng bộ với mọi lần ghi) và Redis (không giải quyết được gì mà câu truy vấn
không tự giải quyết được).

Chi phí ghi: worker đơn luồng ghi ~120 dòng mỗi 1-3 phút. Sáu chỉ mục thêm vào
là không đáng kể ở nhịp đó.

Revision ID: V0006
Revises: V0005
"""
from __future__ import annotations

from alembic import op

revision = "V0006"
down_revision = "V0005"
branch_labels = None
depends_on = None

# (tên, các cột) — `id DESC` đi kèm để chỉ mục phủ luôn tiêu chí phá hoà bền
# vững của câu ORDER BY, nhờ vậy Postgres không phải sắp xếp lại lần nữa.
CHI_MUC_SAP_XEP = (
    ("ix_places_liveness_score", "liveness_score DESC, id DESC"),
    ("ix_places_rating", "rating DESC NULLS LAST, id DESC"),
    ("ix_places_review_count", "review_count DESC NULLS LAST, id DESC"),
)

# `pulse()` lấy `max()` của ba cột mốc thời gian. Có chỉ mục thì mỗi `max()` chỉ
# là một lần đọc đầu chỉ mục thay vì quét cả bảng.
CHI_MUC_NHIP = (
    ("ix_places_last_seen_at", "last_seen_at DESC"),
    ("ix_places_last_verified_at", "last_verified_at DESC"),
    ("ix_places_website_checked_at", "website_checked_at DESC"),
)

TAT_CA = CHI_MUC_SAP_XEP + CHI_MUC_NHIP


def upgrade() -> None:
    # CREATE INDEX thường (không CONCURRENTLY) vì alembic chạy trong transaction.
    # Nó khoá ghi trong lúc dựng, nhưng ở cỡ bảng của công cụ này thì tính bằng
    # mili-giây — worker chỉ khựng lại trong chớp mắt.
    for ten, cot in TAT_CA:
        op.execute(f"CREATE INDEX IF NOT EXISTS {ten} ON places ({cot})")


def downgrade() -> None:
    for ten, _ in TAT_CA:
        op.execute(f"DROP INDEX IF EXISTS {ten}")
