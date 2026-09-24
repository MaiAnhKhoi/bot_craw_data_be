"""V0014 - chỉ mục MỘT PHẦN cho bộ lọc mặc định của trang Địa điểm.

Mọi lượt mở trang Địa điểm đều chạy `WHERE relevance IS NULL OR relevance <>
'weak'` — bộ lọc giấu những dòng đã chấm là lạc đề. Điều kiện đó KHÔNG sargable:
Postgres không biến nó thành `Index Cond` được, chỉ thành `Filter`.

Đo A/B ở 100.000 dòng (cùng một phiên, có chỉ mục / không có chỉ mục):

    lọc quốc gia + trang 1      12,89 ms  ->   7,13 ms   nhanh 1,8 lần
    nhảy sâu OFFSET 79000      193,40 ms  ->  68,72 ms   nhanh 2,8 lần
    trang 1                     0,243 ms  ->  0,243 ms   không đổi
    đếm tổng có lọc            10,93 ms   ->  10,30 ms   KHÔNG dùng chỉ mục này

Hai dòng cuối là chỗ tôi đoán sai lúc đầu, ghi lại để người sau khỏi đoán lại:

  * ĐẾM TỔNG không dùng chỉ mục này. Planner chọn `ix_places_relevance` (648 kB)
    vì rẻ hơn theo mô hình cost (2268 so với 2771) — mô hình đó đếm số page đọc
    chứ không tính CPU của `Filter` trên 100.000 dòng. Ép dùng chỉ mục này thì
    được 10,1 ms không Filter, tức là TỐT HƠN VỀ LÝ THUYẾT nhưng đo thực tế hai
    bên ngang nhau.

  * TRANG 1 không lợi vì 80% số dòng thoả bộ lọc, `Filter` gần như không loại
    dòng nào trước khi gom đủ 50.

Giá trị thật của chỉ mục này nằm ở PHÂN TRANG, không phải ở đếm. Nhảy sâu là chỗ
ăn tiền nhất: không có nó thì Postgres phải Gather Merge + Parallel Seq Scan +
Sort tràn ra ĐĨA (9992 kB); có nó thì đi thẳng chỉ mục, không sort.

Giá phải trả, đo ở 100.000 dòng: chiếm 2,93 MB (3,6% tổng bảng, 6,8% tổng chỉ
mục), làm chậm việc ghi ~4-7 µs mỗi dòng — trong hệ thật có 19 chỉ mục thì con
số đó chìm dưới ngưỡng nhiễu.

Revision ID: V0014
Revises: V0013
"""
from __future__ import annotations

from alembic import op

revision = "V0014"
down_revision = "V0013"
branch_labels = None
depends_on = None

# `(liveness_score DESC, id DESC)` chứ không phải chỉ mục một cột: trang Địa
# điểm luôn LỌC rồi SẮP theo đúng cặp này. Gộp cả hai việc vào một chỉ mục thì
# Postgres vừa bỏ qua được phần lạc đề vừa lấy sẵn thứ tự, không phải sort lại.
#
# Thứ tự khai phải TRÙNG KHÍT với `ORDER BY` của `repository._order` — lệch một
# chi tiết (hướng khoá phụ, vị trí NULL) là chỉ mục thành vô dụng mà không ai
# báo. Xem ghi chú ở `_order`.
TEN = "ix_places_mac_dinh"
DIEU_KIEN = "relevance IS NULL OR relevance <> 'weak'"


def upgrade() -> None:
    op.execute(
        f"CREATE INDEX {TEN} ON places (liveness_score DESC, id DESC) WHERE {DIEU_KIEN}"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {TEN}")
