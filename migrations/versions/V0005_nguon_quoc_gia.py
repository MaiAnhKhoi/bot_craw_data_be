"""V0005 - nguồn xác định quốc gia của mỗi địa điểm.

Trước bản này, `country_code` được suy theo `địa chỉ -> giá trị đã lưu -> gl`.
Đo trên dữ liệu thật: 23% số dòng có địa chỉ RÚT GỌN từ thẻ kết quả nên không
đọc ra tên nước và phải rơi về `gl` — mà `gl` chỉ nói ta đã TÌM ở nước nào.

Bản này thêm TOẠ ĐỘ vào chuỗi (Google cho toạ độ ở 100% số dòng) và ghi lại
NGUỒN đã quyết định. Nguồn quan trọng vì đây là một phỏng đoán, và phỏng đoán
vô hình là thứ nguy hiểm nhất ở đây: đoán sai quốc gia thì số điện thoại NỘI ĐỊA
bị đọc sai vùng, mà "081 882 1104" đọc theo TH hay VN đều ra số HỢP LỆ — không
bộ kiểm tra nào bắt được, và số sai nằm im trong file xuất cho sale gọi.

Phần backfill tính lại quốc gia cho toàn bộ dữ liệu cũ bằng chuỗi mới, rồi đọc
lại số điện thoại của những dòng đổi quốc gia.

Revision ID: V0005
Revises: V0004
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "V0005"
down_revision = "V0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("places", sa.Column("country_source", sa.String(length=8), nullable=True))
    op.create_index("ix_places_country_source", "places", ["country_source"])
    _backfill()


def downgrade() -> None:
    op.drop_index("ix_places_country_source", table_name="places")
    op.drop_column("places", "country_source")


def _backfill() -> None:
    """Tính lại quốc gia bằng chuỗi mới (địa chỉ > toạ độ > cũ > gl) và ghi nguồn.

    Không biết `gl` của lần quét cũ nên dùng chính `country_code` đang lưu làm
    giá trị cũ: nếu địa chỉ hoặc toạ độ cho ra kết quả mạnh hơn thì nó được nâng
    cấp, còn không thì giữ nguyên và đánh dấu nguồn là 'gl' — đúng bản chất, vì
    đó chính là thứ đã sinh ra giá trị cũ.
    """
    from app.modules.scraper.engine.normalize import normalize_phone
    from app.modules.scraper.place.writer import resolve_place_country

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, address, lat, lng, country_code, phone_raw, phone_e164, "
            "phone_national, phone_valid FROM places"
        )
    ).fetchall()

    dat_nguon: list[dict] = []
    doc_lai_sdt: list[dict] = []
    for row in rows:
        ma, nguon = resolve_place_country(
            row.address, row.country_code, None, row.country_code, row.lat, row.lng
        )
        if not ma:
            continue
        dat_nguon.append({"pid": row.id, "cc": ma, "src": nguon})
        if not row.phone_raw:
            continue
        e164, national, valid = normalize_phone(row.phone_raw, ma)
        if (e164, national, valid) != (row.phone_e164, row.phone_national, row.phone_valid):
            doc_lai_sdt.append({"pid": row.id, "e164": e164, "nat": national, "ok": valid})

    if dat_nguon:
        bind.execute(
            sa.text("UPDATE places SET country_code = :cc, country_source = :src WHERE id = :pid"),
            dat_nguon,
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
        f"V0005: gán lại quốc gia + nguồn cho {len(dat_nguon)} địa điểm, "
        f"đọc lại {len(doc_lai_sdt)} số điện thoại"
    )
