"""
Test cho `parse_card` (parsers.py) và `card_from_raw` (search.py).

Vì sao file này quan trọng nhất bộ test: THẺ kết quả tìm kiếm cho sẵn 3/4 trường
nghiệp vụ (tên, SĐT, danh mục + địa chỉ rút gọn, trạng thái mở cửa). Mở trang chi
tiết tốn 1,5-2 giây mỗi địa điểm và tăng rủi ro bị Google chặn, nên nếu lớp bóc
thẻ này sai thì hoặc ta mất dữ liệu, hoặc ta phải mở chi tiết cho mọi địa điểm.

Toàn bộ dữ liệu dưới đây là DOM THẬT đã đo trên Google Maps (hl=vi) ngày
2026-09-22 — copy nguyên văn, kể cả lỗi chính tả và khoảng trắng thừa của Google.
"""

from __future__ import annotations

import pytest

from app.modules.scraper.engine.parsers import parse_card
from app.modules.scraper.engine.search import card_from_raw

# --------------------------------------------------------------------------- #
# Dữ liệu thật (giữ nguyên văn — KHÔNG "dọn dẹp" cho đẹp)
# --------------------------------------------------------------------------- #

CARD_FULL = {
    "href": "https://www.google.com/maps/place/SHOP/data=!4m7!3m6!1s0x31752f4b3330bcc7:0x4db964d976f50e42!8m2!3d10.776889!4d106.700806",
    "name": "SHOP GIỎ QUÀ TRÁI CÂY NHẬP KHẨU GÁI ÚT FRUITS",
    "lines": [
        "SHOP GIỎ QUÀ TRÁI CÂY NHẬP KHẨU GÁI ÚT FRUITS",
        "SHOP GIỎ QUÀ TRÁI CÂY NHẬP KHẨU GÁI ÚT FRUITS",
        "4,9",
        "Cửa hàng bán buôn trái cây · Chung cư linh Tây đường D2 phường linh Xuân",
        "Đang mở cửa · Đóng cửa vào 21:00 · +84 938 655 504",
    ],
    "ratingLabels": ["4,9 sao 64 bài đánh giá"],
}

CARD_NO_PHONE = {  # thẻ không có số điện thoại
    "href": ".../data=!1s0x1:0x2!8m2!3d10.75!4d106.66",
    "name": "Chợ Trái Cây - Rau quả BX Chợ Lớn",
    "lines": [
        "Chợ Trái Cây - Rau quả BX Chợ Lớn",
        "Chợ Trái Cây - Rau quả BX Chợ Lớn",
        "4,3",
        "Cửa hàng rau quả · 114 Trang Tử",
        "Đang mở cửa · Đóng cửa vào 0:00",
    ],
    "ratingLabels": ["4,3 sao"],
}

CARD_SPONSORED = {  # thẻ quảng cáo, có "Được tài trợ", SĐT dạng cố định
    "href": ".../data=!1s0x3:0x4!8m2!3d10.77!4d106.63",
    "name": "GO! Phú Thạnh",
    "lines": [
        "GO! Phú Thạnh",
        "Được tài trợ",
        "GO! Phú Thạnh",
        "4,1(3.822)",
        "Đại siêu thị · 212 Thoại Ngọc Hầu",
        "Đang mở cửa · Đóng cửa vào 22:00 · 028 2227 8879",
    ],
    "ratingLabels": ["4,1 sao 3.822 bài đánh giá"],
}

CARD_CLOSED = {  # đã đóng cửa vĩnh viễn / tạm thời
    "href": ".../data=!1s0x5:0x6!8m2!3d10.80!4d106.70",
    "name": "T Fruit",
    "lines": [
        "T Fruit",
        "T Fruit",
        "4,3(166)",
        "Cửa hàng bán buôn trái cây · 330/2 Đ. Phan Văn Trị",
        "Tạm thời đóng cửa · +84 938 540 707",
    ],
    "ratingLabels": ["4,3 sao 166 bài đánh giá"],
}

