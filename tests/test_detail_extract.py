"""
Chạy EXTRACT_JS THẬT trong Chromium headless trên file HTML mẫu (offline).

Vì sao phải mở trình duyệt thật thay vì tự bịa payload: EXTRACT_JS là ~40 dòng
JavaScript chạy trong trang — phần dễ hỏng nhất khi Google đổi DOM và cũng là
phần mà Python không kiểm được bằng cách nào khác. `page.set_content` cho ta chạy
đúng đoạn JS đó trên khung chi tiết mẫu mà KHÔNG chạm vào internet, nên test này
vẫn lặp lại được y hệt sau nhiều năm.

Mẫu HTML nằm ở tests/fixtures/, rút từ DOM thật đo ngày 2026-09-22 (hl=vi và hl=en).

Chạy:  pytest -m browser       (cần `playwright install chromium` một lần)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.scraper.engine.detail import EXTRACT_JS, build_detail

pytestmark = pytest.mark.browser


def doc(page, fixtures_dir: Path, ten_file: str) -> dict:
    """Nạp một khung chi tiết mẫu rồi chạy đúng EXTRACT_JS của production."""
    page.goto("about:blank")   # chỉ để location.href xác định; không ra internet
    page.set_content((fixtures_dir / ten_file).read_text(encoding="utf-8"))
    return page.evaluate(EXTRACT_JS)


@pytest.fixture
def raw_vi(chromium_page, fixtures_dir):
    return doc(chromium_page, fixtures_dir, "place_detail_vi.html")


@pytest.fixture
def raw_en(chromium_page, fixtures_dir):
    return doc(chromium_page, fixtures_dir, "place_detail_en_closed.html")


class TestKhungTiengVietDayDu:
    def test_extract_js_doc_dung_cac_selector_ben(self, raw_vi):
        """Bốn trường nghiệp vụ đều đi qua data-item-id — loại selector bền nhất."""
        assert raw_vi["name"] == "Cửa Hàng Trái Cây ABC"
        assert raw_vi["phone_item_id"] == "phone:tel:0901234567"
        assert raw_vi["website"] == "https://traicayabc.vn/"
        assert raw_vi["address_label"].startswith("Địa chỉ:")
        assert raw_vi["category"] == "Cửa hàng trái cây"

    def test_lay_rating_o_dau_trang_chu_khong_phai_sao_cua_tung_danh_gia(self, raw_vi):
        """Ca hiểm: mỗi bài đánh giá cũng có span[role=img] '5 sao' / '3 sao'.
        Phải lấy nhãn ĐẦU TIÊN (của doanh nghiệp), không phải của bài đánh giá."""
        assert raw_vi["rating_label"] == "4,6 sao"
        assert raw_vi["review_label"] == "1.234 bài đánh giá"

    def test_bang_bieu_do_sao_khong_bi_dem_thanh_gio_mo_cua(self, raw_vi):
        """Khung đánh giá có MỘT <table> nữa (biểu đồ 5-4-3-2-1 sao) với đủ 2 cột
        và cột thứ hai có chữ. Chỉ luật 'cột đầu toàn chữ số thì bỏ' cứu được
        con số này: 4 = số ngày trong bảng giờ, 7 = đã đếm nhầm cả biểu đồ."""
        assert raw_vi["hours_rows"] == 4

    def test_doc_duoc_tuoi_cac_danh_gia_hien_san_trong_khung(self, raw_vi):
        assert raw_vi["review_ages"] == ["2 năm trước", "3 tháng trước", "một năm trước"]

    def test_build_detail_ra_dung_bon_truong_nghiep_vu(self, raw_vi):
        detail = build_detail(raw_vi)
        assert detail.name == "Cửa Hàng Trái Cây ABC"
        assert detail.address == "123 Đường Lê Lợi, Phường Bến Nghé, Quận 1, Thành phố Hồ Chí Minh"
        assert detail.phone_raw == "0901234567"
        assert detail.website == "https://traicayabc.vn/"

    def test_build_detail_ra_dung_rating_va_so_danh_gia(self, raw_vi):
        detail = build_detail(raw_vi)
        assert detail.rating == 4.6
        assert detail.review_count == 1234
        assert detail.category == "Cửa hàng trái cây"

    def test_danh_gia_moi_nhat_la_3_thang_truoc(self, raw_vi):
        """Trong khung có cả '2 năm trước' và 'một năm trước'; tín hiệu còn-sống
        phải lấy cái MỚI NHẤT = 90 ngày."""
        assert build_detail(raw_vi).latest_review_days == 90

    def test_co_gio_mo_cua_va_tom_tat_da_cat_duoi_xem_them_gio(self, raw_vi):
        detail = build_detail(raw_vi)
        assert detail.has_hours is True
        assert detail.hours_summary == "Đang mở cửa " + chr(0x00B7) + " Đóng cửa vào 22:00"

    def test_dang_ban_binh_thuong_va_khong_thieu_truong_nao(self, raw_vi):
        detail = build_detail(raw_vi)
        assert detail.business_status == "OPERATIONAL"
        assert detail.missing_fields == []


class TestKhungTiengAnhDaDongCua:
    def test_bat_duoc_permanently_closed(self, raw_en):
        detail = build_detail(raw_en)
        assert detail.business_status == "CLOSED_PERMANENTLY"

    def test_khong_sdt_khong_website_thi_de_trong_chu_khong_no(self, raw_en):
        detail = build_detail(raw_en)
        assert raw_en["phone_item_id"] is None
        assert detail.phone_raw is None
        assert detail.website is None

    def test_khong_co_bang_gio_va_khong_co_nut_oh(self, raw_en):
        assert raw_en["hours_rows"] == 0
        assert raw_en["oh_label"] is None
        detail = build_detail(raw_en)
        assert detail.has_hours is False
        assert detail.hours_summary is None

    def test_van_doc_duoc_phan_con_lai_bang_tieng_anh(self, raw_en):
        detail = build_detail(raw_en)
        assert detail.name == "Old Fruit Shop"
        assert detail.address == "45 Nguyen Hue, District 1"
        assert detail.category == "Fruit store"
        assert detail.rating == 3.9
        assert detail.review_count == 87


class TestBuildDetailThuan:
    """Không cần trình duyệt: dựng tay payload y như EXTRACT_JS trả về."""

    def test_website_la_duong_dan_tuong_doi_thi_khong_phai_website_doanh_nghiep(self):
        raw = {
            "name": "X",
            "url": "https://www.google.com/maps/place/X/data=!4m5!3m4!1s0x1:0xff!8m2!3d10.5!4d106.5",
            "phone_item_id": None,
            "phone_label": "Điện thoại: 028 3822 1234",
            "website": "/maps/x",
            "hours_rows": 0,
            "header_text": "",
        }
        detail = build_detail(raw)
        assert detail.website is None

    def test_lay_feature_id_lat_lng_tu_url(self):
        raw = {
            "name": "X",
            "url": "https://www.google.com/maps/place/X/data=!4m5!3m4!1s0x1:0xff!8m2!3d10.5!4d106.5",
            "header_text": "",
        }
        detail = build_detail(raw)
        assert detail.feature_id == "0x1:0xff"
        assert detail.cid == "255"
        assert (detail.lat, detail.lng) == (10.5, 106.5)

    def test_thieu_data_item_id_thi_lui_ve_aria_label_cua_nut_sdt(self):
        """Đã gặp thật: nút SĐT render trước khi data-item-id được gắn."""
        raw = {
            "name": "X",
            "url": "",
            "phone_item_id": None,
            "phone_label": "Điện thoại: 028 3822 1234",
            "header_text": "",
        }
        assert build_detail(raw).phone_raw == "028 3822 1234"

    def test_khong_co_bang_gio_nhung_co_nut_oh_van_tinh_la_co_gio(self):
        """Google KHÔNG render bảng giờ cho nơi có nhiều loại giờ (giao hàng /
        mang đi / tại chỗ); lúc đó nút 'oh' là đường lùi duy nhất."""
        raw = {
            "name": "X",
            "url": "",
            "hours_rows": 0,
            "oh_label": "Đang mở cửa · Đóng cửa vào 22:00·Xem thêm giờ",
            "header_text": "",
        }
        detail = build_detail(raw)
        assert detail.has_hours is True
        assert detail.hours_summary == "Đang mở cửa " + chr(0x00B7) + " Đóng cửa vào 22:00"

    def test_payload_rong_khong_lam_no_va_bao_dung_truong_thieu(self):
        detail = build_detail({})
        assert detail.name is None
        assert detail.business_status == "OPERATIONAL"
        assert detail.has_hours is False
        assert detail.missing_fields == ["name", "address", "category"]
