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
WEBSITE_STATUS_VI = {
    "OK": "Còn hoạt động",
    "DEAD": "Không truy cập được",
    "PARKED": "Trang đỗ tên miền",
    "UNCHECKED": "Chưa kiểm tra",
    "NONE": "Không có",
}

# (tiêu đề cột, hàm lấy giá trị, độ rộng)
COLUMNS: tuple[tuple[str, Callable[[Place, list[str]], object], int], ...] = (
    ("Tên công ty", lambda p, k: p.name, 42),
    ("Vị trí", lambda p, k: p.address, 50),
    ("Số điện thoại", lambda p, k: p.phone_national or p.phone_raw, 18),
    ("Website", lambda p, k: p.website, 34),
    ("Tình trạng", lambda p, k: LIVENESS_VI.get(p.liveness_label, p.liveness_label), 22),
    ("Điểm tình trạng", lambda p, k: p.liveness_score, 15),
    ("Lý do nghi ngờ", lambda p, k: "; ".join(REASON_VI.get(r, r) for r in (p.liveness_reasons or [])), 46),
    ("Ngành nghề", lambda p, k: p.category, 26),
    ("Điểm đánh giá", lambda p, k: p.rating, 13),
    ("Số đánh giá", lambda p, k: p.review_count, 12),
    ("Đánh giá mới nhất (ngày)", lambda p, k: p.latest_review_days, 20),
    ("Tình trạng website", lambda p, k: WEBSITE_STATUS_VI.get(p.website_status, p.website_status), 18),
    ("SĐT chuẩn E.164", lambda p, k: p.phone_e164, 18),
    ("Vĩ độ", lambda p, k: p.lat, 11),
    ("Kinh độ", lambda p, k: p.lng, 11),
    ("Từ khoá tìm ra", lambda p, k: " | ".join(k), 34),
    ("Link Google Maps", lambda p, k: p.maps_url, 40),
    ("Thời điểm quét", lambda p, k: p.scraped_at.isoformat() if p.scraped_at else None, 22),
)

HEADERS = [c[0] for c in COLUMNS]


def _cell(value: object) -> object:
    return "" if value is None else value


def rows_for(places: Iterable[Place], keywords: dict[int, list[str]]) -> Iterator[list]:
    for p in places:
        kw = keywords.get(p.id, [])
        yield [_cell(getter(p, kw)) for _, getter, _w in COLUMNS]


def export_xlsx(places: Iterable[Place], keywords: dict[int, list[str]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = xlsxwriter.Workbook(str(path), {"constant_memory": True, "default_date_format": "yyyy-mm-dd"})
    ws = wb.add_worksheet("Danh sách")
    header_fmt = wb.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#1F4E78", "border": 1})
    for col, (title, _g, width) in enumerate(COLUMNS):
        ws.write(0, col, title, header_fmt)
        ws.set_column(col, col, width)
    ws.freeze_panes(1, 0)
    row_idx = 0
    for row_idx, row in enumerate(rows_for(places, keywords), start=1):
        for col, value in enumerate(row):
            ws.write(row_idx, col, value)
    ws.autofilter(0, 0, max(row_idx, 1), len(COLUMNS) - 1)
    wb.close()
    return path


def export_csv(places: Iterable[Place], keywords: dict[int, list[str]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig để Excel trên Windows mở ra không lỗi font tiếng Việt.
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(HEADERS)
        writer.writerows(rows_for(places, keywords))
    return path


def export_json(places: Iterable[Place], keywords: dict[int, list[str]], path: Path) -> Path:
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
                        "phone": p.phone_national or p.phone_raw,
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
