"""Luật "chỉ lấy lần chạy gần nhất của mỗi chuỗi truy vấn" của trang Địa bàn còn sót.

Chạy offline hoàn toàn: luật này thuần dữ liệu nên không cần Postgres. Đây cũng
là chỗ dễ sai nhất của tính năng — sai một nước là trang báo "đã quét xong" cho
một tỉnh còn sót cả nghìn doanh nghiệp, mà không ai phát hiện ra được.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.modules.scraper.job.repository import latest_run_per_query, remaining_areas

MOC = datetime(2026, 9, 1, tzinfo=UTC)


@dataclass
class LanChayGia:
    """Đủ trường để hai hàm thuần làm việc — không đụng tới SQLAlchemy."""

    id: int
    query: str
    stop_reason: str | None
    finished_at: datetime | None = None


def lan_chay(id_: int, query: str, stop_reason: str | None, ngay: int | None = 0) -> LanChayGia:
    """`ngay` = số ngày sau mốc; None nghĩa là chưa có `finished_at`."""
    return LanChayGia(
        id=id_,
        query=query,
        stop_reason=stop_reason,
        finished_at=None if ngay is None else MOC + timedelta(days=ngay),
    )


def cac_truy_van(rows) -> list[str]:  # noqa: ANN001
    return [row.query for row in rows]


# ---------- gộp theo truy vấn ----------
def test_chay_lai_thanh_cong_thi_bien_mat_khoi_danh_sach():
    """Lý do tồn tại của cả trang: quét lại Đồng Tháp ra `exhausted` là xong việc,
    dù mười lần trước đều bị Google cắt."""
    rows = [
        lan_chay(1, "vựa trái cây Đồng Tháp", "cut_off", ngay=0),
        lan_chay(2, "vựa trái cây Đồng Tháp", "exhausted", ngay=5),
    ]
    assert remaining_areas(rows) == []


def test_lan_gan_nhat_moi_bi_cat_thi_van_con_sot():
    """Chiều ngược lại: lần cũ trọn vẹn không xoá tội cho lần mới bị cắt."""
    rows = [
        lan_chay(1, "vựa trái cây Đồng Tháp", "exhausted", ngay=0),
        lan_chay(2, "vựa trái cây Đồng Tháp", "cut_off", ngay=5),
    ]
    assert cac_truy_van(remaining_areas(rows)) == ["vựa trái cây Đồng Tháp"]


def test_thu_tu_dong_tra_ve_khong_anh_huong_ket_qua():
    """DB không hứa hẹn thứ tự khi không có ORDER BY — luật phải tự đứng vững."""
    moi = lan_chay(2, "q", "exhausted", ngay=5)
    cu = lan_chay(1, "q", "cut_off", ngay=0)
    assert remaining_areas([moi, cu]) == []
    assert remaining_areas([cu, moi]) == []


def test_moi_truy_van_chi_ra_dung_mot_dong():
    rows = [
        lan_chay(1, "q1", "cut_off", ngay=0),
        lan_chay(2, "q1", "cut_off", ngay=3),
        lan_chay(3, "q2", "cap", ngay=1),
    ]
    con_sot = remaining_areas(rows)
    assert sorted(cac_truy_van(con_sot)) == ["q1", "q2"]
    # Và là dòng của LẦN GẦN NHẤT, không phải lần đầu bắt gặp.
    assert [row.id for row in con_sot if row.query == "q1"] == [2]


# ---------- cái gì được tính là "một lần chạy" ----------
def test_luot_bo_qua_recent_khong_che_mat_lan_bi_cat():
    """Lượt `recent` có `finished_at` mới tinh nhưng KHÔNG hề chạm tới Google.

    Mà cơ chế bỏ qua lại coi `cut_off` là "chạy lại cũng ra y hệt", nên đúng
    những tỉnh còn sót mới hay bị bỏ qua. Tính lượt bỏ qua đó là lần chạy mới
    nhất thì tỉnh còn sót lặng lẽ rơi khỏi danh sách — hỏng nặng hơn là không
    có trang này.
    """
    rows = [
        lan_chay(1, "vựa trái cây Đồng Tháp", "cut_off", ngay=0),
        lan_chay(2, "vựa trái cây Đồng Tháp", "recent", ngay=5),
    ]
    assert cac_truy_van(remaining_areas(rows)) == ["vựa trái cây Đồng Tháp"]
    # Và hiện trạng vẫn là con số của lần quét thật, không phải của lượt bỏ qua.
    assert remaining_areas(rows)[0].id == 1


def test_lan_loi_khong_che_mat_lan_bi_cat():
    """`status = failed` để `stop_reason` NULL. Một lần hỏng không chứng minh
    được địa bàn đã trọn vẹn, nên không được phép xoá dấu vết lần trước."""
    rows = [
        lan_chay(1, "q", "cut_off", ngay=0),
        lan_chay(2, "q", None, ngay=5),
    ]
    assert cac_truy_van(remaining_areas(rows)) == ["q"]


def test_truy_van_dang_cho_trong_job_moi_khong_che_lan_bi_cat():
    """Job vừa tạo: dòng chưa chạy, chưa có `finished_at` lẫn `stop_reason`."""
    rows = [
        lan_chay(1, "q", "cut_off", ngay=0),
        lan_chay(9, "q", None, ngay=None),
    ]
    assert cac_truy_van(remaining_areas(rows)) == ["q"]


def test_khong_co_lan_quet_that_nao_thi_khong_ra_dong_nao():
    rows = [lan_chay(1, "q", None, ngay=None), lan_chay(2, "q", "recent", ngay=1)]
    assert latest_run_per_query(rows) == []
    assert remaining_areas(rows) == []


# ---------- so hai lần chạy ngang ngửa ----------
def test_trung_moc_thoi_gian_thi_lay_dong_moi_hon():
    """Hai job chạy song song có thể ghi trùng mốc tới từng micro giây; `id` lớn
    hơn là dòng sinh sau."""
    rows = [
        lan_chay(7, "q", "cut_off", ngay=2),
        lan_chay(8, "q", "exhausted", ngay=2),
    ]
    assert remaining_areas(rows) == []


def test_dong_chua_co_moc_thoi_gian_luon_thua_dong_da_co():
    """`id` lớn hơn nhưng chưa chạy xong thì vẫn không phải "lần gần nhất" — và
    so None với datetime phải không được ném TypeError."""
    rows = [
        lan_chay(1, "q", "cut_off", ngay=3),
        lan_chay(99, "q", "exhausted", ngay=None),
    ]
    assert cac_truy_van(remaining_areas(rows)) == ["q"]


def test_hai_dong_deu_chua_co_moc_thoi_gian_van_so_duoc():
    rows = [
        lan_chay(1, "q", "exhausted", ngay=None),
        lan_chay(2, "q", "cut_off", ngay=None),
    ]
    assert cac_truy_van(remaining_areas(rows)) == ["q"]


# ---------- nhóm cần hành động + bộ lọc ----------
def test_da_quet_het_va_khong_co_gi_deu_khong_phai_viec_phai_lam():
    rows = [lan_chay(1, "q1", "exhausted", ngay=1), lan_chay(2, "q2", "empty", ngay=1)]
    assert remaining_areas(rows) == []


def test_ba_ly_do_can_hanh_dong_deu_duoc_liet_ke():
    rows = [
        lan_chay(1, "q1", "cut_off", ngay=1),
        lan_chay(2, "q2", "cap", ngay=1),
        lan_chay(3, "q3", "unknown", ngay=1),
    ]
    assert sorted(cac_truy_van(remaining_areas(rows))) == ["q1", "q2", "q3"]


def test_bo_loc_ap_dung_sau_khi_gop_chu_khong_phai_truoc():
    """Lọc trước rồi mới gộp sẽ lôi lại lần `cut_off` cũ của một truy vấn mà
    hiện trạng đã là `cap` — người dùng đi chia nhỏ địa bàn trong khi việc thật
    sự cần làm chỉ là nâng trần."""
    rows = [
        lan_chay(1, "q", "cut_off", ngay=0),
        lan_chay(2, "q", "cap", ngay=5),
    ]
    assert remaining_areas(rows, stop_reason="cut_off") == []
    assert cac_truy_van(remaining_areas(rows, stop_reason="cap")) == ["q"]


def test_bo_loc_khong_mo_rong_ra_ngoai_nhom_can_hanh_dong():
    """Hỏi `exhausted` ở đây là hỏi sai câu; không được trả về địa bàn đã xong."""
    rows = [lan_chay(1, "q", "exhausted", ngay=1), lan_chay(2, "q2", "cut_off", ngay=1)]
    assert remaining_areas(rows, stop_reason="exhausted") == []


# ---------- thứ tự hiển thị ----------
def test_xep_lan_quet_moi_nhat_len_dau():
    """Quét xong một lượt 34 tỉnh thì việc cần làm ngay nằm ở chính lượt vừa chạy."""
    rows = [
        lan_chay(1, "cũ", "cut_off", ngay=0),
        lan_chay(2, "mới nhất", "cap", ngay=9),
        lan_chay(3, "giữa", "unknown", ngay=4),
    ]
    assert cac_truy_van(remaining_areas(rows)) == ["mới nhất", "giữa", "cũ"]
