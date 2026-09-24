"""Xuất Excel / CSV / JSON.

Bốn cột nghiệp vụ đứng đầu (tên công ty, vị trí, SĐT, website), rồi tới cột tình
trạng hoạt động — đó là thứ sale cần để biết nên gọi ai trước. Các cột kỹ thuật
xếp cuối.

Ghi theo luồng (`constant_memory` của xlsxwriter, `yield_per` của SQLAlchemy) nên
xuất 100k dòng vẫn không phình RAM.
"""
from __future__ import annotations

import csv
import json
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

import xlsxwriter

from app.modules.geo import service as geo
from app.modules.scraper.engine.normalize import phone_country, to_international
from app.modules.scraper.place.entity import Place

LIVENESS_VI = {
    "ACTIVE": "Đang hoạt động",
    "SUSPECT": "Nghi ngờ",
    "DEAD": "Nhiều khả năng đã ngừng",
}
REASON_VI = {
    "CLOSED_PERMANENTLY": "Google ghi nhận đã đóng cửa vĩnh viễn",
    "CLOSED_TEMPORARILY": "Đang tạm thời đóng cửa",
    "NO_PHONE": "Không có số điện thoại",
    "INVALID_PHONE": "Số điện thoại không hợp lệ",
    "NO_WEBSITE": "Không có website",
    "WEBSITE_DEAD": "Website không truy cập được",
    "WEBSITE_PARKED": "Website chỉ còn trang đỗ tên miền",
    "NO_REVIEWS": "Chưa có đánh giá nào",
    "FEW_REVIEWS": "Rất ít đánh giá",
    "REVIEWS_STALE_1Y": "Không có đánh giá mới trong 1 năm",
    "REVIEWS_STALE_2Y": "Không có đánh giá mới trong 2 năm",
    "REVIEWS_STALE_3Y": "Không có đánh giá mới trong 3 năm",
    "NO_HOURS": "Không công bố giờ mở cửa",
}
CONTACT_VI = {
    "new": "Chưa liên hệ",
    "called": "Đã gọi",
    "interested": "Quan tâm",
    "rejected": "Loại",
}
WEBSITE_STATUS_VI = {
    "OK": "Còn hoạt động",
    "DEAD": "Không truy cập được",
    "PARKED": "Trang đỗ tên miền",
    "UNCHECKED": "Chưa kiểm tra",
    "NONE": "Không có",
}

NGUON_QUOC_GIA_VI = {
    "address": "Địa chỉ",
    "coords": "Toạ độ",
    "gl": "Đoán theo nước đang tìm",
}

# (khoá, tiêu đề cột, hàm lấy giá trị, độ rộng)
#
# KHOÁ là thứ giao diện gửi lên để chọn cột, nên nó KHÔNG BAO GIỜ được đổi —
# đổi một khoá là mọi liên kết tải file người dùng đã lưu lại im lặng xuất
# thiếu cột đó. Tiêu đề thì sửa thoải mái, nó chỉ để người đọc.
COLUMNS: tuple[tuple[str, str, Callable[[Place, list[str]], object], int], ...] = (
    ("name", "Tên công ty", lambda p, k: p.name, 42),
    # Dùng được thì dùng địa chỉ đầy đủ, không có thì đành lấy mẩu từ thẻ —
    # nhưng cột kế bên nói rõ đó là mẩu, để không ai gửi thư tới "Phan Huy Ích".
    ("location", "Vị trí", lambda p, k: p.address or p.address_short, 50),
    ("address_full", "Địa chỉ đầy đủ?", lambda p, k: "Có" if p.address else "Chưa", 15),
    ("country", "Quốc gia", lambda p, k: geo.country_name(p.country_code), 16),
    # Cột này để người dùng biết dòng nào là PHỎNG ĐOÁN yếu mà soi lại.
    ("country_source", "Nguồn quốc gia", lambda p, k: NGUON_QUOC_GIA_VI.get(p.country_source or "", ""), 20),
    # Dạng quốc tế: file xuất đi ra ngoài phần mềm này, mất mã nước là mất luôn
    # thông tin gọi đi nước nào — và số Thái trông y hệt số Việt Nam.
    ("phone", "Số điện thoại", lambda p, k: to_international(p.phone_e164, p.phone_national or p.phone_raw), 20),
    ("website", "Website", lambda p, k: p.website, 34),
    ("liveness", "Tình trạng", lambda p, k: LIVENESS_VI.get(p.liveness_label, p.liveness_label), 22),
    # Xuất kèm để sale làm việc ngay trên file Excel mà vẫn biết ai đã gọi rồi.
    ("contact", "Chăm sóc", lambda p, k: CONTACT_VI.get(p.contact_status, p.contact_status), 16),
    ("contact_note", "Ghi chú chăm sóc", lambda p, k: p.contact_note, 40),
    ("liveness_score", "Điểm tình trạng", lambda p, k: p.liveness_score, 15),
    (
        "liveness_reasons",
        "Lý do nghi ngờ",
        lambda p, k: "; ".join(REASON_VI.get(r, r) for r in (p.liveness_reasons or [])),
        46,
    ),
    ("category", "Ngành nghề", lambda p, k: p.category, 26),
    ("rating", "Điểm đánh giá", lambda p, k: p.rating, 13),
    ("review_count", "Số đánh giá", lambda p, k: p.review_count, 12),
    ("latest_review_days", "Đánh giá mới nhất (ngày)", lambda p, k: p.latest_review_days, 20),
    (
        "website_status",
        "Tình trạng website",
        lambda p, k: WEBSITE_STATUS_VI.get(p.website_status, p.website_status),
        18,
    ),
    ("phone_e164", "SĐT chuẩn E.164", lambda p, k: p.phone_e164, 18),
    ("lat", "Vĩ độ", lambda p, k: p.lat, 11),
    ("lng", "Kinh độ", lambda p, k: p.lng, 11),
    ("keywords", "Từ khoá tìm ra", lambda p, k: " | ".join(k), 34),
    ("maps_url", "Link Google Maps", lambda p, k: p.maps_url, 40),
    ("scraped_at", "Thời điểm quét", lambda p, k: p.scraped_at.isoformat() if p.scraped_at else None, 22),
)