CARD_ODD_ADDRESS = {  # địa chỉ có đoạn rỗng ở giữa + số nhà dễ nhầm là SĐT
    "href": ".../data=!1s0x7:0x8!8m2!3d10.75!4d106.66",
    "name": "Hoarosa",
    "lines": [
        "Hoarosa",
        "Hoarosa",
        "5,0",
        "Cửa hàng bán buôn trái cây ·  · Chung cư Chợ Quán, 98 12 12 5",
        "Mở cả ngày · +84 389 707 677",
    ],
    "ratingLabels": ["5,0 sao", "Không có lối vào cho xe lăn"],
}

ALL_CARDS = {
    "CARD_FULL": CARD_FULL,
    "CARD_NO_PHONE": CARD_NO_PHONE,
    "CARD_SPONSORED": CARD_SPONSORED,
    "CARD_CLOSED": CARD_CLOSED,
    "CARD_ODD_ADDRESS": CARD_ODD_ADDRESS,
}


def parsed(card: dict) -> dict:
    return parse_card(card["name"], card["lines"], card["ratingLabels"])


# --------------------------------------------------------------------------- #
# Danh mục + địa chỉ rút gọn
# --------------------------------------------------------------------------- #


class TestDanhMucVaDiaChi:
    @pytest.mark.parametrize(
        ("card", "danh_muc", "dia_chi"),
        [
            (CARD_FULL, "Cửa hàng bán buôn trái cây", "Chung cư linh Tây đường D2 phường linh Xuân"),
            (CARD_NO_PHONE, "Cửa hàng rau quả", "114 Trang Tử"),
            (CARD_SPONSORED, "Đại siêu thị", "212 Thoại Ngọc Hầu"),
            (CARD_CLOSED, "Cửa hàng bán buôn trái cây", "330/2 Đ. Phan Văn Trị"),
            (CARD_ODD_ADDRESS, "Cửa hàng bán buôn trái cây", "Chung cư Chợ Quán, 98 12 12 5"),
        ],
    )
    def test_tach_dung_danh_muc_va_dia_chi_rut_gon(self, card, danh_muc, dia_chi):
        out = parsed(card)
        assert out["category"] == danh_muc
        assert out["address_short"] == dia_chi

    def test_doan_rong_giua_hai_dau_cham_khong_lam_lech_dia_chi(self):
        """Google thỉnh thoảng nhả ra 'danh mục ·  · địa chỉ' (đoạn giữa rỗng).
        Nếu không bỏ đoạn rỗng thì địa chỉ sẽ thành chuỗi trống."""
        out = parsed(CARD_ODD_ADDRESS)
        assert out["address_short"] == "Chung cư Chợ Quán, 98 12 12 5"
        assert out["category"] == "Cửa hàng bán buôn trái cây"

    def test_dong_ten_lap_lai_khong_bi_hieu_thanh_danh_muc(self):
        """Thẻ thật lặp tên doanh nghiệp 2 lần (aria-label + chữ hiển thị)."""
        assert parsed(CARD_FULL)["category"] != CARD_FULL["name"]


# --------------------------------------------------------------------------- #
# Số điện thoại — chỗ dễ sai nhất
# --------------------------------------------------------------------------- #


class TestSoDienThoai:
    @pytest.mark.parametrize(
        ("card", "sdt"),
        [
            (CARD_FULL, "+84 938 655 504"),
            (CARD_SPONSORED, "028 2227 8879"),   # SỐ CỐ ĐỊNH, không phải di động
            (CARD_CLOSED, "+84 938 540 707"),
            (CARD_ODD_ADDRESS, "+84 389 707 677"),
        ],
    )
    def test_lay_dung_so_dien_thoai_tren_the(self, card, sdt):
        assert parsed(card)["phone_raw"] == sdt

    def test_the_khong_co_so_thi_de_trong_chu_khong_bia(self):
        assert parsed(CARD_NO_PHONE)["phone_raw"] is None

    @pytest.mark.parametrize(
        ("card", "so_nha"),
        [
            (CARD_SPONSORED, "212 Thoại Ngọc Hầu"),
            (CARD_ODD_ADDRESS, "Chung cư Chợ Quán, 98 12 12 5"),
            (CARD_NO_PHONE, "114 Trang Tử"),
            (CARD_CLOSED, "330/2 Đ. Phan Văn Trị"),
        ],
    )
    def test_so_nha_trong_dia_chi_khong_bi_nhan_nham_thanh_sdt(self, card, so_nha):
        """Ca hiểm nhất của cả file: '212 Thoại Ngọc Hầu' và '98 12 12 5' trông
        rất giống SĐT. Nếu lọt vào phone_raw thì sale gọi vào số rác."""
        out = parsed(card)
        assert out["address_short"] == so_nha
        assert out["phone_raw"] != so_nha
        if out["phone_raw"]:
            assert out["phone_raw"] not in so_nha


