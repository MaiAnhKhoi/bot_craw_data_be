"""V0010 - lưu trữ cho việc lọc địa điểm ngoài ngành nghề.

VÌ SAO — Google Maps không bao giờ trả danh sách ngắn. Hết kết quả khớp thật,
nó ĐỘN THÊM thứ loãng dần ở gần đó cho đủ dài. Đo trên job "Công ty trái cây"
quét quần đảo Andaman (174 địa điểm), riêng truy vấn `produce supplier` trả 118
kết quả thì 51% lạc đề — tiệm bánh kem, hiệu sách, cửa hàng quần áo, đại lý du
lịch. Độ đúng ngành theo vị trí trong danh sách:

    vị trí   1–20    90% đúng ngành
    vị trí  41–60    65%
    vị trí  61–80    20%
    vị trí 101–118   11%

Bản này thêm bốn thứ, và cả bốn đều xoay quanh một nguyên tắc: bộ lọc là PHỎNG
ĐOÁN, nên mọi thứ nó bỏ đi phải NHÌN THẤY ĐƯỢC.

    keyword_translations.categories  danh mục ngành nghề dùng để lọc
    places.relevance                 điểm chấm từng địa điểm (match/weak/NULL)
    scrape_jobs.rejected_count       loại bao nhiêu -> hiện lên giao diện
    place_rejects                    loại những gì  -> soi lại được từng dòng

KHÔNG BACKFILL `places.relevance` — xem ghi chú trong `upgrade()`.

Revision ID: V0010
Revises: V0009
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "V0010"
down_revision = "V0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- 1. Danh mục ngành nghề, nằm cùng dòng với bản dịch từ khoá ---
    #
    # AI sinh `categories` CÙNG MỘT LƯỢT với `keywords` và cùng phụ thuộc
    # `source_hash`, nên chúng phải sống chung một dòng. Tách ra bảng riêng thì
    # sẽ có lúc một bên trúng bộ nhớ đệm còn bên kia không, và job đi lọc bằng
    # danh mục của một bộ từ khoá khác.
    #
    # NOT NULL + '[]' thay vì cho phép NULL: mọi dòng đã dịch trước bản này đều
    # là "chưa có danh mục", mà "chưa có" đọc tự nhiên nhất là danh sách rỗng —
    # code lọc chỉ cần `if not categories` là đủ, không phải phân biệt NULL với [].
    op.add_column(
        "keyword_translations",
        sa.Column(
            "categories",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )

    # --- 2. Điểm chấm ngành nghề của từng địa điểm ---
    #
    # CỐ Ý ĐỂ NULL TOÀN BỘ DỮ LIỆU CŨ, không backfill. Dữ liệu cũ được quét
    # trước khi có danh mục ngành nghề: không lưu lại job nào/từ khoá nào đã tìm
    # ra nó với danh mục gì, nên không có căn cứ nào để chấm ngược. Chấm bừa rồi
    # ẩn đi chính là kiểu hỏng âm thầm mà dự án này sợ nhất — hàng nghìn lead
    # biến mất khỏi bảng vì một phép đoán không ai nhìn thấy.
    #
    # NULL nghĩa là "chưa chấm", và "chưa chấm" KHÔNG BAO GIỜ bị ẩn: dữ liệu cũ
    # vẫn hiện nguyên như trước bản này. Quét lại thì chúng được chấm thật.
    op.add_column("places", sa.Column("relevance", sa.String(length=8), nullable=True))
    # "Chỉ xem kết quả đúng ngành" là bộ lọc mặc định của bảng Địa điểm, chạy
    # trên mọi lần mở trang -> phải có chỉ mục.
    op.create_index("ix_places_relevance", "places", ["relevance"])

    # --- 3. Đếm số bị loại, để job không im lặng ---
    #
    # Kết quả bị loại không vào bảng `places` nên không con số nào khác của job
    # nhắc tới chúng. Thiếu cột này thì một job loại 96/174 kết quả vẫn báo
    # "xong, 78 kết quả" và người dùng không có cách nào biết bộ lọc siết quá tay.
    op.add_column(
        "scrape_jobs",
        sa.Column("rejected_count", sa.Integer(), nullable=False, server_default="0"),
    )

    # --- 4. Nhật ký loại bỏ ---
    #
    # Giữ TỐI THIỂU và CỐ Ý nằm ngoài bảng `places`: không lọt vào bảng chính,
    # không lọt vào file xuất, không được tính là kết quả. Chỉ để soi lại khi
    # nghi bộ lọc vứt nhầm lead thật. Không số điện thoại, không địa chỉ — đây
    # không phải danh sách lead.
    op.create_table(
        "place_rejects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("query", sa.String(length=300), nullable=False),
        sa.Column("feature_id", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("category", sa.String(length=160), nullable=True),
        sa.Column("maps_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        # Xoá job là xoá luôn nhật ký của nó: mấy dòng này chỉ có nghĩa khi còn
        # job để đối chiếu.
        sa.ForeignKeyConstraint(["job_id"], ["scrape_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Luôn đọc theo job ("job này đã loại những gì") -> chỉ mục trên job_id.
    op.create_index("ix_place_rejects_job_id", "place_rejects", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_place_rejects_job_id", table_name="place_rejects")
    op.drop_table("place_rejects")
    op.drop_column("scrape_jobs", "rejected_count")
    op.drop_index("ix_places_relevance", table_name="places")
    op.drop_column("places", "relevance")
    op.drop_column("keyword_translations", "categories")
