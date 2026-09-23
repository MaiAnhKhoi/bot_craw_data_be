"""Quốc gia của địa điểm, vùng đọc số điện thoại, và lý do dừng của truy vấn.

Chạy offline hoàn toàn: không DB, không mạng, không trình duyệt.
"""
from __future__ import annotations

import pytest

from app.modules.geo import service as geo
from app.modules.scraper.engine.models import (
    STOP_CAP,
    STOP_CUT_OFF,
    STOP_EMPTY,
    STOP_EXHAUSTED,
    STOP_UNKNOWN,
    CardResult,
    SearchOutcome,
)
from app.modules.scraper.engine.normalize import normalize_phone, to_international
from app.modules.scraper.engine.parsers import is_phone_segment, parse_card
from app.modules.scraper.job.repository import CO_THE_BO_QUA
from app.modules.scraper.place.entity import Place
from app.modules.scraper.place.writer import (
    country_of_address,
    region_of,
    resolve_place_country,
)


# ---------- nhận quốc gia từ đuôi địa chỉ ----------
@pytest.mark.parametrize(
    ("address", "expected"),
    [
        # Địa chỉ thật Google trả về cho các địa điểm ở Bangkok
        ("325 169-170 Lan Luang 9 Alley, Khwaeng Si Yaek Mahanak, Bangkok 10300, Thailand", "TH"),
        ("1112, 2 Sukhumvit Rd, Prakanong, Khlong Toei, Bangkok 10110, Thailand", "TH"),
        ("12 Nguyễn Huệ, Phường Bến Nghé, Thành phố Hồ Chí Minh, Việt Nam", "VN"),
        ("1-1 Chiyoda, Chiyoda City, Tokyo 100-8111, Japan", "JP"),
        # Tên ISO nhiều đoạn — đúng nhóm 15 nước từng âm thầm rơi về Việt Nam
        ("123 Sejong-daero, Jung-gu, Seoul, Korea, Republic of", "KR"),
    ],
)
def test_doc_duoc_quoc_gia_tu_duoi_dia_chi(address, expected):
    assert country_of_address(address) == expected


@pytest.mark.parametrize("address", [None, "", "12 Nguyễn Huệ, Quận 1", "khong-co-that"])
def test_dia_chi_khong_co_ten_nuoc_thi_tra_none(address):
    assert country_of_address(address) is None


# ---------- thứ tự ưu tiên khi chốt quốc gia ----------
# Chuỗi đầy đủ (có thêm toạ độ) được kiểm kỹ ở tests/test_toa_do_quoc_gia.py.
def test_dia_chi_thang_gl_cua_truy_van():
    """Tìm ở biên giới vẫn ra kết quả bên kia biên — địa chỉ mới là sự thật."""
    address = "NR1, Bavet, Svay Rieng, Cambodia"
    assert resolve_place_country(address, None, None, "VN") == ("KH", "address")


def test_gia_tri_da_luu_khong_bi_truy_van_sau_ghi_de():
    assert resolve_place_country(None, "TH", "address", "VN") == ("TH", "address")


def test_chua_biet_gi_thi_dung_gl_cua_truy_van():
    assert resolve_place_country(None, None, None, "th") == ("TH", "gl")
    assert resolve_place_country("12 Nguyễn Huệ, Quận 1", None, None, "vn") == ("VN", "gl")


def test_khong_co_manh_moi_nao_thi_de_trong():
    """Để trống chứ KHÔNG mặc định VN: đoán bừa quốc gia sẽ kéo theo đọc sai
    số điện thoại, mà số sai vẫn trông hợp lệ nên không ai phát hiện ra."""
    assert resolve_place_country(None, None, None, None) == (None, None)
    assert resolve_place_country(None, None, None, "") == (None, None)


# ---------- vùng đọc số điện thoại ----------
def test_vung_theo_dia_diem_khong_theo_job():
    assert region_of(Place(country_code="TH")) == "TH"
    assert region_of(Place(country_code="jp")) == "JP"


def test_chua_biet_quoc_gia_thi_dung_phuong_an_du_phong():
    assert region_of(Place(), "VN") == "VN"
    assert region_of(Place(), "") == "VN"


