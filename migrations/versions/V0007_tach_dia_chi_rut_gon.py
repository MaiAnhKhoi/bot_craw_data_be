"""V0007 - tách địa chỉ rút gọn ra khỏi địa chỉ đầy đủ.

Cột `places.address` đang chứa HAI LOẠI GIÁ TRỊ khác hẳn nhau, và đó là gốc của
việc "ô địa chỉ lúc rỗng lúc hiện không đầy đủ". Đo trên dữ liệu thật:

    đã mở trang chi tiết :    22 dòng,  0% rỗng, dài trung bình 58 ký tự
                              "105/20 Đ. 59, An Hội Tây, Hồ Chí Minh, Việt Nam"
    chỉ có thẻ kết quả   : 2.526 dòng, 39% RỖNG, dài trung bình 20 ký tự
                              "Phan Huy Ích"  ·  "183/72/6 Nguyễn Văn Khối"

Một cột mang hai nghĩa thì không có cách hiển thị nào đúng: hiện thẳng ra thì
người dùng tưởng đó là địa chỉ, ẩn đi thì mất dữ liệu của chế độ quét nhanh.

Sau bản này: `address` hoặc ĐẦY ĐỦ hoặc NULL, không có trạng thái thứ ba. Mẩu
trên thẻ chuyển sang `address_short` và được giao diện hiện kèm dấu hiệu "chưa
đầy đủ".

Backfill: mọi dòng có `address` mà KHÔNG đọc ra được tên quốc gia ở đuôi đều là
mẩu từ thẻ — chuyển sang `address_short` rồi xoá khỏi `address`.

Revision ID: V0007
Revises: V0006
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "V0007"
down_revision = "V0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("places", sa.Column("address_short", sa.Text(), nullable=True))
    _backfill()


def downgrade() -> None:
    # Gộp ngược lại để không mất dữ liệu nếu phải lùi bản.
    op.execute("UPDATE places SET address = address_short WHERE address IS NULL")
    op.drop_column("places", "address_short")


def _backfill() -> None:
    """Phân loại lại địa chỉ đang có bằng đúng luật mà code dùng.

    Tiêu chí "đầy đủ" = đọc ra được tên quốc gia ở đuôi chuỗi. Đó cũng chính là
    tiêu chí `writer.country_of_address` dùng, nên dữ liệu cũ và dữ liệu mới
    được phân loại giống hệt nhau — không có hai luật song song.
    """
    from app.modules.scraper.place.writer import country_of_address

    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, address FROM places WHERE address IS NOT NULL AND address <> ''")
    ).fetchall()

    chuyen: list[dict] = []
    for row in rows:
        if not country_of_address(row.address):
            chuyen.append({"pid": row.id, "ngan": row.address})

    if chuyen:
        bind.execute(
            sa.text(
                "UPDATE places SET address_short = :ngan, address = NULL WHERE id = :pid"
            ),
            chuyen,
        )
    print(
        f"V0007: chuyển {len(chuyen)}/{len(rows)} địa chỉ rút gọn sang `address_short`"
    )
