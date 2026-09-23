"""
Test cho `app/modules/scraper/engine/liveness.py` — chấm điểm "công ty còn sống".

Vì sao quan trọng: danh bạ Google Maps đầy hồ sơ của doanh nghiệp đã nghỉ bán.
Điểm số này quyết định sale gọi ai TRƯỚC, nên sai công thức là lãng phí thời gian
người thật. Hai tính chất phải giữ bằng mọi giá:
  1. Tín hiệu Google KHẲNG ĐỊNH (đã đóng cửa vĩnh viễn) thì chốt luôn, không
     pha loãng bằng các tín hiệu suy đoán.
  2. Mọi lý do trừ điểm đều phải trả ra cho giao diện — không để hộp đen quyết thay.
"""

from __future__ import annotations

import pytest

from app.modules.scraper.engine.liveness import (
    ACTIVE_THRESHOLD,
    PENALTIES,
    SUSPECT_THRESHOLD,
    LivenessInput,
    evaluate,
)

# Một doanh nghiệp "khoẻ" làm mốc; mỗi test chỉ bẻ đúng một vài tín hiệu.
KHOE = dict(
    business_status="OPERATIONAL",
    phone_e164="+84938655504",
    phone_valid=True,
    website="https://traicayabc.vn",
    website_status="OK",
    review_count=50,
    latest_review_days=30,
    has_hours=True,
)


def inp(**thay_doi) -> LivenessInput:
    return LivenessInput(**{**KHOE, **thay_doi})


class TestCongTyKhoe:
    def test_du_moi_tin_hieu_tot_thi_100_diem_va_khong_co_ly_do_tru(self):
        result = evaluate(inp())
        assert result.score == 100
        assert result.label == "ACTIVE"
        assert result.reasons == []


class TestDongCuaVinhVienChotLuon:
    def test_diem_0_nhan_dead_va_dung_mot_ly_do(self):
        """Dù MỌI tín hiệu khác cũng xấu, reasons vẫn chỉ có đúng 1 phần tử:
        không cần liệt kê thêm gì khi Google đã khẳng định."""
        result = evaluate(
            LivenessInput(
                business_status="CLOSED_PERMANENTLY",
                phone_e164=None,
                website=None,
                website_status="NONE",
                review_count=0,
                latest_review_days=2000,
                has_hours=False,
            )
        )
        assert result.score == 0
        assert result.label == "DEAD"
        assert result.reasons == ["CLOSED_PERMANENTLY"]

    def test_moi_tin_hieu_khac_deu_bi_bo_qua(self):
        """Cùng một hồ sơ, chỉ khác cờ của Google: hồ sơ hoàn hảo vẫn phải ra 0."""
        result = evaluate(inp(business_status="CLOSED_PERMANENTLY"))
        assert (result.score, result.label, result.reasons) == (0, "DEAD", ["CLOSED_PERMANENTLY"])


class TestDongCuaTamThoi:
    def test_tam_dong_va_khong_co_sdt_ra_suspect_dung_cong_thuc(self):
        result = evaluate(inp(business_status="CLOSED_TEMPORARILY", phone_e164=None, phone_valid=None))
        assert result.reasons == ["CLOSED_TEMPORARILY", "NO_PHONE"]
        # 100 - 45 - 15 = 40, đúng ngưỡng SUSPECT
        assert result.score == 100 - PENALTIES["CLOSED_TEMPORARILY"] - PENALTIES["NO_PHONE"]
        assert result.score == 40
        assert result.label == "SUSPECT"

    def test_tam_dong_cong_don_het_tin_hieu_xau_thi_rot_xuong_dead(self):
        result = evaluate(
            inp(
                business_status="CLOSED_TEMPORARILY",
                phone_e164=None,
                phone_valid=None,
                website=None,
                website_status="NONE",
                review_count=0,
                latest_review_days=None,
                has_hours=False,
            )
        )
        assert result.reasons == [
            "CLOSED_TEMPORARILY", "NO_PHONE", "NO_WEBSITE", "NO_REVIEWS", "NO_HOURS",
        ]
        assert result.score == 100 - (45 + 15 + 5 + 20 + 10)
        assert result.score == 5
        assert result.label == "DEAD"