KHOA_COT = tuple(c[0] for c in COLUMNS)
# Tiêu đề theo đúng thứ tự chuẩn. Giữ lại vì test và vài chỗ đọc file cần
# biết cột nào nằm ở đâu khi xuất đủ.
HEADERS = [c[1] for c in COLUMNS]


def chon_cot(khoa: list[str] | None) -> tuple:
    """Lọc COLUMNS theo danh sách khoá giao diện gửi lên.

    Giữ THỨ TỰ CHUẨN của `COLUMNS`, không theo thứ tự người dùng gửi: bốn cột
    nghiệp vụ đứng đầu là một quyết định có chủ đích (tên, vị trí, SĐT, website —
    thứ sale cần trước), và một file xuất mỗi lần một thứ tự cột thì không ai
    dựng được công thức Excel trên đó.

    Khoá lạ bị BỎ QUA chứ không báo lỗi: giao diện có thể gửi tên cột của bảng
    mà file xuất không có. Nhưng rỗng hoặc không khoá nào hợp lệ thì trả về ĐỦ
    cột — thà xuất thừa còn hơn giao cho sale một file trống trơn.
    """
    if not khoa:
        return COLUMNS
    can = {k.strip() for k in khoa if k and k.strip()}
    ra = tuple(c for c in COLUMNS if c[0] in can)
    return ra or COLUMNS


def _cell(value: object) -> object:
    return "" if value is None else value


def rows_for(
    places: Iterable[Place], keywords: dict[int, list[str]], cols: tuple = COLUMNS
) -> Iterator[list]:
    for p in places:
        kw = keywords.get(p.id, [])
        yield [_cell(getter(p, kw)) for _k, _t, getter, _w in cols]


def export_xlsx(
    places: Iterable[Place], keywords: dict[int, list[str]], path: Path, cols: tuple = COLUMNS
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = xlsxwriter.Workbook(str(path), {"constant_memory": True, "default_date_format": "yyyy-mm-dd"})
    ws = wb.add_worksheet("Danh sách")
    header_fmt = wb.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#1F4E78", "border": 1})
    for col, (_k, title, _g, width) in enumerate(cols):
        ws.write(0, col, title, header_fmt)
        ws.set_column(col, col, width)
    ws.freeze_panes(1, 0)
    row_idx = 0
    for row_idx, row in enumerate(rows_for(places, keywords, cols), start=1):
        for col, value in enumerate(row):
            ws.write(row_idx, col, value)
    ws.autofilter(0, 0, max(row_idx, 1), len(cols) - 1)
    wb.close()
    return path


def export_csv(
    places: Iterable[Place], keywords: dict[int, list[str]], path: Path, cols: tuple = COLUMNS
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig để Excel trên Windows mở ra không lỗi font tiếng Việt.
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow([t for _k, t, _g, _w in cols])
        writer.writerows(rows_for(places, keywords, cols))
    return path


def export_json(
    places: Iterable[Place], keywords: dict[int, list[str]], path: Path, cols: tuple = COLUMNS
) -> Path:
    """JSON CỐ Ý xuất đủ trường, bỏ qua `cols`.

    Hai định dạng kia là để người đọc bằng mắt nên bớt cột là hợp lý. JSON là để
    máy đọc: nó giữ giá trị thô (toạ độ là số, mã quốc gia là mã) chứ không phải
    chuỗi hiển thị tiếng Việt, và một bên tiêu thụ nó mà thiếu trường thì hỏng
    ngay. Điều này ghi cả trong mô tả của endpoint để không ai bị bất ngờ.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        fh.write("[\n")
        first = True
        for p in places:
            if not first:
                fh.write(",\n")
            first = False
            fh.write(
                json.dumps(
                    {
                        "name": p.name,
                        "address": p.address,
                        "address_short": p.address_short,
                        "country_code": p.country_code,
                        "country_name": geo.country_name(p.country_code),
                        "country_source": p.country_source,
                        "phone_country_code": phone_country(p.phone_e164),
                        # Dạng QUỐC TẾ, giống hệt cột Excel/CSV. Dùng
                        # `phone_national` ở đây là tái tạo lại đúng cái lỗi mà cả
                        # module này sinh ra để chống: "081 939 8727" của Bangkok
                        # là đầu số Vinaphone khi đọc như số Việt Nam, và JSON
                        # không có cột quốc gia nào để phân biệt.
                        "phone": to_international(p.phone_e164, p.phone_national or p.phone_raw),
                        "phone_e164": p.phone_e164,
                        "website": p.website,
                        "liveness_label": p.liveness_label,
                        "liveness_score": p.liveness_score,
                        "liveness_reasons": list(p.liveness_reasons or []),
                        "category": p.category,
                        "rating": p.rating,
                        "review_count": p.review_count,
                        "lat": p.lat,
                        "lng": p.lng,
                        "keywords": keywords.get(p.id, []),
                        "maps_url": p.maps_url,
                    },
                    ensure_ascii=False,
                )
            )
        fh.write("\n]\n")
    return path


EXPORTERS = {"xlsx": export_xlsx, "csv": export_csv, "json": export_json}
MEDIA_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8",
    "json": "application/json",
}
