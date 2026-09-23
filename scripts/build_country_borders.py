"""Sinh bộ dữ liệu biên giới quốc gia dùng để suy quốc gia từ TOẠ ĐỘ.

    python -m scripts.build_country_borders

Kết quả ghi đè `app/modules/geo/data/borders.json` — file này ĐƯỢC COMMIT, y như
`geo.json`, nên lúc chạy không cần mạng và không phụ thuộc dịch vụ ngoài nào.

VÌ SAO CẦN: quốc gia của một địa điểm đang được suy theo thứ tự
`địa chỉ -> giá trị đã lưu -> gl của truy vấn`. Đo trên dữ liệu thật: 23% số dòng
có địa chỉ RÚT GỌN từ thẻ kết quả ("90 Wichayanon Rd", "1366") nên không đọc ra
tên nước, phải rơi về `gl`. Mà `gl` chỉ nói ta đã TÌM ở nước nào, không nói địa
điểm NẰM ở nước nào. Trong khi 100% số dòng đều có toạ độ — Google cho sẵn trong
URL của mọi thẻ. Toạ độ không phụ thuộc ngôn ngữ, không phụ thuộc Google viết địa
chỉ đầy đủ hay rút gọn.

Hệ quả nếu đoán sai quốc gia: số điện thoại NỘI ĐỊA bị đọc sai vùng. "081 882 1104"
đọc theo TH ra +66818821104, đọc theo VN ra +84818821104 — CẢ HAI đều hợp lệ, nên
không bộ kiểm tra nào bắt được, và số sai nằm im trong file xuất cho sale gọi.

Nguồn: Natural Earth 1:50m admin-0 (public domain), qua kho nvkelso/natural-earth-vector.
Độ phân giải đó cho sai số biên giới cỡ 1-2 km — đủ cho mọi ca trừ cửa hàng nằm
sát biên. Những ca đó được `country_source` đánh dấu là suy từ toạ độ để người
dùng soi lại, thay vì tin mù quáng.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx

OUT = Path("app/modules/geo/data/borders.json")
SRC = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/"
    "geojson/ne_50m_admin_0_countries.geojson"
)

# 3 chữ số thập phân ~ 110 m. Giữ nhiều hơn là vô nghĩa: bản đồ 1:50m vốn đã sai
# số cỡ 1-2 km, mà mỗi chữ số thêm vào lại phình file lên hàng trăm KB.
NGHIN_PHAN = 3


def lay_ma(props: dict) -> str | None:
    """Mã ISO alpha-2 của một vùng lãnh thổ.

    Natural Earth để `ISO_A2 = "-99"` cho các vùng đang tranh chấp hoặc chưa được
    công nhận rộng rãi (Kosovo, Bắc Síp, Somaliland...). `ISO_A2_EH` là cột vá sẵn
    cho đúng nhóm đó, nên thử nó trước khi bỏ vùng lãnh thổ đi — bỏ sót một vùng
    nghĩa là mọi địa điểm trong đó rơi về `gl`.
    """
    for khoa in ("ISO_A2_EH", "ISO_A2", "WB_A2"):
        ma = (props.get(khoa) or "").strip().upper()
        if len(ma) == 2 and ma.isalpha():
            return ma
    return None


def gon_vong(vong: list) -> list[list[float]]:
    """Làm tròn toạ độ và bỏ các điểm trùng nhau sau khi làm tròn."""
    out: list[list[float]] = []
    for diem in vong:
        x, y = round(float(diem[0]), NGHIN_PHAN), round(float(diem[1]), NGHIN_PHAN)
        if not out or out[-1] != [x, y]:
            out.append([x, y])
    return out


def lay_cac_vong(geom: dict) -> list[list[list[float]]]:
    """Chỉ lấy VÒNG NGOÀI của mỗi đa giác.

    Cố ý bỏ lỗ (interior ring): lỗ trong biên giới quốc gia là các vùng bao
    (enclave) như Vatican trong Ý hay Lesotho trong Nam Phi. Giữ lỗ thì một điểm
    ở Vatican sẽ không thuộc nước nào; bỏ lỗ thì nó ra "Ý" — sai, nhưng sai ít hơn
    hẳn so với không có gì. Và các enclave đó đều có mục riêng nên được thử TRƯỚC
    (xem `borders.country_at`: nước có diện tích hộp bao nhỏ hơn được xét trước).
    """
    loai = geom.get("type")
    if loai == "Polygon":
        da_giac = [geom["coordinates"]]
    elif loai == "MultiPolygon":
        da_giac = geom["coordinates"]
    else:
        return []
    out = []
    for poly in da_giac:
        if not poly:
            continue
        vong = gon_vong(poly[0])
        if len(vong) >= 4:
            out.append(vong)
    return out


def hop_bao(vongs: list[list[list[float]]]) -> list[float]:
    xs = [p[0] for v in vongs for p in v]
    ys = [p[1] for v in vongs for p in v]
    return [min(xs), min(ys), max(xs), max(ys)]


def main() -> int:
    print("Đang tải biên giới quốc gia từ Natural Earth...")
    resp = httpx.get(SRC, timeout=180, follow_redirects=True)
    resp.raise_for_status()
    data = resp.json()

    theo_ma: dict[str, list] = {}
    bo_qua = 0
    for feat in data.get("features", []):
        ma = lay_ma(feat.get("properties") or {})
        if not ma:
            bo_qua += 1
            continue
        vongs = lay_cac_vong(feat.get("geometry") or {})
        if vongs:
            theo_ma.setdefault(ma, []).extend(vongs)

    countries = []
    for ma, vongs in sorted(theo_ma.items()):
        bbox = hop_bao(vongs)
        countries.append(
            {
                "code": ma,
                "bbox": bbox,
                # Diện tích hộp bao — dùng để xét nước NHỎ trước. Quan trọng với
                # enclave và với các nước nằm lọt trong hộp bao của nước lớn hơn.
                "area": round((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]), 4),
                "rings": vongs,
            }
        )
    countries.sort(key=lambda c: c["area"])

    if len(countries) < 200:
        raise SystemExit(f"Dữ liệu bất thường: chỉ có {len(countries)} quốc gia")

    payload = {
        "generated_at": date.today().isoformat(),
        "source": SRC,
        "precision": NGHIN_PHAN,
        "countries": countries,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    so_diem = sum(len(v) for c in countries for v in c["rings"])
    print(
        f"Đã ghi {OUT} — {len(countries)} quốc gia, "
        f"{sum(len(c['rings']) for c in countries)} đa giác, "
        f"{so_diem} điểm, {OUT.stat().st_size // 1024} KB "
        f"(bỏ qua {bo_qua} vùng không có mã ISO alpha-2)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