class TestThieuTungTinHieu:
    def test_khong_danh_gia_khong_gio_khong_website(self):
        result = evaluate(
            inp(website=None, website_status="NONE", review_count=0, latest_review_days=None, has_hours=False)
        )
        assert result.reasons == ["NO_WEBSITE", "NO_REVIEWS", "NO_HOURS"]
        assert result.score == 100 - (
            PENALTIES["NO_WEBSITE"] + PENALTIES["NO_REVIEWS"] + PENALTIES["NO_HOURS"]
        )
        assert result.score == 65
        assert result.label == "SUSPECT"

    def test_so_danh_gia_none_cung_tinh_la_khong_co_danh_gia(self):
        assert "NO_REVIEWS" in evaluate(inp(review_count=None, latest_review_days=None)).reasons

    @pytest.mark.parametrize(("so_danh_gia", "ly_do"), [(0, "NO_REVIEWS"), (1, "FEW_REVIEWS"), (2, "FEW_REVIEWS")])
    def test_it_danh_gia_bi_tru_nhe_hon_khong_co_danh_gia(self, so_danh_gia, ly_do):
        result = evaluate(inp(review_count=so_danh_gia))
        assert ly_do in result.reasons
        assert PENALTIES["FEW_REVIEWS"] < PENALTIES["NO_REVIEWS"]

    def test_tu_3_danh_gia_tro_len_thi_khong_bi_tru(self):
        assert evaluate(inp(review_count=3)).reasons == []

    def test_sdt_khong_hop_le_bi_tru_nhe_hon_khong_co_sdt(self):
        khong_co = evaluate(inp(phone_e164=None, phone_valid=None))
        khong_hop_le = evaluate(inp(phone_e164="+840123", phone_valid=False))
        assert khong_co.reasons == ["NO_PHONE"]
        assert khong_hop_le.reasons == ["INVALID_PHONE"]
        assert khong_hop_le.score > khong_co.score

    @pytest.mark.parametrize(
        ("trang_thai", "ly_do"), [("DEAD", "WEBSITE_DEAD"), ("PARKED", "WEBSITE_PARKED")]
    )
    def test_website_chet_hoac_do_bi_tru(self, trang_thai, ly_do):
        result = evaluate(inp(website_status=trang_thai))
        assert result.reasons == [ly_do]

    @pytest.mark.parametrize("trang_thai", ["OK", "UNCHECKED"])
    def test_website_song_hoac_chua_kiem_thi_khong_tru(self, trang_thai):
        assert evaluate(inp(website_status=trang_thai)).reasons == []


class TestDanhGiaCu:
    @pytest.mark.parametrize(
        ("so_ngay", "ly_do"),
        [
            (364, None),
            (365, "REVIEWS_STALE_1Y"),
            (729, "REVIEWS_STALE_1Y"),
            (730, "REVIEWS_STALE_2Y"),
            (1094, "REVIEWS_STALE_2Y"),
            (1095, "REVIEWS_STALE_3Y"),
            (3000, "REVIEWS_STALE_3Y"),
        ],
    )
    def test_moc_1_2_3_nam(self, so_ngay, ly_do):
        result = evaluate(inp(latest_review_days=so_ngay))
        assert result.reasons == ([] if ly_do is None else [ly_do])

    def test_danh_gia_cu_3_nam_khong_gan_kem_nhan_1y_hay_2y(self):
        """Ba mốc loại trừ lẫn nhau — nếu gắn chồng thì bị trừ tới 50 điểm cho
        cùng một tín hiệu."""
        reasons = evaluate(inp(latest_review_days=365 * 3)).reasons
        assert reasons == ["REVIEWS_STALE_3Y"]
        assert "REVIEWS_STALE_1Y" not in reasons
        assert "REVIEWS_STALE_2Y" not in reasons

    def test_co_tuoi_danh_gia_nhung_khong_co_danh_gia_thi_khong_gan_nhan_cu(self):
        """Ca hiểm: bóc được chuỗi '5 năm trước' từ một mẩu chữ nào đó trong khung
        nhưng review_count = 0. Không được trừ hai lần cho cùng một sự thật
        'chỗ này không có đánh giá'."""
        result = evaluate(inp(review_count=0, latest_review_days=365 * 5))
        assert result.reasons == ["NO_REVIEWS"]
        assert not any(r.startswith("REVIEWS_STALE") for r in result.reasons)