def test_so_noi_dia_thai_khong_duoc_bien_thanh_so_viet_nam():
    """Lỗi tiềm ẩn nguy hiểm nhất của cả module.

    Thẻ kết quả ghi số theo dạng NỘI ĐỊA. Đọc "081 939 8727" của Bangkok bằng
    vùng VN cho ra +84819398727 — một số Việt Nam có thật về mặt định dạng, nên
    `is_valid_number` gật đầu, giao diện hiện màu bình thường và nó nằm im trong
    file xuất cho sale gọi. Không có bộ kiểm tra nào khác bắt được ca này.
    """
    sai = normalize_phone("081 939 8727", "VN")
    assert sai[0] == "+84819398727"
    assert sai[2] is True, "chính vì nó 'hợp lệ' nên lỗi mới lọt được"

    dung = normalize_phone("081 939 8727", "TH")
    assert dung[0] == "+66819398727"
    assert dung[1] == "081 939 8727"
    assert dung[2] is True


def test_so_dang_quoc_te_thi_vung_khong_con_y_nghia():
    """Trang chi tiết trả về "+6622819715" — đã có mã nước nên đọc bằng vùng nào
    cũng ra một kết quả. Đây là lý do dữ liệu quét qua pha chi tiết không dính lỗi."""
    assert normalize_phone("+6622819715", "VN")[0] == normalize_phone("+6622819715", "TH")[0]


# ---------- tên quốc gia hiển thị ----------
def test_ten_quoc_gia_tieng_viet():
    assert geo.country_name("TH") == "Thái Lan"
    assert geo.country_name("th") == "Thái Lan"
    assert geo.country_name("KR") == "Hàn Quốc"


def test_ma_la_thi_tra_lai_chinh_ma_do():
    assert geo.country_name("ZZ") == "ZZ"
    assert geo.country_name(None) is None


# ---------- lý do dừng ----------
def test_cac_ly_do_dung_khong_trung_nhau():
    ly_do = [STOP_EXHAUSTED, STOP_CUT_OFF, STOP_CAP, STOP_EMPTY, STOP_UNKNOWN]
    assert len(set(ly_do)) == len(ly_do)


def test_search_outcome_dem_va_lap_duoc_nhu_danh_sach():
    """Nơi gọi cũ viết `len(cards)` và `for card in cards` — giữ nguyên được."""
    cards = [CardResult(name="A", maps_url="u1"), CardResult(name="B", maps_url="u2")]
    outcome = SearchOutcome(cards, STOP_EXHAUSTED)
    assert len(outcome) == 2
    assert [c.name for c in outcome] == ["A", "B"]


def test_mac_dinh_la_khong_ro_chu_khong_phai_da_quet_het():
    """Mặc định phải là ca THẬN TRỌNG. Nếu mặc định là 'đã quét hết' thì mọi
    đường thoát nào quên gán sẽ âm thầm báo địa bàn đã xong và người dùng bỏ
    sót dữ liệu mà không bao giờ biết."""
    assert SearchOutcome().stop_reason == STOP_UNKNOWN
    assert SearchOutcome().cards == []


# ---------- định dạng hiển thị theo từng quốc gia ----------
@pytest.mark.parametrize(
    ("so_goc", "vung", "hien_thi"),
    [
        # Mỗi nước một quy ước nhóm số RIÊNG, lấy từ metadata libphonenumber:
        # Nhật/Hàn/Mỹ dùng gạch nối, phần còn lại dùng khoảng trắng, độ dài mã
        # vùng cũng khác nhau. Không tự chế định dạng ở đây.
        ("0901234567", "VN", "+84 901 234 567"),
        ("02838221234", "VN", "+84 28 3822 1234"),
        ("022819715", "TH", "+66 2 281 9715"),
        ("0819398727", "TH", "+66 81 939 8727"),
        ("0312345678", "JP", "+81 3-1234-5678"),
        ("0212345678", "KR", "+82 2-1234-5678"),
        ("2125551234", "US", "+1 212-555-1234"),
        ("0201234567", "NL", "+31 20 123 4567"),
        ("042345678", "AE", "+971 4 234 5678"),
        ("02071234567", "GB", "+44 20 7123 4567"),
    ],
)
def test_hien_thi_dung_dinh_dang_tung_quoc_gia(so_goc, vung, hien_thi):
    e164, _, _ = normalize_phone(so_goc, vung)
    assert to_international(e164) == hien_thi


