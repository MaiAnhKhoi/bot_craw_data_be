"""
Test cho `app/modules/scraper/engine/parsers.py` — lớp bóc tách THUẦN.

Vì sao chăm chút cho mấy hàm regex này: toàn bộ giá trị nghiệp vụ của con bot
(tên, SĐT, địa chỉ, rating, độ "còn sống") đều đi qua đây. Google KHÔNG báo trước
khi đổi chữ trên giao diện, nên mỗi ca hiểm ghi lại ở đây là một lần đã đo thật
trên Google Maps tiếng Việt/tiếng Anh ngày 2026-09-22.

Không DB, không trình duyệt, không mạng — chạy được ở mọi máy.
"""

from __future__ import annotations

import pytest

from app.modules.scraper.engine.parsers import (
    detect_business_status,
    feature_id_to_cid,
    is_phone_segment,
    newest_review_days,
    normalize_ws,
    parse_hours_summary,
    parse_phone_item_id,
    parse_place_url,
    parse_rating,
    parse_relative_days,
    parse_review_count,
    split_segments,
    strip_label,
)

NBSP = chr(0x00A0)
THIN_SPACE = chr(0x2009)
NARROW_NBSP = chr(0x202F)
MIDDLE_DOT = chr(0x00B7)
DOT_OPERATOR = chr(0x22C5)


class TestNormalizeWs:
    def test_gop_khoang_trang_va_cat_hai_dau(self):
        assert normalize_ws("   Cửa   hàng  trái cây  ") == "Cửa hàng trái cây"

    def test_doi_moi_loai_khoang_trang_la_ve_dau_cach_thuong(self):
        """Google rải NBSP / thin space trong aria-label; nếu không quy về dấu
        cách thường thì mọi so sánh chuỗi phía sau đều trượt."""
        raw = "090" + NBSP + "123" + THIN_SPACE + "4567" + NARROW_NBSP + "x"
        assert normalize_ws(raw) == "090 123 4567 x"

    def test_chuoi_rong_va_chuoi_toan_khoang_trang_thanh_none(self):
        assert normalize_ws("") is None
        assert normalize_ws("   " + NBSP + " ") is None

    def test_none_van_la_none(self):
        assert normalize_ws(None) is None


class TestParsePlaceUrl:
    def test_url_day_du_uu_tien_toa_do_ghim_hon_toa_do_khung_nhin(self):
        """URL thật có CẢ @lat,lng (tâm khung nhìn) lẫn !3d/!4d (ghim địa điểm).
        Phải lấy !3d/!4d, nếu không lead sẽ mang toạ độ của... màn hình."""
        url = (
            "https://www.google.com/maps/place/Shop+A/@10.5,106.5,17z/"
            "data=!3m1!4b1!4m6!3m5!1s0x31752f4b3330bcc7:0x4db964d976f50e42"
            "!8m2!3d10.776889!4d106.700806"
        )
        info = parse_place_url(url)
        assert info["feature_id"] == "0x31752f4b3330bcc7:0x4db964d976f50e42"
        assert info["cid"] == "5600618496778374722"
        assert (info["lat"], info["lng"]) == (10.776889, 106.700806)
        assert info["name"] == "Shop A"

    def test_url_chi_co_data_van_lay_duoc_feature_id_va_ten(self):
        url = "https://www.google.com/maps/place/C%E1%BB%ADa+H%C3%A0ng/data=!4m2!3m1!1s0x31752f:0xabc"
        info = parse_place_url(url)
        assert info["feature_id"] == "0x31752f:0xabc"
        assert info["cid"] == "2748"
        assert info["name"] == "Cửa Hàng"          # %-decode + '+' thành dấu cách
        assert info["lat"] is None and info["lng"] is None

    def test_url_chi_co_toa_do_sau_dau_a_cong(self):
        info = parse_place_url("https://www.google.com/maps/@10.7769,106.7009,15z")
        assert (info["lat"], info["lng"]) == (10.7769, 106.7009)
        assert info["feature_id"] is None and info["cid"] is None

    def test_url_am_duoc_ho_tro(self):
        info = parse_place_url("https://www.google.com/maps/@-33.8688,-151.2093,15z")
        assert (info["lat"], info["lng"]) == (-33.8688, -151.2093)

    @pytest.mark.parametrize("url", ["", "https://example.com/hello", "không phải url"])
    def test_url_rac_tra_ve_toan_none_chu_khong_no(self, url):
        assert parse_place_url(url) == {
            "feature_id": None, "cid": None, "lat": None, "lng": None, "name": None,
        }