class TestDiemVaNhan:
    @pytest.mark.parametrize(
        ("thay_doi", "diem_mong_doi", "nhan_mong_doi"),
        [
            # 100 - 15 - 5 - 10 = 70 -> đúng ngưỡng ACTIVE (>=)
            (dict(phone_e164=None, phone_valid=None, website=None, website_status="NONE", has_hours=False),
             70, "ACTIVE"),
            # 100 - 25 - 6 = 69 -> tụt ngay dưới ngưỡng
            (dict(website_status="DEAD", latest_review_days=400), 69, "SUSPECT"),
            # 100 - 45 - 15 = 40 -> đúng ngưỡng SUSPECT (>=)
            (dict(business_status="CLOSED_TEMPORARILY", phone_e164=None, phone_valid=None), 40, "SUSPECT"),
            # 100 - 45 - 16 = 39 -> tụt ngay dưới ngưỡng
            (dict(business_status="CLOSED_TEMPORARILY", latest_review_days=730), 39, "DEAD"),
        ],
    )
    def test_dung_nguong_70_va_40(self, thay_doi, diem_mong_doi, nhan_mong_doi):
        result = evaluate(inp(**thay_doi))
        assert result.score == diem_mong_doi
        assert result.label == nhan_mong_doi

    def test_hang_so_nguong_dung_nhu_tai_lieu(self):
        assert (ACTIVE_THRESHOLD, SUSPECT_THRESHOLD) == (70, 40)

    def test_tru_qua_100_diem_van_khong_am(self):
        """Tổng điểm trừ ở đây là 115 — phải kẹp về 0, không được ra số âm."""
        result = evaluate(
            inp(
                business_status="CLOSED_TEMPORARILY",
                phone_e164=None,
                phone_valid=None,
                website_status="DEAD",
                review_count=0,
                latest_review_days=None,
                has_hours=False,
            )
        )
        assert sum(PENALTIES[r] for r in result.reasons) == 115
        assert result.score == 0
        assert result.label == "DEAD"

    @pytest.mark.parametrize("trang_thai", ["OPERATIONAL", "CLOSED_TEMPORARILY", "CLOSED_PERMANENTLY"])
    @pytest.mark.parametrize("co_sdt", [True, False])
    @pytest.mark.parametrize("so_ngay", [None, 10, 400, 2000])
    def test_diem_luon_nam_trong_khoang_0_100(self, trang_thai, co_sdt, so_ngay):
        result = evaluate(
            inp(
                business_status=trang_thai,
                phone_e164="+84938655504" if co_sdt else None,
                phone_valid=co_sdt or None,
                latest_review_days=so_ngay,
            )
        )
        assert 0 <= result.score <= 100
        assert result.label in {"ACTIVE", "SUSPECT", "DEAD"}

    def test_diem_bang_100_tru_tong_diem_phat_cua_cac_ly_do(self):
        """Công thức phải luôn khớp với bảng PENALTIES — không có điểm trừ ẩn."""
        result = evaluate(
            inp(phone_e164=None, phone_valid=None, website_status="PARKED", review_count=2,
                latest_review_days=800, has_hours=False)
        )
        assert result.reasons == [
            "NO_PHONE", "WEBSITE_PARKED", "FEW_REVIEWS", "REVIEWS_STALE_2Y", "NO_HOURS",
        ]
        assert result.score == 100 - sum(PENALTIES[r] for r in result.reasons)


class TestKhongCoMaChet:
    """Mỗi mã trong PENALTIES phải thực sự sinh ra được từ một đầu vào hợp lệ."""

    KICH_BAN = {
        "CLOSED_PERMANENTLY": dict(business_status="CLOSED_PERMANENTLY"),
        "CLOSED_TEMPORARILY": dict(business_status="CLOSED_TEMPORARILY"),
        "NO_PHONE": dict(phone_e164=None, phone_valid=None),
        "INVALID_PHONE": dict(phone_e164="+840123", phone_valid=False),
        "NO_WEBSITE": dict(website=None, website_status="NONE"),
        "WEBSITE_DEAD": dict(website_status="DEAD"),
        "WEBSITE_PARKED": dict(website_status="PARKED"),
        "NO_REVIEWS": dict(review_count=0, latest_review_days=None),
        "FEW_REVIEWS": dict(review_count=2),
        "REVIEWS_STALE_1Y": dict(latest_review_days=400),
        "REVIEWS_STALE_2Y": dict(latest_review_days=800),
        "REVIEWS_STALE_3Y": dict(latest_review_days=1200),
        "NO_HOURS": dict(has_hours=False),
    }

    def test_bang_kich_ban_phu_het_bang_penalties(self):
        assert set(self.KICH_BAN) == set(PENALTIES)

    @pytest.mark.parametrize("ma", sorted(PENALTIES))
    def test_moi_ma_deu_sinh_ra_duoc(self, ma):
        assert ma in evaluate(inp(**self.KICH_BAN[ma])).reasons

    def test_moi_ma_deu_tru_diem_that(self):
        for ma, phat in PENALTIES.items():
            result = evaluate(inp(**self.KICH_BAN[ma]))
            assert result.score == max(0, 100 - phat), ma
