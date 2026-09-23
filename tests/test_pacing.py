"""
Test cho `app/modules/scraper/engine/pacing.py` — nhịp cào tự điều chỉnh.

Đây là lớp chống chặn quan trọng nhất: chạy một luồng trên IP công ty, không có
proxy để xoay, nên thứ duy nhất điều khiển được là TỐC ĐỘ. Sai một chiều:
  - nới quá nhanh -> ăn captcha, mất cả phiên và có thể cả IP trong vài giờ;
  - không bao giờ nới -> job 10.000 địa điểm chạy mấy ngày không xong.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from app.modules.scraper.engine import pacing
from app.modules.scraper.engine.pacing import Pacer, in_night_rest


class TestCurrentSeconds:
    def test_nhip_nen_la_trung_binh_cong_cua_khoang(self):
        assert Pacer(base_min=4.0, base_max=8.0).current_seconds == 6.0

    def test_nhan_theo_he_so_hien_tai(self):
        p = Pacer(base_min=4.0, base_max=8.0, multiplier=2.0)
        assert p.current_seconds == 12.0

    def test_lam_tron_mot_chu_so_thap_phan(self):
        """Giá trị này đi thẳng lên log/API nên phải gọn, không lê thê số lẻ."""
        p = Pacer(base_min=4.0, base_max=7.0, multiplier=4.0 / 3)
        # (4 + 7) / 2 * 1,333... = 7,333... -> 7.3
        assert p.current_seconds == 7.3


class TestPhanUngVoiTinHieuXau:
    def test_on_suspicion_nhan_doi_he_so(self):
        p = Pacer()
        p.on_suspicion()
        assert p.multiplier == 2.0
        p.on_suspicion()
        assert p.multiplier == 4.0

    def test_on_block_nhan_ba_he_so(self):
        p = Pacer()
        p.on_block()
        assert p.multiplier == 3.0

    def test_bi_chan_sau_khi_da_nghi_ngo_thi_cong_don(self):
        p = Pacer()
        p.on_suspicion()   # 2
        p.on_block()       # 6
        assert p.multiplier == 6.0

    @pytest.mark.parametrize("su_co", ["on_suspicion", "on_block"])
    def test_moi_su_co_deu_xoa_chuoi_chay_em(self, su_co):
        """Chuỗi 'êm' phải tính lại từ đầu, nếu không thì vừa bị soi đã nới nhịp."""
        p = Pacer(probe_after=3)
        p.on_success()
        p.on_success()
        assert p.consecutive_ok == 2
        getattr(p, su_co)()
        assert p.consecutive_ok == 0

    def test_he_so_bi_chan_tran_boi_ceiling(self):
        """Trần = ceiling / base_min, vì wait() kẹp mỗi đầu mút theo ceiling."""
        p = Pacer(base_min=4.0, base_max=8.0, ceiling=45.0)
        for _ in range(10):
            p.on_block()
        assert p.multiplier == pytest.approx(45.0 / 4.0)   # 11.25
        p.on_suspicion()
        assert p.multiplier == pytest.approx(45.0 / 4.0)   # không vượt được nữa

    def test_base_min_bang_0_khong_lam_chia_cho_0(self):
        p = Pacer(base_min=0.0, base_max=1.0, ceiling=45.0)
        p.on_block()
        assert p.multiplier == 3.0


class TestNoiNhipKhiChayEm:
    def test_chi_noi_sau_khi_du_so_lan_em(self):
        p = Pacer(probe_after=3)
        p.on_suspicion()
        assert p.multiplier == 2.0
        p.on_success()
        p.on_success()
        assert p.multiplier == 2.0          # chưa đủ 3 lần -> chưa nới
        p.on_success()
        assert p.multiplier == pytest.approx(2.0 / 1.5)
        assert p.consecutive_ok == 0        # đếm lại từ đầu cho bậc sau

    def test_noi_tu_tu_chu_khong_nhay_thang_ve_muc_nen(self):
        p = Pacer(probe_after=1)
        p.on_block()                        # 3.0
        p.on_success()
        assert p.multiplier == pytest.approx(2.0)
        p.on_success()
        assert p.multiplier == pytest.approx(4.0 / 3.0)

    def test_khong_bao_gio_nhanh_hon_muc_nen(self):
        """Dù chạy êm bao lâu, hệ số cũng dừng ở 1.0 — nhịp nền là sàn."""
        p = Pacer(probe_after=1)
        p.on_suspicion()
        for _ in range(50):
            p.on_success()
        assert p.multiplier == 1.0
        assert p.current_seconds == Pacer().current_seconds

    def test_dang_o_muc_nen_thi_on_success_khong_doi_gi(self):
        p = Pacer(probe_after=2)
        p.on_success()
        p.on_success()
        p.on_success()
        assert p.multiplier == 1.0
        assert p.consecutive_ok == 3        # chỉ đếm, không reset khi chưa cần nới

    def test_reset_dua_ve_trang_thai_ban_dau(self):
        p = Pacer()
        p.on_block()
        p.on_success()
        p.reset()
        assert (p.multiplier, p.consecutive_ok) == (1.0, 0)


class TestWaitKepTheoCeiling:
    """`wait()` mới là chỗ ceiling thật sự có hiệu lực, nên kiểm luôn hai đầu mút.

    Chỉ thay `random.uniform` để đọc được khoảng thực tế — bản thân Pacer vẫn
    chạy nguyên bản, không mock.
    """

    @staticmethod
    def _khoang_cho(p: Pacer, monkeypatch) -> tuple[float, float]:
        ghi: dict[str, float] = {}

        def gia_lap_uniform(lo: float, hi: float) -> float:
            ghi["lo"], ghi["hi"] = lo, hi
            return 0.0

        monkeypatch.setattr(pacing.random, "uniform", gia_lap_uniform)
        asyncio.run(p.wait())
        return ghi["lo"], ghi["hi"]

    def test_muc_nen_cho_dung_khoang_base(self, monkeypatch):
        p = Pacer(base_min=4.0, base_max=8.0)
        assert self._khoang_cho(p, monkeypatch) == (4.0, 8.0)

    def test_he_so_kich_tran_thi_ca_hai_dau_mut_bi_kep_ve_ceiling(self, monkeypatch):
        p = Pacer(base_min=4.0, base_max=8.0, ceiling=45.0)
        for _ in range(10):
            p.on_block()
        assert self._khoang_cho(p, monkeypatch) == (45.0, 45.0)


class TestNghiDem:
    @pytest.mark.parametrize("gio", [2, 3, 5])
    def test_trong_khung_2h_6h(self, gio):
        assert in_night_rest(datetime(2026, 9, 23, gio, 30), 2, 6) is True

    @pytest.mark.parametrize("gio", [1, 6, 7, 23])
    def test_ngoai_khung_2h_6h(self, gio):
        """6h là giờ KẾT THÚC nên đã ra khỏi khung (nửa khoảng [start, end))."""
        assert in_night_rest(datetime(2026, 9, 23, gio, 0), 2, 6) is False

    @pytest.mark.parametrize("gio", [22, 23, 0, 3, 5])
    def test_khung_vat_qua_nua_dem_22h_6h(self, gio):
        assert in_night_rest(datetime(2026, 9, 23, gio, 0), 22, 6) is True

    @pytest.mark.parametrize("gio", [6, 12, 21])
    def test_ngoai_khung_vat_qua_nua_dem(self, gio):
        assert in_night_rest(datetime(2026, 9, 23, gio, 0), 22, 6) is False

    @pytest.mark.parametrize("gio", [0, 3, 12, 22, 23])
    def test_start_bang_end_nghia_la_tat_tinh_nang(self, gio):
        """Quy ước: start == end KHÔNG có nghĩa là nghỉ 24/24 mà là tắt hẳn."""
        assert in_night_rest(datetime(2026, 9, 23, gio, 0), 0, 0) is False
        assert in_night_rest(datetime(2026, 9, 23, gio, 0), 22, 22) is False

    def test_chi_xet_gio_khong_xet_phut(self):
        assert in_night_rest(datetime(2026, 9, 23, 1, 59), 2, 6) is False
        assert in_night_rest(datetime(2026, 9, 23, 2, 0), 2, 6) is True