def test_luon_co_ma_quoc_gia_o_dau():
    """Đây là cả lý do đổi sang dạng quốc tế.

    Dạng nội địa bỏ mã nước đi: số Bangkok ra "02 281 9715", số TP.HCM ra
    "028 3822 1234". Người dùng là công ty Việt Nam, nhìn cái đầu tiên sẽ đọc
    thành số Việt Nam rồi bấm gọi không được, mà không có gì báo là đã nhầm.
    """
    e164, national, _ = normalize_phone("022819715", "TH")
    assert not national.startswith("+")          # dạng nội địa: không có mã nước
    assert to_international(e164).startswith("+66 ")


def test_khong_doc_duoc_thi_giu_nguyen_chu_khong_nuot_mat():
    assert to_international(None, "0123 khong ro") == "0123 khong ro"
    assert to_international(None) is None
    assert to_international("khong-phai-so", "du phong") == "du phong"


# ---------- nhận số điện thoại trên thẻ kết quả, theo từng nước ----------
@pytest.mark.parametrize(
    ("doan", "vung"),
    [
        ("+84 938 655 504", "VN"),
        ("081 939 8727", "TH"),      # nội địa Thái, có số 0 dẫn
        ("052 079 553", "TH"),
        ("+66 2 281 9715", "VN"),    # dạng quốc tế: vùng nào cũng phải nhận ra
        ("6222 3333", "SG"),         # Singapore KHÔNG có số 0 dẫn
        ("(212) 555-1234", "US"),
        ("+1 718-555-1234", "VN"),
        ("03-1234-5678", "JP"),
        ("020 7123 4567", "GB"),
        ("+971 4 234 5678", "VN"),
    ],
)
def test_nhan_ra_so_dien_thoai_cua_moi_nuoc(doan, vung):
    """Bản cũ dùng regex ghim cứng đầu số Việt Nam (`+84` hoặc `0`), nên MỌI số
    nước ngoài dạng quốc tế đều bị coi là không phải số điện thoại."""
    assert is_phone_segment(doan, vung) is True


@pytest.mark.parametrize(
    "doan",
    ["325 169-170", "1366", "10110", "54, 88", "109 3", "1112, 2"],
)
def test_manh_dia_chi_khong_bi_nham_thanh_so_dien_thoai(doan):
    """Toàn bộ lấy từ địa chỉ thật của dữ liệu đã quét ở Chiang Mai/Phuket.
    "325 169-170" có đủ 9 chữ số nên luật đếm chữ số cũ sẽ cho qua."""
    assert is_phone_segment(doan, "TH") is False


def test_the_nuoc_ngoai_lay_duoc_so_dien_thoai():
    card = parse_card(
        "Fruit Shop",
        [
            "Fruit Shop",
            "4,5(120)",
            "Fruit wholesaler · Sukhumvit Rd",
            "Open · Closes 9 PM · +66 2 281 9715",
        ],
        ["4,5 sao"],
        "TH",
    )
    assert card["phone_raw"] == "+66 2 281 9715"
    assert card["category"] == "Fruit wholesaler"


def test_so_dien_thoai_khong_chui_vao_cot_nganh_nghe():
    """Thẻ không có dòng ngành nghề: đoạn SĐT không nhận ra được sẽ rơi xuống
    nhánh gán `category`, và số điện thoại đi thẳng vào cột "Ngành nghề" của
    file xuất."""
    card = parse_card("ABC Fruits", ["ABC Fruits", "4,8(30)", "6222 3333"], ["4,8 sao"], "SG")
    assert card["phone_raw"] == "6222 3333"
    assert card["category"] is None


# ---------- chỉ bỏ qua truy vấn khi chạy lại cũng ra y hệt ----------
def test_ly_do_can_chay_lai_khong_duoc_bo_qua():
    """`cap` và `unknown` là hai ca mà chính giao diện đang khuyên người dùng
    chạy lại. Bỏ qua chúng thì lời khuyên đó thành bẫy: nâng trần rồi chạy lại,
    hệ thống vẫn im lặng bỏ qua suốt `ttl_days` ngày."""
    assert "cap" not in CO_THE_BO_QUA
    assert "unknown" not in CO_THE_BO_QUA
    assert "recent" not in CO_THE_BO_QUA


def test_ly_do_da_tron_ven_thi_duoc_bo_qua():
    assert set(CO_THE_BO_QUA) == {"exhausted", "cut_off", "empty"}
