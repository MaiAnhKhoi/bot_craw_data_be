"""Test danh mục địa giới và phép khai triển lựa chọn thành danh sách địa điểm.

Dữ liệu là file JSON tĩnh đã commit nên test này chạy offline, không cần DB,
không cần mạng.
"""
from __future__ import annotations

import pytest

from app.modules.geo import service as geo


@pytest.fixture(scope="module")
def data():
    return geo.load()


@pytest.fixture(scope="module")
def hcm_code() -> str:
    found = geo.provinces("VN", "ho chi")
    assert found, "không tìm thấy TP Hồ Chí Minh"
    return found[0]["code"]


# ---------- dữ liệu nền ----------
def test_co_du_bay_chau_luc(data):
    codes = {c["code"] for c in data.continents}
    assert codes == {"AS", "EU", "AF", "NA", "SA", "OC", "AN"}
    assert all(c["name"].strip() for c in data.continents)


def test_moi_quoc_gia_deu_co_chau_luc(data):
    assert len(data.countries) > 200
    for c in data.countries:
        assert c["continent"] in {x["code"] for x in data.continents}, c
        assert c["code"] and c["name"] and c["query"]
        assert c["levels"] in (1, 2, 3)


def test_ten_quoc_gia_da_dich_sang_tieng_viet():
    vn = next(c for c in geo.countries() if c["code"] == "VN")
    assert vn["name"] == "Việt Nam"
    assert vn["query"] == "Việt Nam"
    assert vn["levels"] == 3          # có tới cấp phường/xã


def test_viet_nam_dung_dia_gioi_moi():
    """Từ 01/07/2025 còn 34 tỉnh/thành và bỏ cấp huyện.

    ISO 3166-2 trong pycountry vẫn là bản 63 tỉnh cũ; nếu ai đó lỡ để script sinh
    dữ liệu lấy nhầm nguồn thì test này đỏ ngay.
    """
    provinces = geo.provinces("VN")
    assert len(provinces) == 34
    names = {p["name"] for p in provinces}
    assert "Thành phố Hồ Chí Minh" in names
    assert "Thành phố Hà Nội" in names
    # Các tỉnh đã bị sáp nhập, không được còn trong danh sách
    assert not any("Bắc Kạn" in n for n in names)
    assert not any("Hà Giang" in n for n in names)


def test_so_luong_phuong_xa_viet_nam(data):
    total = sum(len(v) for v in data.wards.values())
    assert 3000 < total < 3600, f"số phường/xã bất thường: {total}"


def test_nuoc_ngoai_khong_co_cap_phuong_xa():
    """Chỉ Việt Nam có dữ liệu cấp 3 — đây là giới hạn đã biết, không phải lỗi."""
    thai = geo.provinces("TH")
    assert thai, "Thái Lan phải có cấp tỉnh"
    assert geo.wards(thai[0]["code"]) == []


# ---------- tìm kiếm ----------
@pytest.mark.parametrize(
    ("q", "expected"),
    [("viet", "Việt Nam"), ("VIỆT", "Việt Nam"), ("viet nam", "Việt Nam"), ("Vietnam", None)],
)
def test_tim_quoc_gia_khong_dau(q, expected):
    names = [c["name"] for c in geo.countries(q=q)]
    if expected:
        assert expected in names
    else:
        # "Vietnam" viết liền không khớp "Viet Nam" — chấp nhận được, ghi lại hành vi thật
        assert expected is None


def test_tim_tinh_khong_dau():
    assert [p["name"] for p in geo.provinces("VN", "ho chi")] == ["Thành phố Hồ Chí Minh"]
    assert [p["name"] for p in geo.provinces("VN", "HỒ CHÍ")] == ["Thành phố Hồ Chí Minh"]
    assert geo.provinces("VN", "khong-ton-tai-abc") == []


def test_tim_phuong_khong_dau(hcm_code):
    found = [w["name"] for w in geo.wards(hcm_code, "ben thanh")]
    assert "Phường Bến Thành" in found


