"""V0011 - ai đã chấm ngành nghề, và vì sao.

VÌ SAO — V0010 dựng bộ lọc ngành nghề bằng LUẬT CỨNG: so nhãn ngành Google gắn
cho địa điểm với danh mục ngành do AI liệt kê. Đo trên dữ liệu thật, phần sai còn
lại dồn đúng vào hai ca mà luật cứng không thể nào xử được:

    nhãn vô nghĩa   `Battambang Agro Industry Co., Ltd.` bị Google gắn nhãn
                    `Company` -> luật loại oan. Riêng Andaman có 6 dòng
                    `General store` cùng cảnh.
    khớp qua TÊN    `Dress store`, `Book store`, `Tourist attraction` lọt vào
                    chỉ vì trong tên có đúng một chữ dính ngành.

Nên thêm một tầng nữa: mấy ca ranh giới đó được đưa cho AI nhìn tên + nhãn + địa
chỉ rồi quyết giữ hay bỏ. Bản migration này là phần LƯU TRỮ cho tầng đó — ghi lại
AI (hay luật) đã quyết cái gì và vì sao.

Thiếu bốn cột này thì tầng AI thành một hộp đen: một quyết định sai không có cách
nào truy ngược, và người đi sửa không biết mình phải sửa DANH MỤC ngành nghề hay
sửa PROMPT. Đó là hai việc hoàn toàn khác nhau.

    places.relevance_source   'rule' | 'ai'  -> ai đã chấm
    places.relevance_reason   lời AI giải thích, chỉ khi source = 'ai'
    place_rejects.source      'rule' | 'ai'  -> ai đã loại
    place_rejects.reason      lời AI giải thích, chỉ khi source = 'ai'

KHÔNG BACKFILL — xem ghi chú trong `upgrade()`.

Revision ID: V0011
Revises: V0010
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "V0011"
down_revision = "V0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- 1. Nguồn + lý do của điểm chấm ngành nghề ---
    #
    # KHÔNG ĐÁNH CHỈ MỤC, cố ý. Không có màn hình nào lọc hay sắp xếp theo hai cột
    # này: chúng chỉ được đọc khi người dùng đã mở đúng MỘT dòng để hỏi "vì sao nó
    # bị chấm thế". Chỉ mục cho kiểu truy cập đó chỉ tổ làm chậm mỗi lần ghi — mà
    # ghi thì diễn ra hàng nghìn lần một job.
    #
    # VARCHAR(300) cho `relevance_reason`: đủ cho một câu giải thích, và là cái van
    # chặn AI trả về nguyên đoạn văn rồi phình cột lên vô tội vạ.
    #
    # ★ CỐ Ý ĐỂ NULL TOÀN BỘ DỮ LIỆU CŨ, không backfill `'rule'`.
    # Nhìn qua thì mọi dòng có `relevance` từ trước đều do luật cứng chấm, điền
    # `'rule'` vào có vẻ vô hại. Nhưng đó là luật cứng PHIÊN BẢN KHÁC — bản chưa có
    # tầng AI, chưa có mấy ca ranh giới ở trên. Điền `'rule'` là khẳng định một
    # điều mình không kiểm chứng được, và cột này tồn tại chính là để người ta TIN
    # nó lúc đi sửa bộ lọc. Một cột truy vết mà nói dối thì tệ hơn hẳn không có.
    #
    # NULL nghĩa là "không biết ai quyết" — đúng sự thật, và nhìn phát biết ngay là
    # dòng cũ.
    op.add_column("places", sa.Column("relevance_source", sa.String(length=8), nullable=True))
    op.add_column("places", sa.Column("relevance_reason", sa.String(length=300), nullable=True))

    # --- 2. Nguồn + lý do của việc loại bỏ ---
    #
    # `place_rejects` sinh ra để việc loại bỏ không im lặng, nhưng "đã loại" thôi
    # thì chưa đủ. Người dùng mở bảng này ra là để trả lời MỘT câu: bộ lọc có siết
    # quá tay không. Trả lời được câu đó thì phải biết "vì sao loại", chứ nhìn tên
    # với nhãn ngành thì vẫn phải tự đoán.
    #
    # Cũng không backfill, cùng một lý do như trên.
    op.add_column("place_rejects", sa.Column("source", sa.String(length=8), nullable=True))
    op.add_column("place_rejects", sa.Column("reason", sa.String(length=300), nullable=True))


def downgrade() -> None:
    op.drop_column("place_rejects", "reason")
    op.drop_column("place_rejects", "source")
    op.drop_column("places", "relevance_reason")
    op.drop_column("places", "relevance_source")