# --------------------------------------------------------------------------- #
# Quảng cáo / trạng thái / giờ
# --------------------------------------------------------------------------- #


class TestQuangCaoVaTrangThai:
    def test_the_quang_cao_duoc_danh_dau_sponsored(self):
        """Thẻ 'Được tài trợ' vẫn là lead hợp lệ nhưng phải gắn cờ để sale biết
        thứ tự trên Google không phản ánh độ liên quan."""
        assert parsed(CARD_SPONSORED)["sponsored"] is True

    @pytest.mark.parametrize(
        "key", ["CARD_FULL", "CARD_NO_PHONE", "CARD_CLOSED", "CARD_ODD_ADDRESS"]
    )
    def test_the_thuong_khong_bi_gan_co_quang_cao(self, key):
        assert parsed(ALL_CARDS[key])["sponsored"] is False

    def test_dong_duoc_tai_tro_khong_bi_hieu_thanh_danh_muc(self):
        out = parsed(CARD_SPONSORED)
        assert out["category"] == "Đại siêu thị"
        assert out["address_short"] == "212 Thoại Ngọc Hầu"

    def test_tam_thoi_dong_cua_tra_ve_closed_temporarily(self):
        out = parsed(CARD_CLOSED)
        assert out["business_status"] == "CLOSED_TEMPORARILY"
        assert out["hours_summary"] == "Tạm thời đóng cửa"
        assert out["has_hours"] is False   # đang nghỉ thì không tính là có giờ mở cửa

    @pytest.mark.parametrize(
        ("card", "tom_tat_gio"),
        [
            (CARD_FULL, "Đang mở cửa · Đóng cửa vào 21:00"),
            (CARD_NO_PHONE, "Đang mở cửa · Đóng cửa vào 0:00"),
            (CARD_SPONSORED, "Đang mở cửa · Đóng cửa vào 22:00"),
            (CARD_ODD_ADDRESS, "Mở cả ngày"),
        ],
    )
    def test_gom_dung_cac_manh_trang_thai_mo_cua(self, card, tom_tat_gio):
        out = parsed(card)
        assert out["hours_summary"] == tom_tat_gio
        assert out["business_status"] == "OPERATIONAL"
        assert out["has_hours"] is True

    def test_sdt_khong_lot_vao_tom_tat_gio(self):
        """SĐT nằm CÙNG DÒNG với trạng thái mở cửa; phải tách sạch."""
        assert "938" not in (parsed(CARD_FULL)["hours_summary"] or "")


# --------------------------------------------------------------------------- #
# Rating và số lượt đánh giá
# --------------------------------------------------------------------------- #


class TestRatingVaSoDanhGia:
    @pytest.mark.parametrize(
        ("card", "rating", "so_danh_gia"),
        [
            (CARD_FULL, 4.9, 64),
            (CARD_NO_PHONE, 4.3, None),       # nhãn chỉ có '4,3 sao', không có số lượt
            (CARD_SPONSORED, 4.1, 3822),      # '3.822' là 3822 chứ không phải 3,822
            (CARD_CLOSED, 4.3, 166),
            (CARD_ODD_ADDRESS, 5.0, None),
        ],
    )
    def test_rating_va_so_luot_danh_gia(self, card, rating, so_danh_gia):
        out = parsed(card)
        assert out["rating"] == rating
        assert out["review_count"] == so_danh_gia

    def test_nhan_phu_khong_phai_rating_bi_bo_qua(self):
        """CARD_ODD_ADDRESS có thêm nhãn 'Không có lối vào cho xe lăn' trong cùng
        danh sách role=img — không được biến nó thành rating/số đánh giá."""
        out = parsed(CARD_ODD_ADDRESS)
        assert out["rating"] == 5.0
        assert out["review_count"] is None

    def test_dong_chi_co_rating_khong_bi_hieu_thanh_danh_muc(self):
        """Dòng '4,9' hay '4,1(3.822)' đứng riêng một dòng trên thẻ."""
        for card in (CARD_FULL, CARD_SPONSORED, CARD_CLOSED):
            out = parsed(card)
            assert out["category"] is not None
            assert "(" not in out["category"]


