"""Sinh lại bộ dữ liệu địa giới dùng cho ô chọn địa điểm ở giao diện.

    python -m scripts.build_geo_data

Kết quả ghi đè `app/modules/geo/data/geo.json` — file này ĐƯỢC COMMIT vào repo,
nên ứng dụng lúc chạy KHÔNG cần mạng và không phụ thuộc dịch vụ ngoài nào.

Nguồn:
  * Châu lục, quốc gia, tên tiếng Việt : pycountry + pycountry_convert (offline)
  * Đơn vị hành chính cấp 1 toàn cầu   : ISO 3166-2 trong pycountry (offline)
  * Tỉnh/thành + phường/xã Việt Nam    : provinces.open-api.vn (CẦN MẠNG)

Vì sao Việt Nam phải lấy riêng: ISO 3166-2 trong pycountry vẫn là bản 63 tỉnh
CŨ. Từ 01/07/2025 Việt Nam còn 34 tỉnh/thành và bỏ hẳn cấp huyện, chỉ còn
tỉnh -> phường/xã. Dùng danh sách cũ để ghép truy vấn thì vừa tìm nhầm địa bàn
vừa bỏ sót tên mới. Chạy lại script này mỗi khi địa giới thay đổi.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pycountry
import pycountry_convert as pc

OUT = Path("app/modules/geo/data/geo.json")
VN_API = "https://provinces.open-api.vn/api/v2/?depth=2"

CONTINENT_VI = {
    "AS": "Châu Á",
    "EU": "Châu Âu",
    "AF": "Châu Phi",
    "NA": "Bắc Mỹ",
    "SA": "Nam Mỹ",
    "OC": "Châu Đại Dương",
    "AN": "Nam Cực",
}

# pycountry_convert không map được 8 mã này; tra tay một lần cho đủ.
CONTINENT_PATCH = {
    "AQ": "AN", "TF": "AN", "EH": "AF", "PN": "OC",
    "SX": "NA", "TL": "AS", "UM": "OC", "VA": "EU",
}


def country_name_vi() -> dict[str, str]:
    """Tên quốc gia tiếng Việt lấy từ bản dịch gettext đi kèm pycountry."""
    import gettext

    try:
        tr = gettext.translation("iso3166-1", pycountry.LOCALES_DIR, languages=["vi"])
    except OSError:
        return {}
    return {c.alpha_2: tr.gettext(c.name) for c in pycountry.countries}


def build_countries() -> list[dict]:
    vi = country_name_vi()
    out: list[dict] = []
    for c in pycountry.countries:
        code = c.alpha_2
        try:
            continent = pc.country_alpha2_to_continent_code(code)
        except Exception:  # noqa: BLE001 — thư viện ném KeyError/nhiều kiểu khác nhau
            continent = CONTINENT_PATCH.get(code)
        if not continent:
            continue
        out.append(
            {
                "code": code,
                "continent": continent,
                "name": vi.get(code) or c.name,
                "name_en": c.name,
                # Chuỗi thật sự ghép vào truy vấn Google Maps. Dùng tên tiếng Anh cho
                # nước ngoài vì Maps nhận diện tốt hơn tên đã dịch.
                "query": c.name,
                "levels": 1,
            }
        )
    return sorted(out, key=lambda x: x["name"])


def build_provinces() -> dict[str, list[dict]]:
    """Đơn vị hành chính cấp 1 theo ISO 3166-2, CHỈ lấy cấp cao nhất.

    pycountry trộn cả cấp con (có `parent_code`) vào cùng danh sách; lấy hết sẽ
    ra một danh sách phẳng lộn xộn hai ba cấp.
    """
    out: dict[str, list[dict]] = {}
    for sub in pycountry.subdivisions:
        if getattr(sub, "parent_code", None):
            continue
        out.setdefault(sub.country_code, []).append({"code": sub.code, "name": sub.name, "query": sub.name})
    for items in out.values():
        items.sort(key=lambda x: x["name"])
    return out


def fetch_vietnam() -> tuple[list[dict], dict[str, list[dict]]]:
    resp = httpx.get(VN_API, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    provinces: list[dict] = []
    wards: dict[str, list[dict]] = {}
    for p in data:
        pcode = f"VN-{p['code']}"
        provinces.append({"code": pcode, "name": p["name"], "query": p["name"]})
        items = [
            {"code": f"{pcode}-{w['code']}", "name": w["name"], "query": w["name"]}
            for w in (p.get("wards") or [])
        ]
        items.sort(key=lambda x: x["name"])
        if items:
            wards[pcode] = items
    provinces.sort(key=lambda x: x["name"])
    return provinces, wards


def main() -> int:
    countries = build_countries()
    provinces = build_provinces()

    print("Đang tải địa giới Việt Nam hiện hành...")
    vn_provinces, vn_wards = fetch_vietnam()
    if len(vn_provinces) < 30:
        raise SystemExit(f"Dữ liệu Việt Nam bất thường: chỉ có {len(vn_provinces)} tỉnh")
    provinces["VN"] = vn_provinces   # thay hẳn bản 63 tỉnh cũ của ISO

    for c in countries:
        if c["code"] == "VN":
            c["levels"] = 3          # tỉnh -> phường/xã
            c["query"] = "Việt Nam"
        elif provinces.get(c["code"]):
            c["levels"] = 2          # chỉ tới cấp 1

    payload = {
        "generated_at": date.today().isoformat(),
        "sources": {
            "countries": "pycountry (ISO 3166-1) + pycountry_convert",
            "provinces": "pycountry (ISO 3166-2)",
            "vietnam": VN_API,
        },
        "continents": [{"code": k, "name": v} for k, v in CONTINENT_VI.items()],
        "countries": countries,
        "provinces": provinces,
        "wards": vn_wards,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(
        f"Đã ghi {OUT} — {len(countries)} quốc gia, "
        f"{sum(len(v) for v in provinces.values())} đơn vị cấp 1, "
        f"{sum(len(v) for v in vn_wards.values())} phường/xã Việt Nam, "
        f"{OUT.stat().st_size // 1024} KB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