class TestFeatureIdToCid:
    def test_doi_khoi_hex_thu_hai_sang_thap_phan(self):
        assert feature_id_to_cid("0x31752f4b3330bcc7:0x4db964d976f50e42") == "5600618496778374722"
        assert feature_id_to_cid("0x1:0xabc") == "2748"

    @pytest.mark.parametrize("value", [None, "", "0x31752f4b3330bcc7", "0x1:zzz"])
    def test_dau_vao_khong_dung_dinh_dang_tra_none(self, value):
        assert feature_id_to_cid(value) is None


class TestStripLabel:
    def test_bo_tien_to_tieng_viet(self):
        assert strip_label("Địa chỉ: 12 Lê Lợi, Quận 1", "address") == "12 Lê Lợi, Quận 1"
        assert strip_label("Điện thoại: 090 123 4567", "phone") == "090 123 4567"
        assert strip_label("Trang web: abc.vn", "website") == "abc.vn"

    def test_bo_tien_to_tieng_anh(self):
        assert strip_label("Address: 12 Le Loi", "address") == "12 Le Loi"
        assert strip_label("Phone: 090 123 4567", "phone") == "090 123 4567"

    def test_khong_phan_biet_hoa_thuong_o_tien_to(self):
        assert strip_label("ADDRESS: 12 Le Loi", "address") == "12 Le Loi"

    def test_khong_co_tien_to_thi_giu_nguyen(self):
        assert strip_label("12 Lê Lợi, Quận 1", "address") == "12 Lê Lợi, Quận 1"

    def test_loai_nhan_la_thi_khong_cat_gi(self):
        assert strip_label("Giá: ₫₫", "price") == "Giá: ₫₫"

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_rong_tra_none(self, value):
        assert strip_label(value, "address") is None

    def test_chi_co_tien_to_khong_co_noi_dung_tra_none(self):
        assert strip_label("Địa chỉ:", "address") is None


class TestParseRating:
    def test_dau_phay_thap_phan_kieu_viet(self):
        assert parse_rating("4,5 sao") == 4.5

    def test_dau_cham_thap_phan_kieu_anh(self):
        assert parse_rating("4.5 stars") == 4.5

    def test_diem_tuyet_doi_va_diem_0_van_hop_le(self):
        assert parse_rating("5,0 sao") == 5.0
        assert parse_rating("0,0 sao") == 0.0

    @pytest.mark.parametrize("text", ["9,9 sao", "10 sao", "87 reviews"])
    def test_ngoai_khoang_0_5_bi_loai(self, text):
        """Số ngoài thang 0-5 gần như chắc chắn là ta bắt nhầm nhãn khác
        (số lượt đánh giá, giá...) nên trả None thay vì bịa ra rating."""
        assert parse_rating(text) is None

    def test_bay_ngam_dau_cham_nghin_bi_doc_thanh_dau_thap_phan(self):
        """BẪY ĐÃ BIẾT (không phải lỗi của hàm): '1.234 bài đánh giá' ra 1.234 vì
        dấu chấm phân cách nghìn kiểu Việt bị hiểu là dấu thập phân, và 1.234 thì
        lọt thang 0-5 nên không bị loại.

        Hàm này chỉ ĐƯỢC PHÉP nhận nhãn sao; detail.py lọc sẵn bằng /(star|sao)/i
        và parse_card chỉ đưa vào dòng khớp _RATING_ONLY_RE. Ghi lại ca này để
        nếu ai nới bộ lọc đó ra thì có test nhắc ngay."""
        assert parse_rating("1.234 bài đánh giá") == 1.234

    @pytest.mark.parametrize("text", [None, "", "sao", "chưa có đánh giá"])
    def test_khong_co_so_tra_none(self, text):
        assert parse_rating(text) is None


