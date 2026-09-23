"""V0003 - lý do dừng của mỗi truy vấn + quốc gia của mỗi địa điểm.

Hai cột, nhưng cột thứ hai sửa một lỗi dữ liệu im lặng:

1. `job_queries.stop_reason` — `results_found` một mình không trả lời được câu hỏi
   quan trọng nhất sau mỗi lần quét: "địa bàn này đã lấy hết chưa?". Google ngừng
   trả kết quả quanh mốc 110-120 mỗi truy vấn dù địa bàn còn hàng nghìn doanh
   nghiệp. Vòng cuộn VẪN LUÔN phân biệt được ba tình huống (hết danh sách thật /
   bị Google ngắt / chạm trần của mình) nhưng trước đây chỉ ghi vào log debug rồi
   vứt đi. Giờ lưu lại để lọc ra đúng những địa bàn cần chia nhỏ.

2. `places.country_code` — sinh ra vì cột "Quốc gia" trên bảng địa điểm (địa chỉ
   bị cắt ngắn nên nhìn không ra nước nào), nhưng nó còn bịt một lỗ tiềm ẩn:
   VÙNG ĐỂ ĐỌC SỐ ĐIỆN THOẠI. Trước đây cả job dùng chung một vùng, mặc định
   "VN", trong khi số đọc từ THẺ KẾT QUẢ là dạng nội địa:

       "02 281 9715" (Bangkok)  vùng VN -> +8422819715   sai, số không tồn tại
       "081 939 8727" (Bangkok) vùng VN -> +84819398727  sai NHƯNG HỢP LỆ

   Số lấy từ trang chi tiết thì đã ở dạng quốc tế ("+6622819715") nên không dính.
   Vì vậy phần đọc lại bên dưới thường KHÔNG sửa dòng nào — nó chỉ dọn phần dữ
   liệu đã quét ở chế độ không mở trang chi tiết, nếu có.

Revision ID: V0003
Revises: V0002
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "V0003"
down_revision = "V0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_queries", sa.Column("stop_reason", sa.String(length=16), nullable=True))
    op.add_column("places", sa.Column("country_code", sa.String(length=2), nullable=True))
    op.create_index("ix_places_country_code", "places", ["country_code"])
    _backfill()


def downgrade() -> None:
    op.drop_index("ix_places_country_code", table_name="places")
    op.drop_column("places", "country_code")
    op.drop_column("job_queries", "stop_reason")


def _backfill() -> None:
    """Điền quốc gia từ đuôi địa chỉ, rồi đọc lại số điện thoại theo vùng đó.

    Import module của ứng dụng trong migration nói chung là nên tránh, nhưng hai
    module dùng ở đây (`geo.service`, `engine.normalize`) là hàm thuần trên dữ
    liệu tham chiếu tĩnh, không đụng tới ORM hay schema — nên chúng không thể
    lệch pha với bảng theo thời gian. Viết lại luật nhận diện quốc gia bằng SQL
    thì vừa dài vừa sẽ phân kỳ với bản trong code.
    """
    from app.modules.geo import service as geo
    from app.modules.scraper.engine.normalize import normalize_phone

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, address, phone_raw, phone_e164, phone_national, phone_valid "
            "FROM places WHERE address IS NOT NULL AND address <> ''"
        )
    ).fetchall()

    dat_quoc_gia: list[dict] = []
    doc_lai_sdt: list[dict] = []
    for row in rows:
        found = geo.resolve_country(row.address)
        if not found:
            continue
        code = found["code"]
        dat_quoc_gia.append({"pid": row.id, "cc": code})
        if not row.phone_raw:
            continue
        e164, national, valid = normalize_phone(row.phone_raw, code)
        if (e164, national, valid) != (row.phone_e164, row.phone_national, row.phone_valid):
            # Kể cả khi đọc lại ra None: số cũ đã được suy ra bằng vùng SAI, giữ
            # lại chỉ để "có dữ liệu" thì tệ hơn là để trống. `phone_raw` vẫn còn
            # nguyên nên không mất gì.
            doc_lai_sdt.append(
                {"pid": row.id, "e164": e164, "nat": national, "ok": valid}
            )

    if dat_quoc_gia:
        bind.execute(
            sa.text("UPDATE places SET country_code = :cc WHERE id = :pid"), dat_quoc_gia
        )
    if doc_lai_sdt:
        bind.execute(
            sa.text(
                "UPDATE places SET phone_e164 = :e164, phone_national = :nat, "
                "phone_valid = :ok WHERE id = :pid"
            ),
            doc_lai_sdt,
        )
    print(
        f"V0003: gán quốc gia cho {len(dat_quoc_gia)} địa điểm, "
        f"đọc lại {len(doc_lai_sdt)} số điện thoại sai vùng"
    )