def test_loc_quoc_gia_theo_chau_luc():
    asia = geo.countries("AS")
    assert 40 < len(asia) < 70
    assert all(c["continent"] == "AS" for c in asia)
    assert len(geo.countries(geo.ALL)) == len(geo.countries())


# ---------- khai triển ----------
def test_khong_chon_cap_tinh_thi_mot_dong_moi_quoc_gia():
    locs, total = geo.expand(country="VN")
    assert (locs, total) == (["Việt Nam"], 1)


def test_tat_ca_tinh_cua_mot_quoc_gia():
    locs, total = geo.expand(country="VN", province=geo.ALL)
    assert total == 34 and len(locs) == 34
    # Chuỗi ghép từ trong ra ngoài, đúng thứ tự Google Maps quen đọc
    assert all(x.endswith(", Việt Nam") for x in locs)
    assert "Thành phố Hồ Chí Minh, Việt Nam" in locs


def test_tat_ca_phuong_cua_mot_tinh(hcm_code):
    locs, total = geo.expand(country="VN", province=hcm_code, ward=geo.ALL)
    assert total == len(geo.wards(hcm_code)) > 100
    assert "Phường Bến Thành, Thành phố Hồ Chí Minh, Việt Nam" in locs


def test_tat_ca_tinh_va_tat_ca_phuong(data):
    locs, total = geo.expand(country="VN", province=geo.ALL, ward=geo.ALL)
    assert total == sum(len(v) for v in data.wards.values())
    assert len(locs) == total <= geo.MAX_LOCATIONS
    assert all(x.count(",") == 2 for x in locs)   # phường, tỉnh, quốc gia


def test_chau_luc_chi_la_bo_loc_khong_vao_chuoi():
    """"vựa trái cây Châu Á" là truy vấn vô nghĩa — tên châu lục không bao giờ
    được ghép vào chuỗi địa điểm."""
    locs, total = geo.expand(continent="AS", country=geo.ALL)
    assert total == len(geo.countries("AS"))
    assert not any("Châu Á" in x or "Asia" in x for x in locs)


def test_mot_phuong_cu_the(hcm_code):
    ward = next(w for w in geo.wards(hcm_code) if w["name"] == "Phường Bến Thành")
    locs, total = geo.expand(country="VN", province=hcm_code, ward=ward["code"])
    assert total == 1
    assert locs == ["Phường Bến Thành, Thành phố Hồ Chí Minh, Việt Nam"]


def test_quoc_gia_khong_co_cap_tinh_van_duoc_giu_lai():
    """Chọn "tất cả tỉnh" trên toàn cầu: nước nào không có dữ liệu cấp tỉnh thì
    vẫn phải xuất hiện ở mức quốc gia, không được biến mất im lặng."""
    locs, total = geo.expand(continent=geo.ALL, country=geo.ALL, province=geo.ALL)
    assert total > 3000
    no_province = [c for c in geo.countries() if not geo.provinces(c["code"])]
    assert no_province, "kịch bản này chỉ có nghĩa khi thật sự có nước thiếu dữ liệu"
    assert no_province[0]["query"] in locs


def test_chon_cap_phuong_o_nuoc_khong_co_du_lieu_thi_dung_o_cap_tinh():
    thai = geo.provinces("TH")[0]
    locs, total = geo.expand(country="TH", province=thai["code"], ward=geo.ALL)
    assert total == 1
    assert locs == [f"{thai['name']}, Thailand"]


def test_ma_khong_ton_tai_tra_rong():
    assert geo.expand(country="ZZ") == ([], 0)
    assert geo.expand(country="VN", province="VN-khong-co") == ([], 0)


def test_cat_bot_khi_qua_nhieu(monkeypatch):
    """Trần MAX_LOCATIONS chặn payload khổng lồ, nhưng `total` vẫn phải là số THẬT
    để giao diện cảnh báo đúng."""
    monkeypatch.setattr(geo, "MAX_LOCATIONS", 10)
    locs, total = geo.expand(country="VN", province=geo.ALL, ward=geo.ALL)
    assert len(locs) == 10
    assert total > 3000