class TestParseReviewCount:
    @pytest.mark.parametrize(
        ("text", "mong_doi"),
        [
            ("1.234 bài đánh giá", 1234),      # vi: dấu chấm là phân cách nghìn
            ("1,234 reviews", 1234),           # en: dấu phẩy là phân cách nghìn
            ("1 review", 1),
            ("(1.234)", 1234),                 # thẻ kết quả chỉ ghi số trong ngoặc
            ("(166)", 166),
            ("2.5K reviews", 2500),            # en rút gọn
            ("(2,5 K)", 2500),
            ("1,2 N đánh giá", 1200),          # vi rút gọn: N = nghìn
            ("4,9 sao 64 bài đánh giá", 64),   # nhãn thật trên thẻ: rating + số đánh giá
            ("4,1 sao 3.822 bài đánh giá", 3822),
        ],
    )
    def test_cac_dang_so_luot_danh_gia_that(self, text, mong_doi):
        assert parse_review_count(text) == mong_doi

    @pytest.mark.parametrize("text", [None, "", "4,9 sao", "Không có lối vào cho xe lăn"])
    def test_chuoi_khong_chua_so_luot_danh_gia(self, text):
        """Ca hiểm: '4,9 sao' là RATING. Nếu hàm này nuốt luôn số 4,9 thì mọi
        doanh nghiệp đều bị ghi nhận có 4 lượt đánh giá."""
        assert parse_review_count(text) is None


class TestParsePhoneItemId:
    def test_boc_so_tu_data_item_id(self):
        assert parse_phone_item_id("phone:tel:0901234567") == "0901234567"

    def test_giai_ma_urlencode(self):
        """Số quốc tế bị urlencode: '+' thành %2B, dấu cách thành %20."""
        assert parse_phone_item_id("phone:tel:%2B84%20938%20655%20504") == "+84 938 655 504"

    @pytest.mark.parametrize("value", [None, "", "address", "phone:0901234567", "authority"])
    def test_khong_phai_item_id_dien_thoai(self, value):
        assert parse_phone_item_id(value) is None

    def test_tien_to_dung_nhung_khong_co_so_tra_none(self):
        assert parse_phone_item_id("phone:tel:") is None


class TestDetectBusinessStatus:
    @pytest.mark.parametrize(
        "text",
        [
            "Cửa hàng ABC Đã đóng cửa vĩnh viễn",
            "đóng cửa vĩnh viễn",
            "Old Fruit Shop Permanently closed",
            "PERMANENTLY CLOSED",
        ],
    )
    def test_dong_cua_vinh_vien_hai_ngon_ngu(self, text):
        assert detect_business_status(text) == "CLOSED_PERMANENTLY"

    @pytest.mark.parametrize(
        "text", ["Tạm thời đóng cửa", "tạm đóng cửa", "Temporarily closed"]
    )
    def test_dong_cua_tam_thoi_hai_ngon_ngu(self, text):
        assert detect_business_status(text) == "CLOSED_TEMPORARILY"

    def test_vinh_vien_thang_tam_thoi_khi_ca_hai_cung_xuat_hien(self):
        assert detect_business_status("Tạm thời đóng cửa · Đã đóng cửa vĩnh viễn") == "CLOSED_PERMANENTLY"

    @pytest.mark.parametrize(
        "text",
        [
            None,
            "",
            "Đang mở cửa · Đóng cửa vào 22:00",   # 'Đóng cửa vào 22:00' là GIỜ, không phải đã nghỉ bán
            "Mở cả ngày",
            "Open · Closes 10 PM",
        ],
    )
    def test_binh_thuong_va_none_deu_la_operational(self, text):
        assert detect_business_status(text) == "OPERATIONAL"


class TestParseHoursSummary:
    def test_cat_duoi_xem_them_gio_tieng_viet(self):
        label = "Đang mở cửa · Đóng cửa vào 22:00·Xem thêm giờ"
        assert parse_hours_summary(label) == "Đang mở cửa " + MIDDLE_DOT + " Đóng cửa vào 22:00"

    def test_cat_duoi_see_more_hours_tieng_anh_va_chuan_hoa_dau_cham_giua(self):
        label = "Open " + DOT_OPERATOR + " Closes 10 PM" + DOT_OPERATOR + "See more hours"
        assert parse_hours_summary(label) == "Open " + MIDDLE_DOT + " Closes 10 PM"

    def test_nhan_khong_co_duoi_thi_giu_nguyen(self):
        assert parse_hours_summary("Mở cả ngày") == "Mở cả ngày"

    @pytest.mark.parametrize("label", [None, "", "   "])
    def test_nhan_rong_tra_none(self, label):
        assert parse_hours_summary(label) is None


