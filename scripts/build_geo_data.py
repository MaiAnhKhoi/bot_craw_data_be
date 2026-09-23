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


# Tên hiển thị + chuỗi truy vấn ghi đè.
#
# Hai lý do phải có bảng này:
#  1. Tên ISO 3166-1 là tên NGOẠI GIAO, không phải thứ người ta gõ vào Google Maps:
#     "Korea, Republic of", "Taiwan, Province of China", "Lao People's Democratic
#     Republic". Ghép vào truy vấn cho ra chuỗi lạ và kết quả kém.
#  2. Tên có DẤU PHẨY làm vỡ việc nhận diện quốc gia từ đuôi chuỗi địa điểm —
#     15 nước dính, trong đó có Hàn Quốc và Đài Loan.
#
# Bản dịch tiếng Việt tự động của pycountry cũng phiên âm rất lạ với nhóm này
# ("Bắc Hàn, Cộng hoà Nhân dân Dân chủ", "Phi-li-pi-nợ"), nên sửa luôn tên hiển thị
# cho các thị trường hay dùng.
NAME_OVERRIDE: dict[str, tuple[str, str]] = {
    # mã: (tên hiển thị tiếng Việt, chuỗi dùng trong truy vấn Google Maps)
    "KR": ("Hàn Quốc", "South Korea"),
    "KP": ("Triều Tiên", "North Korea"),
    "TW": ("Đài Loan", "Taiwan"),
    "IR": ("Iran", "Iran"),
    "BO": ("Bolivia", "Bolivia"),
    "VE": ("Venezuela", "Venezuela"),
    "MD": ("Moldova", "Moldova"),
    "TZ": ("Tanzania", "Tanzania"),
    "PS": ("Palestine", "Palestine"),
    "CD": ("CHDC Congo", "DR Congo"),
    "CG": ("Congo", "Republic of the Congo"),
    "FM": ("Micronesia", "Micronesia"),
    "VG": ("Quần đảo Virgin thuộc Anh", "British Virgin Islands"),
    "VI": ("Quần đảo Virgin thuộc Mỹ", "U.S. Virgin Islands"),
    "SH": ("Saint Helena", "Saint Helena"),
    "BQ": ("Bonaire", "Bonaire"),
    "SY": ("Syria", "Syria"),
    "MK": ("Bắc Macedonia", "North Macedonia"),
    # Thị trường hay dùng — tên tự động đọc rất lạ
    "LA": ("Lào", "Laos"),
    "RU": ("Nga", "Russia"),
    "JP": ("Nhật Bản", "Japan"),
    "PH": ("Philippines", "Philippines"),
    "ID": ("Indonesia", "Indonesia"),
    "MY": ("Malaysia", "Malaysia"),
    "SG": ("Singapore", "Singapore"),
    "KH": ("Campuchia", "Cambodia"),
    "MM": ("Myanmar", "Myanmar"),
    "NL": ("Hà Lan", "Netherlands"),
    "GB": ("Anh", "United Kingdom"),
    "AE": ("UAE", "United Arab Emirates"),
    "IN": ("Ấn Độ", "India"),
    "VN": ("Việt Nam", "Việt Nam"),
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
        display, query = NAME_OVERRIDE.get(code, (vi.get(code) or c.name, c.name))
        out.append(
            {
                "code": code,
                "continent": continent,
                "name": display,
                "name_en": c.name,
                # Chuỗi thật sự ghép vào truy vấn Google Maps. Dùng tên tiếng Anh cho
                # nước ngoài vì Maps nhận diện tốt hơn tên đã dịch.
                "query": query,
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

    # Dấu phẩy trong chuỗi truy vấn làm vỡ việc nhận diện quốc gia từ đuôi chuỗi
    # địa điểm. Thà dừng ở đây còn hơn để lọt ra rồi âm thầm quét nhầm nước.
    con_dau_phay = [c["code"] for c in countries if "," in c["query"]]
    if con_dau_phay:
        raise SystemExit(f"Còn {len(con_dau_phay)} quốc gia có dấu phẩy trong `query`: {con_dau_phay}")

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
