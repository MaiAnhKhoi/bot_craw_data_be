"""
Test cho `app/modules/scraper/engine/normalize.py`.

Hai hàm ở đây quyết định chất lượng file lead giao cho sale:
  - `normalize_phone`: Google trả SĐT ở đủ kiểu (0938…, (028) …, +84…, hai số
    ngăn bằng dấu '/'). Muốn nhập CRM hay gọi tự động thì phải về E.164.
  - `clean_company_website`: link trong khung chi tiết RẤT HAY là link nội bộ của
    Google (đặt bàn, chỉ đường, chia sẻ). Nhận nhầm những link đó thành website
    doanh nghiệp sẽ làm hỏng luôn tín hiệu "còn sống" ở liveness.py.
"""

from __future__ import annotations

import pytest

from app.modules.scraper.engine.normalize import (
    clean_company_website,
    normalize_phone,
    website_host,
)


class TestNormalizePhoneHopLe:
    def test_di_dong_viet_nam(self):
        e164, quoc_gia, hop_le = normalize_phone("0938 655 504")
        assert (e164, quoc_gia, hop_le) == ("+84938655504", "0938 655 504", True)

    def test_co_dinh_co_ngoac_don(self):
        """Số cố định TP.HCM viết kiểu '(028) 2227 8879' — dấu ngoặc phải được bỏ."""
        e164, quoc_gia, hop_le = normalize_phone("(028) 2227 8879")
        assert e164 == "+842822278879"
        assert quoc_gia == "028 2227 8879"
        assert hop_le is True

    def test_da_o_dang_e164_thi_giu_nguyen_ket_qua(self):
        assert normalize_phone("+84 938 655 504") == ("+84938655504", "0938 655 504", True)

    def test_nhieu_so_ngan_bang_dau_gach_cheo_lay_so_dau_tien_hop_le(self):
        """Google hay liệt kê 2 số trong cùng một ô."""
        e164, _, hop_le = normalize_phone("028 2227 8879 / 0938 655 504")
        assert e164 == "+842822278879"
        assert hop_le is True

    @pytest.mark.parametrize("dau_phan_cach", ["/", ",", ";", " hoặc ", " or "])
    def test_moi_kieu_phan_cach_deu_tach_duoc(self, dau_phan_cach):
        raw = "abc" + dau_phan_cach + "0938 655 504"
        e164, _, hop_le = normalize_phone(raw)
        assert (e164, hop_le) == ("+84938655504", True)

    def test_bo_qua_so_dau_khong_hop_le_de_lay_so_hop_le_phia_sau(self):
        e164, _, hop_le = normalize_phone("0123 / 0938 655 504")
        assert (e164, hop_le) == ("+84938655504", True)

    def test_vung_khac_viet_nam(self):
        e164, quoc_gia, hop_le = normalize_phone("650-253-0000", region="US")
        assert e164 == "+16502530000"
        assert quoc_gia == "(650) 253-0000"
        assert hop_le is True

    def test_cung_mot_chuoi_doi_vung_thi_doi_ket_qua(self):
        """Số VN mà khai region='US' thì không còn hợp lệ — chứng minh tham số
        region thật sự được dùng chứ không phải trang trí."""
        assert normalize_phone("0938 655 504", region="VN")[2] is True
        assert normalize_phone("0938 655 504", region="US")[2] is False


class TestNormalizePhoneKhongHopLe:
    def test_so_sai_van_tra_best_effort_nhung_danh_dau_khong_hop_le(self):
        """Giữ lại để người dùng tự nhìn và sửa tay, nhưng KHÔNG được coi là hợp lệ."""
        e164, quoc_gia, hop_le = normalize_phone("0123")
        assert e164 == "+840123"
        assert quoc_gia == "0123"
        assert hop_le is False

    def test_so_qua_ngan(self):
        assert normalize_phone("12345")[2] is False

    @pytest.mark.parametrize("rac", [None, "", "   ", "abcxyz", "liên hệ Zalo"])
    def test_chuoi_rac_tra_toan_none(self, rac):
        assert normalize_phone(rac) == (None, None, False)


class TestCleanCompanyWebsite:
    @pytest.mark.parametrize(
        "url",
        [
            "https://traicayabc.vn/",
            "https://shop.traicayabc.com.vn/gio-qua",
            "http://abc.vn",                     # http trần vẫn là website thật
            "https://abc.vn:8443/trang-chu",
        ],
    )
    def test_giu_website_that_cua_doanh_nghiep(self, url):
        assert clean_company_website(url) == url

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.google.com/maps/place/x",
            "https://google.com/search?q=abc",
            "https://www.google.com.vn/maps",
            "https://maps.app.goo.gl/abcd1234",
            "https://goo.gl/maps/xyz",
            "https://shop.business.site",        # trang miễn phí do Google dựng
            "https://business.site",
        ],
    )
    def test_loai_moi_link_noi_bo_cua_google(self, url):
        assert clean_company_website(url) is None

    @pytest.mark.parametrize("url", ["/maps/x", "/url?q=abc", "/"])
    def test_loai_duong_dan_tuong_doi(self, url):
        """href tương đối luôn là link nội bộ của chính trang Google Maps."""
        assert clean_company_website(url) is None

    @pytest.mark.parametrize("url", [None, "", "   "])
    def test_rong_tra_none(self, url):
        assert clean_company_website(url) is None

    def test_tu_them_https_khi_thieu_giao_thuc(self):
        assert clean_company_website("traicayabc.vn") == "https://traicayabc.vn"
        assert clean_company_website("www.traicayabc.vn/gio-qua") == "https://www.traicayabc.vn/gio-qua"

    def test_cat_khoang_trang_thua_hai_dau(self):
        assert clean_company_website("  https://traicayabc.vn/  ") == "https://traicayabc.vn/"

    @pytest.mark.parametrize("url", ["localhost", "khong-phai-ten-mien"])
    def test_chuoi_khong_co_dau_cham_khong_phai_ten_mien(self, url):
        assert clean_company_website(url) is None

    def test_ten_mien_chua_chu_google_nhung_khac_google(self):
        """'googlefruit.vn' KHÔNG phải tên miền của Google — chỉ so khớp đúng tên
        miền hoặc tên miền con, không so khớp chuỗi con."""
        assert clean_company_website("https://googlefruit.vn") == "https://googlefruit.vn"
        assert clean_company_website("https://mybusiness.site") == "https://mybusiness.site"


class TestWebsiteHost:
    @pytest.mark.parametrize(
        ("url", "host"),
        [
            ("https://WWW.Abc.VN/path?a=1", "www.abc.vn"),   # hạ về chữ thường
            ("http://abc.vn:8080/x", "abc.vn"),              # bỏ cổng
            ("https://shop.traicayabc.com.vn", "shop.traicayabc.com.vn"),
            ("abc.vn/x", "abc.vn"),                          # không có giao thức vẫn đọc được
        ],
    )
    def test_boc_dung_host(self, url, host):
        assert website_host(url) == host

    @pytest.mark.parametrize("url", [None, ""])
    def test_rong_tra_none(self, url):
        assert website_host(url) is None