class TestParseRelativeDays:
    @pytest.mark.parametrize(
        ("text", "mong_doi"),
        [
            ("2 năm trước", 730),
            ("một năm trước", 365),        # Google viết CHỮ khi n = 1
            ("3 tháng trước", 90),
            ("5 ngày trước", 5),
            ("2 tuần trước", 14),
            ("a year ago", 365),
            ("2 months ago", 60),
            ("10 days ago", 10),
            ("Một Năm Trước", 365),        # có hoa có thường, vẫn phải hiểu
        ],
    )
    def test_cac_cach_viet_that_tren_google(self, text, mong_doi):
        assert parse_relative_days(text) == mong_doi

    @pytest.mark.parametrize(
        "text", [None, "", "hôm qua", "vừa xong", "Rất tươi ngon", "2 giờ trước"]
    )
    def test_chuoi_khong_doc_duoc_tra_none(self, text):
        """'2 giờ trước' không nằm trong bảng đơn vị (ta chỉ quan tâm độ tuổi tính
        bằng ngày) nên trả None thay vì đoán bừa."""
        assert parse_relative_days(text) is None


class TestNewestReviewDays:
    def test_lay_gia_tri_nho_nhat_tuc_la_danh_gia_moi_nhat(self):
        assert newest_review_days(["2 năm trước", "3 tháng trước", "một năm trước"]) == 90

    def test_bo_qua_cac_chuoi_khong_doc_duoc(self):
        assert newest_review_days(["Rất tươi ngon", "5 ngày trước", "hôm qua"]) == 5

    @pytest.mark.parametrize("labels", [None, [], ["Rất tươi ngon", "hôm qua"]])
    def test_khong_co_gia_tri_nao_doc_duoc_tra_none(self, labels):
        assert newest_review_days(labels) is None


class TestSplitSegments:
    def test_tach_theo_dau_cham_giua_va_bo_doan_rong(self):
        """Ca thật: địa chỉ có đoạn RỖNG kẹp giữa hai dấu chấm giữa."""
        line = "Cửa hàng bán buôn trái cây " + MIDDLE_DOT + "  " + MIDDLE_DOT + " Chung cư Chợ Quán, 98 12 12 5"
        assert split_segments(line) == ["Cửa hàng bán buôn trái cây", "Chung cư Chợ Quán, 98 12 12 5"]

    def test_chap_nhan_ca_dot_operator(self):
        assert split_segments("A" + DOT_OPERATOR + "B") == ["A", "B"]

    def test_khong_co_dau_cham_giua_thi_tra_mot_doan(self):
        assert split_segments("Cửa hàng rau quả") == ["Cửa hàng rau quả"]

    @pytest.mark.parametrize("line", ["", "   ", MIDDLE_DOT + " " + MIDDLE_DOT])
    def test_dong_rong_tra_danh_sach_rong(self, line):
        assert split_segments(line) == []


class TestIsPhoneSegment:
    @pytest.mark.parametrize(
        "segment",
        [
            "028 2227 8879",     # số cố định TP.HCM — ca này từng bị bỏ sót
            "+84 938 655 504",
            "0901234567",
            "090 123 4567",
            "84 938 655 504",    # thiếu dấu + nhưng vẫn là số quốc tế
        ],
    )
    def test_nhan_dung_so_dien_thoai(self, segment):
        assert is_phone_segment(segment) is True

    @pytest.mark.parametrize(
        "segment",
        [
            "212 Thoại Ngọc Hầu",             # số nhà + tên đường
            "98 12 12 5",                     # số nhà kiểu lạ, rất giống SĐT
            "Chung cư Chợ Quán, 98 12 12 5",
            "330/2 Đ. Phan Văn Trị",
            "4,9",                            # điểm đánh giá
            "0901",                           # quá ngắn để là SĐT
            "Đang mở cửa",
            "",
        ],
    )
    def test_tu_choi_moi_thu_khong_phai_so_dien_thoai(self, segment):
        """Đây là hàng phòng thủ quan trọng nhất của thẻ kết quả: nếu số nhà lọt
        vào ô SĐT thì sale gọi vào số rác và mất niềm tin vào cả file lead."""
        assert is_phone_segment(segment) is False