# --------------------------------------------------------------------------- #
# card_from_raw — ghép thẻ thành CardResult
# --------------------------------------------------------------------------- #


class TestCardFromRaw:
    def test_the_day_du_ra_dung_moi_truong(self):
        card = card_from_raw(CARD_FULL)
        assert card is not None
        assert card.name == "SHOP GIỎ QUÀ TRÁI CÂY NHẬP KHẨU GÁI ÚT FRUITS"
        assert card.maps_url == CARD_FULL["href"]
        assert card.feature_id == "0x31752f4b3330bcc7:0x4db964d976f50e42"
        assert card.cid == "5600618496778374722"
        assert (card.lat, card.lng) == (10.776889, 106.700806)
        assert card.category == "Cửa hàng bán buôn trái cây"
        assert card.address_short == "Chung cư linh Tây đường D2 phường linh Xuân"
        assert card.phone_raw == "+84 938 655 504"
        assert card.rating == 4.9
        assert card.review_count == 64
        assert card.business_status == "OPERATIONAL"
        assert card.has_hours is True
        assert card.sponsored is False

    @pytest.mark.parametrize(
        ("card", "feature_id", "cid", "lat", "lng"),
        [
            (CARD_NO_PHONE, "0x1:0x2", "2", 10.75, 106.66),
            (CARD_SPONSORED, "0x3:0x4", "4", 10.77, 106.63),
            (CARD_CLOSED, "0x5:0x6", "6", 10.80, 106.70),
            (CARD_ODD_ADDRESS, "0x7:0x8", "8", 10.75, 106.66),
        ],
    )
    def test_feature_id_lat_lng_lay_tu_href(self, card, feature_id, cid, lat, lng):
        result = card_from_raw(card)
        assert result.feature_id == feature_id
        assert result.cid == cid
        assert (result.lat, result.lng) == (lat, lng)

    def test_the_da_dong_cua_giu_nguyen_trang_thai_khi_ghep(self):
        card = card_from_raw(CARD_CLOSED)
        assert card.business_status == "CLOSED_TEMPORARILY"
        assert card.has_hours is False
        assert card.phone_raw == "+84 938 540 707"   # vẫn lấy SĐT để sale xác minh lại

    def test_the_quang_cao_giu_co_sponsored_khi_ghep(self):
        assert card_from_raw(CARD_SPONSORED).sponsored is True

    def test_the_thieu_ten_tra_none(self):
        """Feed của Google có cả thẻ quảng cáo rỗng / ô chèn giữa danh sách;
        không có tên thì không phải lead, bỏ luôn."""
        assert card_from_raw({"href": "", "name": None, "lines": [], "ratingLabels": []}) is None
        assert card_from_raw({"href": "https://x/y", "name": "   ", "lines": []}) is None

    def test_thieu_aria_label_thi_lay_ten_tu_duong_dan(self):
        """Đường lùi: tên nằm sẵn trong URL /maps/place/<tên>/..."""
        card = card_from_raw(
            {
                "href": "https://www.google.com/maps/place/C%E1%BB%ADa+H%C3%A0ng+ABC/data=!1s0x1:0x2",
                "name": None,
                "lines": [],
                "ratingLabels": [],
            }
        )
        assert card is not None
        assert card.name == "Cửa Hàng ABC"
        assert card.feature_id == "0x1:0x2"

    def test_moi_the_that_deu_ra_lead_dung_ten(self):
        for key, raw in ALL_CARDS.items():
            card = card_from_raw(raw)
            assert card is not None, key
            assert card.name == raw["name"], key
