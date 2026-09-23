"""Suy quốc gia từ toạ độ, và chuỗi ưu tiên khi chốt quốc gia của một địa điểm.

Chạy offline hoàn toàn: dữ liệu biên giới là file đã commit, không gọi mạng.
"""
from __future__ import annotations

import pytest

from app.modules.geo import borders
from app.modules.scraper.engine.normalize import phone_country
from app.modules.scraper.engine.parsers import normalize_ws
from app.modules.scraper.place.writer import DO_TIN_NGUON, resolve_place_country


# ---------- toạ độ -> quốc gia ----------
@pytest.mark.parametrize(
    ("ten", "lat", "lng", "mong"),
    [
        ("TP.HCM", 10.7769, 106.7009, "VN"),
        ("Hà Nội", 21.0278, 105.8342, "VN"),
        ("Bangkok", 13.7563, 100.5018, "TH"),
        ("Chiang Mai", 18.7943, 98.9976, "TH"),
        ("Tokyo", 35.6762, 139.6503, "JP"),
        ("Seoul", 37.5665, 126.9780, "KR"),
        ("Dubai", 25.2048, 55.2708, "AE"),
        ("Phnom Penh", 11.5564, 104.9282, "KH"),
        ("Viêng Chăn", 17.9757, 102.6331, "LA"),
        ("Đài Bắc", 25.0330, 121.5654, "TW"),
    ],
)
def test_toa_do_trong_dat_lien(ten, lat, lng, mong):
    assert borders.country_at(lat, lng) == mong


@pytest.mark.parametrize(
    ("ten", "lat", "lng", "mong"),
    [
        # Bản đồ 1:50m vẽ bờ biển rất thô, nên những điểm này nằm NGOÀI đa giác
        # đất liền và chỉ ra đúng nhờ lớp bù "bờ gần nhất trong ngưỡng".
        # Đây không phải ca hiếm: doanh nghiệp xuất nhập khẩu trái cây nằm ở CẢNG.
        ("Manhattan", 40.7128, -74.0060, "US"),
        ("Singapore", 1.3521, 103.8198, "SG"),
        ("Hong Kong", 22.3193, 114.1694, "HK"),
        ("Cảng Rotterdam", 51.9244, 4.4777, "NL"),
        ("Cảng Hải Phòng", 20.8449, 106.6881, "VN"),
        ("Phuket", 7.8804, 98.3923, "TH"),
    ],
)
def test_diem_sat_bo_bien_van_ra_dung_nuoc(ten, lat, lng, mong):
    assert borders.country_at(lat, lng) == mong


@pytest.mark.parametrize(
    ("lat", "lng"),
    [
        (15.0, 113.0),      # giữa Biển Đông, cách bờ hàng trăm km
        (0.0, -30.0),       # giữa Đại Tây Dương
        (None, None),
        (10.0, None),
        (None, 106.0),
        (999.0, 999.0),     # toạ độ vô lý
    ],
)
def test_ngoai_khoi_va_toa_do_hong_thi_tra_none(lat, lng):
    """CỐ Ý không đoán nước gần nhất ở đây. Một địa điểm giữa đại dương là dấu
    hiệu dữ liệu hỏng; đoán bừa chỉ tạo ra giá trị sai trông như thật."""
    assert borders.country_at(lat, lng) is None


# ---------- chuỗi ưu tiên nguồn ----------
def test_dia_chi_manh_hon_toa_do_manh_hon_gl():
    assert DO_TIN_NGUON["address"] > DO_TIN_NGUON["coords"] > DO_TIN_NGUON["gl"]


def test_toa_do_cuu_duoc_dia_chi_rut_gon():
    """Đúng 6 dòng thật đã đo được: địa chỉ từ thẻ kết quả không có tên nước nên
    trước đây phải rơi về `gl`. Toạ độ thì luôn có."""
    ma, nguon = resolve_place_country("1366", None, None, "TH", 18.8186006, 98.9752576)
    assert (ma, nguon) == ("TH", "coords")


def test_toa_do_thang_gl_o_vung_giap_bien():
    """Tìm ở Việt Nam nhưng Google trả về một địa điểm bên kia biên giới.

    `gl` sẽ nói VN — và địa điểm đó sẽ bị đọc số điện thoại theo vùng VN. Toạ độ
    là thứ duy nhất phát hiện ra được.
    """
    ma, nguon = resolve_place_country("Bavet Market", None, None, "VN", 11.6959, 105.6739)
    assert (ma, nguon) == ("KH", "coords")


def test_dia_chi_day_du_thang_tat_ca():
    ma, nguon = resolve_place_country(
        "1 Sukhumvit, Bangkok 10110, Thailand", "VN", "gl", "VN", 10.77, 106.70
    )
    assert (ma, nguon) == ("TH", "address")


def test_gia_tri_cu_manh_hon_khong_bi_gl_ghi_de():
    """Một truy vấn sau ở nước khác không được hạ cấp kết luận đã có."""
    ma, nguon = resolve_place_country(None, "TH", "address", "VN", None, None)
    assert (ma, nguon) == ("TH", "address")


def test_gia_tri_cu_yeu_hon_thi_duoc_nang_cap():
    """Bản ghi cũ chỉ đoán theo `gl`; lần quét sau có toạ độ thì phải sửa lại."""
    ma, nguon = resolve_place_country(None, "VN", "gl", "VN", 13.7563, 100.5018)
    assert (ma, nguon) == ("TH", "coords")


def test_khong_co_manh_moi_nao_thi_de_trong():
    assert resolve_place_country(None, None, None, None, None, None) == (None, None)


# ---------- ký tự icon font lẫn vào địa chỉ ----------
@pytest.mark.parametrize(
    ("tho", "sach"),
    [
        #  là glyph của bộ icon font Google nhúng thẳng vào text của thẻ.
        # Không lọc thì địa chỉ hiện ra thành ", 201" — đã gặp thật.
        (", 201", "201"),
        (", 98, 9 Chiang Mai Soi 3", "98, 9 Chiang Mai Soi 3"),
        ("1366", "1366"),
        ("90 Wichayanon Rd", "90 Wichayanon Rd"),
        ("Quận 1, TP.HCM", "Quận 1, TP.HCM"),
    ],
)
def test_bo_ky_tu_icon_font_khoi_dia_chi(tho, sach):
    assert normalize_ws(tho) == sach


# ---------- đối chiếu số điện thoại với quốc gia ----------
def test_doc_nguoc_quoc_gia_tu_so_dien_thoai():
    assert phone_country("+66818821104") == "TH"
    assert phone_country("+84938655504") == "VN"
    assert phone_country(None) is None
    assert phone_country("khong-phai-so") is None
