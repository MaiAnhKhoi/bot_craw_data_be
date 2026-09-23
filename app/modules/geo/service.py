"""Danh mục địa giới hành chính phục vụ ô chọn địa điểm.

Dữ liệu nằm trong một file JSON ĐƯỢC COMMIT (`data/geo.json`), nạp một lần vào bộ
nhớ lúc import. Không bảng DB, không migration, không gọi mạng lúc chạy: đây là dữ
liệu tham chiếu tĩnh, mỗi năm đổi một lần. Đổi thì chạy
`python -m scripts.build_geo_data` rồi commit file mới.

Phạm vi dữ liệu — nói rõ để không ai kỳ vọng nhầm:
  * Châu lục + quốc gia  : đủ toàn thế giới (249 quốc gia/vùng lãnh thổ).
  * Cấp tỉnh/bang        : đủ toàn thế giới theo ISO 3166-2 (~3.500 đơn vị).
  * Cấp phường/xã        : CHỈ Việt Nam (3.321 đơn vị, theo địa giới từ 01/07/2025).
    Không có nguồn miễn phí nào phủ cấp này cho cả thế giới ở kích thước nhúng được
    vào repo; nước khác sẽ dừng ở cấp tỉnh.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.core.text import fold_text

DATA_FILE = Path(__file__).parent / "data" / "geo.json"

ALL = "ALL"          # mã đặc biệt: "tách ra từng mục ở cấp này"
MAX_LOCATIONS = 5000  # trần số dòng trả về một lần, chặn payload khổng lồ


@dataclass(frozen=True)
class GeoData:
    continents: list[dict]
    countries: list[dict]
    provinces: dict[str, list[dict]]
    wards: dict[str, list[dict]]
    generated_at: str

    def country(self, code: str) -> dict | None:
        return next((c for c in self.countries if c["code"] == code), None)


@lru_cache(maxsize=1)
def load() -> GeoData:
    raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return GeoData(
        continents=raw["continents"],
        countries=raw["countries"],
        provinces=raw["provinces"],
        wards=raw["wards"],
        generated_at=raw.get("generated_at", ""),
    )


def _filter(items: list[dict], q: str | None, limit: int | None = None) -> list[dict]:
    """Lọc theo chuỗi tìm kiếm không dấu; giữ nguyên thứ tự đã sắp sẵn."""
    if q:
        needle = fold_text(q)
        items = [
            i for i in items
            if needle in fold_text(i["name"]) or needle in fold_text(i.get("name_en", ""))
        ]
    return items[:limit] if limit else items


def continents() -> list[dict]:
    return load().continents


def countries(continent: str | None = None, q: str | None = None) -> list[dict]:
    items = load().countries
    if continent and continent != ALL:
        items = [c for c in items if c["continent"] == continent]
    return _filter(items, q)


def provinces(country: str, q: str | None = None) -> list[dict]:
    return _filter(load().provinces.get(country, []), q)


def wards(province: str, q: str | None = None) -> list[dict]:
    return _filter(load().wards.get(province, []), q)


def expand(
    continent: str | None = None,
    country: str | None = None,
    province: str | None = None,
    ward: str | None = None,
) -> tuple[list[str], int]:
    """Biến lựa chọn trên giao diện thành danh sách chuỗi địa điểm cụ thể.

    Quy ước: `None` = dừng ở cấp trên, `ALL` = tách ra từng mục ở cấp này, còn lại
    là một mã cụ thể. Chuỗi ghép từ trong ra ngoài, ví dụ
    "Phường Bến Thành, Thành phố Hồ Chí Minh, Việt Nam" — đúng thứ tự Google Maps quen đọc.

    Trả (danh sách đã cắt theo trần, tổng số thật). Tổng số thật dùng để cảnh báo
    người dùng trước khi họ tạo ra hàng nghìn truy vấn.
    """
    data = load()

    pool = data.countries
    if continent and continent != ALL:
        pool = [c for c in pool if c["continent"] == continent]
    if country and country != ALL:
        pool = [c for c in pool if c["code"] == country]
    if not pool:
        return [], 0

    # Không chọn tới cấp tỉnh: mỗi quốc gia một dòng.
    if not province:
        out = [c["query"] for c in pool]
        return out[:MAX_LOCATIONS], len(out)

    out: list[str] = []
    total = 0
    for c in pool:
        items = data.provinces.get(c["code"], [])
        if province != ALL:
            items = [p for p in items if p["code"] == province]
        if not items:
            if province != ALL:
                # Người dùng chỉ đích danh một mã tỉnh mà không khớp -> không trả gì.
                # Lùi về mức quốc gia ở đây là âm thầm đưa dữ liệu KHÁC thứ họ chọn.
                continue
            # Còn với "tất cả": quốc gia thiếu dữ liệu cấp tỉnh vẫn phải được giữ ở
            # mức quốc gia, chọn "phủ hết" mà mất nguyên một nước thì mới là sai.
            total += 1
            if len(out) < MAX_LOCATIONS:
                out.append(c["query"])
            continue

        for p in items:
            if not ward:
                total += 1
                if len(out) < MAX_LOCATIONS:
                    out.append(f"{p['query']}, {c['query']}")
                continue

            ward_items = data.wards.get(p["code"], [])
            if ward != ALL:
                ward_items = [w for w in ward_items if w["code"] == ward]
            if not ward_items:
                if ward != ALL:
                    continue     # mã phường cụ thể không khớp -> bỏ qua, cùng lý do ở trên
                # "Tất cả" mà tỉnh này không có dữ liệu cấp phường: dừng ở cấp tỉnh.
                total += 1
                if len(out) < MAX_LOCATIONS:
                    out.append(f"{p['query']}, {c['query']}")
                continue
            for w in ward_items:
                total += 1
                if len(out) < MAX_LOCATIONS:
                    out.append(f"{w['query']}, {p['query']}, {c['query']}")
    return out, total
