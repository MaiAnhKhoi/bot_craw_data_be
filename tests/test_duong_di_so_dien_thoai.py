"""Số điện thoại đi trọn đường: thẻ Google -> chuẩn hoá -> hiển thị -> file xuất.

Số điện thoại là GIÁ TRỊ SỬ DỤNG của toàn bộ công cụ này — quét được tên công ty
mà số sai thì coi như không quét được gì. Mà sai số điện thoại lại là loại lỗi
IM LẶNG nhất trong dự án: "081 882 1104" đọc theo vùng VN ra `+84818821104`,
đọc theo vùng TH ra `+66818821104`, và CẢ HAI đều qua được `is_valid_number`.
Không có bộ kiểm tra tự động nào ngoài file này bắt được ca đó.

Chạy offline hoàn toàn: không DB, không mạng, không trình duyệt.
"""
from __future__ import annotations

import pytest

from app.modules.scraper.engine.normalize import (
    normalize_phone,
    phone_country,
    to_international,
)
from app.modules.scraper.engine.parsers import parse_card


def _the_google(nganh: str, dong_sdt: str, ten: str = "Fruit Co") -> list[str]:
    """Dựng lại đúng hình dạng các dòng text của một thẻ kết quả Google Maps."""
    return [ten, "4,5(120)", nganh, dong_sdt]


# Mỗi ca: (nước, dòng SĐT trên thẻ, E.164 đúng, chuỗi hiển thị đúng)
CAC_NUOC = [
    ("VN", "Đang mở cửa · Đóng cửa vào 21:00 · 090 123 4567", "+84901234567", "+84 901 234 567"),
    ("VN", "Đang mở cửa · 028 3822 1234", "+842838221234", "+84 28 3822 1234"),
    ("TH", "Open · Closes 9 PM · 081 939 8727", "+66819398727", "+66 81 939 8727"),
    ("TH", "Open · 02 281 9715", "+6622819715", "+66 2 281 9715"),
    ("TH", "Open · +66 2 245 5311", "+6622455311", "+66 2 245 5311"),
    ("JP", "Open · 03-1234-5678", "+81312345678", "+81 3-1234-5678"),
    ("KR", "Open · 02-1234-5678", "+82212345678", "+82 2-1234-5678"),
    ("SG", "Open · 6222 3333", "+6562223333", "+65 6222 3333"),
    ("US", "Open · (212) 555-1234", "+12125551234", "+1 212-555-1234"),
    ("GB", "Open · 020 7123 4567", "+442071234567", "+44 20 7123 4567"),
    ("NL", "Open · 020 123 4567", "+31201234567", "+31 20 123 4567"),
    ("AE", "Open · 04 234 5678", "+97142345678", "+971 4 234 5678"),
    ("CN", "Open · 010 1234 5678", "+861012345678", "+86 10 1234 5678"),
    ("MY", "Open · 03-2382 1234", "+60323821234", "+60 3-2382 1234"),
]


@pytest.mark.parametrize(("vung", "dong", "e164_mong", "hien_thi_mong"), CAC_NUOC)
def test_tu_the_google_toi_chuoi_hien_thi(vung, dong, e164_mong, hien_thi_mong):
    """Đường đi đầy đủ: bóc thẻ -> chuẩn hoá theo vùng -> chuỗi hiển thị."""
    card = parse_card("Fruit Co", _the_google("Fruit wholesaler", dong), ["4,5 sao"], vung)
    assert card["phone_raw"] is not None, f"KHÔNG bóc được SĐT từ thẻ {vung}"

    e164, national, hop_le = normalize_phone(card["phone_raw"], vung)
    assert e164 == e164_mong
    assert hop_le is True
    assert to_international(e164) == hien_thi_mong
    # Quốc gia đọc ngược từ số phải khớp nước đang quét.
    assert phone_country(e164) == vung


@pytest.mark.parametrize(("vung", "dong", "e164_mong", "_hien_thi"), CAC_NUOC)
def test_sdt_khong_lan_sang_cot_nganh_nghe(vung, dong, e164_mong, _hien_thi):
    """Thẻ KHÔNG có dòng ngành nghề: đoạn SĐT không nhận ra được sẽ rơi xuống
    nhánh gán `category` và đi thẳng vào cột "Ngành nghề" của file xuất."""
    chi_co_sdt = dong.split("·")[-1].strip()
    card = parse_card("Fruit Co", ["Fruit Co", "4,5(120)", chi_co_sdt], ["4,5 sao"], vung)
    assert card["phone_raw"] is not None
    assert card["category"] is None, f"SĐT {vung} chui vào cột Ngành nghề"


def test_doc_sai_vung_van_ra_so_HOP_LE_nen_khong_the_tin_vao_co_hop_le():
    """Ca nguy hiểm nhất, viết ra để không ai sửa code theo hướng "cứ kiểm tra
    is_valid_number là đủ". Một số Bangkok đọc bằng vùng VN cho ra một số Việt
    Nam hợp lệ hoàn toàn — chỉ có QUỐC GIA đúng mới cứu được."""
    sai = normalize_phone("081 882 1104", "VN")
    dung = normalize_phone("081 882 1104", "TH")
    assert sai[2] is True and dung[2] is True      # cả hai đều "hợp lệ"
    assert sai[0] == "+84818821104"
    assert dung[0] == "+66818821104"
    assert phone_country(sai[0]) == "VN"
    assert phone_country(dung[0]) == "TH"


def test_so_dang_quoc_te_mien_nhiem_voi_vung_sai():
    """Trang chi tiết luôn trả dạng quốc tế. Đó là lý do dữ liệu đi qua pha chi
    tiết không dính lỗi vùng, và cũng là lý do lỗi này nằm im rất lâu."""
    for vung in ("VN", "TH", "US", "JP"):
        assert normalize_phone("+66 2 281 9715", vung)[0] == "+6622819715"


def test_nhieu_so_tren_mot_the_thi_lay_so_hop_le_dau_tien():
    """Google đôi khi liệt kê nhiều số trên một dòng."""
    e164, _, hop_le = normalize_phone("028 3822 1234 / 0901 234 567", "VN")
    assert hop_le is True
    assert e164 == "+842838221234"


def test_so_dau_khong_hop_le_thi_lay_so_hop_le_ke_tiep():
    """"028 1234 5678" thừa một chữ số nên không phải số Việt Nam hợp lệ — phải
    bỏ qua chứ không được lấy bừa rồi đánh dấu là không hợp lệ, vì số thứ hai
    mới là số gọi được."""
    e164, _, hop_le = normalize_phone("028 1234 5678 / 0901 234 567", "VN")
    assert hop_le is True
    assert e164 == "+84901234567"


@pytest.mark.parametrize("rac", [None, "", "   ", "không có số", "---"])
def test_dau_vao_rac_khong_lam_vo_gi(rac):
    assert normalize_phone(rac, "VN") == (None, None, False)
    assert to_international(None, rac) == (rac or None)


def test_so_khong_hop_le_van_duoc_giu_lai_de_nguoi_dung_tu_nhin():
    """Google thỉnh thoảng trả số lạ ("+66 6284101915" — đã gặp thật). Không được
    nuốt mất: thà hiện ra kèm dấu hiệu còn hơn im lặng bỏ đi một đầu mối."""
    e164, _, hop_le = normalize_phone("+66 6284101915", "TH")
    assert hop_le is False
    assert e164 is not None
    assert to_international(e164) is not None
