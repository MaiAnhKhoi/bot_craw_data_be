"""Tách chuỗi truy vấn thành "từ khoá + địa bàn", rồi bung địa bàn xuống cấp dưới.

Chạy offline hoàn toàn: cả hai việc này chỉ đọc danh mục địa giới đã nạp sẵn, nên
không cần Postgres. Đây cũng là chỗ dễ sai nhất của nút "chia nhỏ" — sai một
nước là người dùng đặt lệnh chạy hàng trăm truy vấn cho SAI địa bàn, và job vẫn
chạy trơn tru suốt đêm chứ không có lỗi nào báo.

Dấu phẩy là cái bẫy chính, vì nó nằm ở CẢ BA phía:
  * địa điểm do `geo.expand()` sinh ra có 2-3 đoạn ngăn bằng dấu phẩy;
  * 29 tỉnh/bang và 15 quốc gia mang sẵn dấu phẩy trong tên ISO;
  * từ khoá thì do người dùng gõ, muốn có gì cũng được.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from app.core.exceptions import AppError
from app.modules.geo import service as geo
from app.modules.scraper.job import service as job_service
from app.modules.scraper.job.request import RemainingSplitRequest
from app.modules.scraper.job.subdivide import (
    BO_QUA,
    CAP_PHUONG,
    CAP_QUOC_GIA,
    CAP_TINH,
    CHAY_LAI,
    CHIA_NHO,
    NANG_TRAN,
    bung_dia_ban,
    lap_ke_hoach,
    so_phut_uoc_tinh,
    tach_truy_van,
)

KW = "vựa trái cây"


# =====================================================================
# Tách chuỗi truy vấn
# =====================================================================
def test_cap_tinh_la_ca_hay_gap_nhat():
    """Quy trình đúng là quét 34 tỉnh trước, nên dòng bị cắt gần như luôn ở cấp này."""
    ra = tach_truy_van(f"{KW} Thành phố Cần Thơ, Việt Nam")

    assert ra.tu_khoa == KW
    assert ra.dia_diem == "Thành phố Cần Thơ, Việt Nam"
    assert (ra.cap, ra.country, ra.province) == (CAP_TINH, "VN", "VN-92")


def test_cap_quoc_gia_khong_co_dau_phay_nao_de_cat():
    """Tên quốc gia DÍNH LIỀN từ khoá — cắt theo dấu phẩy là không tách được gì."""
    ra = tach_truy_van("fruit wholesaler Thailand")

    assert (ra.tu_khoa, ra.cap, ra.country) == ("fruit wholesaler", CAP_QUOC_GIA, "TH")


def test_cap_phuong_xa():
    ra = tach_truy_van(f"{KW} Phường Bến Thành, Thành phố Hồ Chí Minh, Việt Nam")

    assert ra.tu_khoa == KW
    assert (ra.cap, ra.province) == (CAP_PHUONG, "VN-79")
    assert ra.ward is not None


def test_ten_tinh_co_dau_phay_ben_trong():
    """29 tỉnh/bang mang dấu phẩy trong tên ISO. Cắt chuỗi theo dấu phẩy thì
    "Tatarstan, Respublika" gãy làm đôi và tỉnh biến mất."""
    ra = tach_truy_van("fruit wholesaler Tatarstan, Respublika, Russia")

    assert ra.tu_khoa == "fruit wholesaler"
    assert (ra.cap, ra.country, ra.province) == (CAP_TINH, "RU", "RU-TA")


def test_ten_quoc_gia_co_dau_phay_ben_trong():
    """Người dùng gõ tên ISO ("Korea, Republic of") thay cho dạng bộ chọn sinh ra."""
    ra = tach_truy_van("fruit wholesaler Korea, Republic of")

    assert (ra.tu_khoa, ra.cap, ra.country) == ("fruit wholesaler", CAP_QUOC_GIA, "KR")
    # Và địa bàn trả về ở dạng CHUẨN của danh mục, không phải dạng người dùng gõ.
    assert ra.dia_diem == "South Korea"


def test_tu_khoa_co_dau_phay_ben_trong():
    """Từ khoá do người dùng gõ; không có luật nào cấm dấu phẩy trong đó."""
    ra = tach_truy_van("vựa trái cây, rau củ Thành phố Cần Thơ, Việt Nam")

    assert ra.tu_khoa == "vựa trái cây, rau củ"
    assert (ra.cap, ra.province) == (CAP_TINH, "VN-92")


def test_tu_khoa_co_dau_phay_o_ca_truy_van_cap_phuong():
    ra = tach_truy_van(
        "vựa trái cây, rau củ Phường Bến Thành, Thành phố Hồ Chí Minh, Việt Nam"
    )

    assert ra.tu_khoa == "vựa trái cây, rau củ"
    assert ra.cap == CAP_PHUONG


@pytest.mark.parametrize(
    ("truy_van", "cho_doi"),
    [
        ("fruit wholesaler DR Congo", "CD"),          # "Congo" cũng là một quốc gia
        ("fruit wholesaler South Sudan", "SS"),       # chứa "Sudan"
        ("fruit wholesaler Equatorial Guinea", "GQ"),  # chứa "Guinea"
        ("fruit wholesaler American Samoa", "AS"),    # chứa "Samoa"
    ],
)
def test_ten_nuoc_ket_thuc_bang_ten_mot_nuoc_khac(truy_van: str, cho_doi: str):
    """Phải lấy ĐUÔI DÀI NHẤT. Lấy đuôi ngắn nhất là quét Cộng hoà Congo trong khi
    người dùng chọn Cộng hoà Dân chủ Congo — cách nhau nửa châu lục."""
    ra = tach_truy_van(truy_van)

    assert (ra.country, ra.tu_khoa) == (cho_doi, "fruit wholesaler")


def test_ten_tinh_ket_thuc_bang_ten_mot_tinh_khac():
    """"Ciudad Autónoma de Buenos Aires" (thủ đô) chứa "Buenos Aires" (tỉnh)."""
    ra = tach_truy_van("fruit wholesaler Ciudad Autónoma de Buenos Aires, Argentina")

    assert ra.tu_khoa == "fruit wholesaler"
    assert ra.province == "AR-C"


def test_hai_phuong_chi_khac_nhau_o_dau():
    """Bỏ dấu là bắt buộc để gõ tay vẫn khớp, nhưng nó gộp mất 8 cặp phường/xã chỉ
    khác nhau ở dấu. Chuỗi do bộ chọn sinh ra thì nguyên văn nên phải trả đúng cái."""
    lang = tach_truy_van(f"{KW} Xã Văn Lang, Tỉnh Thái Nguyên, Việt Nam")
    lang_huyen = tach_truy_van(f"{KW} Xã Văn Lăng, Tỉnh Thái Nguyên, Việt Nam")

    assert lang.ward != lang_huyen.ward
    assert lang.tu_khoa == lang_huyen.tu_khoa == KW


def test_go_khong_dau_van_khop():
    """Người dùng dán lại từ một file Excel đã mất dấu."""
    ra = tach_truy_van("vua trai cay Thanh pho Can Tho, Viet Nam")

    assert (ra.cap, ra.country, ra.province) == (CAP_TINH, "VN", "VN-92")


def test_thua_khoang_trang_khong_lam_hong_ket_qua():
    ra = tach_truy_van(f"  {KW}   Thành phố Cần Thơ,   Việt Nam  ")

    assert (ra.tu_khoa, ra.province) == (KW, "VN-92")


def test_nhan_ra_nuoc_nhung_khong_nhan_ra_tinh_thi_coi_nhu_that_bai():
    """Danh mục ghi tên ISO 'Krung Thep Maha Nakhon', người dùng gõ 'Bangkok'.

    Đây là ca nguy hiểm nhất vì nó KHÔNG trông giống lỗi: bỏ qua dấu phẩy đứng
    trước tên nước thì chuỗi này thành "truy vấn cấp quốc gia, từ khoá 'fruit
    wholesaler Bangkok'", và cả 78 tỉnh Thái Lan được quét bằng từ khoá rác đó —
    gần một tiếng chạy để ghi về dữ liệu không ai hỏi.
    """
    ra = tach_truy_van("fruit wholesaler Bangkok, Thailand")

    assert ra.cap is None
    assert ra.country == "TH"
    assert ra.ly_do and "Bangkok" in ra.ly_do


def test_thieu_dau_phay_thi_van_hieu_la_cap_tinh():
    """Chiều ngược lại: nhận ra tên tỉnh rồi thì thiếu dấu phẩy cũng không sao —
    Google đọc chuỗi chứ không đọc dấu câu."""
    ra = tach_truy_van(f"{KW} Thành phố Cần Thơ Việt Nam")

    assert (ra.cap, ra.province) == (CAP_TINH, "VN-92")
    assert ra.tu_khoa == KW


def test_khong_nhan_ra_dia_ban_thi_noi_thang():
    """Người dùng tự gõ "Quận 1" — không khớp danh mục nào. Đoán bừa ở đây là đi
    chia nhỏ một địa bàn khác hẳn thứ họ định quét."""
    ra = tach_truy_van(f"{KW} Quận 1")

    assert ra.cap is None
    assert ra.country is None
    assert ra.tu_khoa == f"{KW} Quận 1"


def test_truy_van_chi_co_ten_dia_ban_thi_khong_co_tu_khoa():
    ra = tach_truy_van("Việt Nam")

    assert (ra.cap, ra.tu_khoa, ra.country) == (None, "", "VN")


def test_chuoi_rong():
    ra = tach_truy_van("   ")

    assert (ra.cap, ra.tu_khoa, ra.country) == (None, "", None)


# ---------- phép thử quyết định: đi vòng qua CẢ danh mục ----------
def cac_truy_van_mau():
    """Mọi chuỗi địa điểm mà `geo.expand()` có thể sinh ra, gắn thêm một từ khoá."""
    data = geo.load()
    for c in data.countries:
        yield f"{KW} {c['query']}", c["code"], None, None
        for p in data.provinces.get(c["code"], []):
            yield f"{KW} {p['query']}, {c['query']}", c["code"], p["code"], None
            for w in data.wards.get(p["code"], []):
                yield (
                    f"{KW} {w['query']}, {p['query']}, {c['query']}",
                    c["code"], p["code"], w["code"],
                )


def test_moi_chuoi_dia_diem_trong_danh_muc_deu_tach_lai_dung():
    """7.131 chuỗi — toàn bộ quốc gia, tỉnh/bang và phường/xã trong danh mục.

    Danh mục là dữ liệu THẬT và mỗi năm đổi một lần (địa giới 01/07/2025 vừa đổi
    xong). Vòng này bắt được cả va chạm tên mới lẫn lỗi luật, chứ mươi ca viết tay
    thì chỉ bắt được thứ mình đã nghĩ ra.
    """
    ten_tinh = {p["code"]: p["query"] for lst in geo.load().provinces.values() for p in lst}
    ten_phuong = {w["code"]: w["query"] for lst in geo.load().wards.values() for w in lst}

    hong: list[tuple] = []
    so_ca = 0
    for truy_van, cc, pc, wc in cac_truy_van_mau():
        so_ca += 1
        ra = tach_truy_van(truy_van)
        cap_cho_doi = CAP_PHUONG if wc else (CAP_TINH if pc else CAP_QUOC_GIA)
        # So theo TÊN chứ không theo mã ở cấp tỉnh: vài nước có hai đơn vị trùng
        # tên (thành phố và vùng cùng gọi "Lənkəran"), mà chuỗi gửi cho Google
        # thì giống hệt nhau nên trả về mã nào cũng quét ra đúng chỗ đó.
        dung = (
            ra.tu_khoa == KW
            and ra.cap == cap_cho_doi
            and ra.country == cc
            and (pc is None or ten_tinh.get(ra.province) == ten_tinh[pc])
            and (wc is None or ten_phuong.get(ra.ward) == ten_phuong[wc])
        )
        if not dung:
            hong.append((truy_van, ra))

    assert so_ca > 7_000, "danh mục hụt mất dữ liệu — vòng kiểm này thành vô nghĩa"
    assert hong == []


# =====================================================================
# Bung địa bàn xuống cấp dưới
# =====================================================================
def test_tinh_viet_nam_bung_xuong_phuong_xa():
    ket_qua = bung_dia_ban(tach_truy_van(f"{KW} Thành phố Hồ Chí Minh, Việt Nam"))

    assert ket_qua.ly_do is None
    assert len(ket_qua.locations) == 168
    assert all(loc.endswith(", Thành phố Hồ Chí Minh, Việt Nam") for loc in ket_qua.locations)


def test_tinh_nuoc_ngoai_khong_bung_duoc_va_phai_noi_ro():
    """Cái bẫy chính: `geo.expand(country="TH", province=..., ward=ALL)` KHÔNG báo
    lỗi — nó trả về đúng một dòng, chính là tỉnh cũ. Cứ thế mà tạo job là người
    dùng nhận một job y hệt cái vừa chạy, chờ thêm 40 giây để lại bị cắt."""
    ket_qua = bung_dia_ban(tach_truy_van("fruit wholesaler Krung Thep Maha Nakhon, Thailand"))

    assert ket_qua.locations == []
    assert ket_qua.ly_do and "phường/xã" in ket_qua.ly_do
    # Và đúng là geo sẽ trả lại chính tỉnh đó nếu không chặn ở đây.
    assert geo.expand(country="TH", province="TH-10", ward=geo.ALL)[1] == 1


def test_cap_quoc_gia_bung_xuong_tinh():
    ket_qua = bung_dia_ban(tach_truy_van(f"{KW} Việt Nam"))

    assert ket_qua.ly_do is None
    assert len(ket_qua.locations) == 34
    assert all(loc.endswith(", Việt Nam") for loc in ket_qua.locations)


def test_da_o_cap_phuong_thi_het_duong_chia():
    ket_qua = bung_dia_ban(
        tach_truy_van(f"{KW} Phường Bến Thành, Thành phố Hồ Chí Minh, Việt Nam")
    )

    assert ket_qua.locations == []
    assert ket_qua.ly_do and "phường/xã" in ket_qua.ly_do


def test_quoc_gia_khong_co_cap_duoi_nao():
    """49 quốc gia/vùng lãnh thổ trong danh mục không có đơn vị cấp tỉnh."""
    khong_co = next(c for c in geo.countries() if c["levels"] == 1)
    ket_qua = bung_dia_ban(tach_truy_van(f"fruit wholesaler {khong_co['query']}"))

    assert ket_qua.locations == []
    assert ket_qua.ly_do is not None


def test_khong_nhan_ra_dia_ban_thi_bao_ly_do_rieng():
    """Hai kiểu "không chia được" khác nhau thì lời nhắn cũng phải khác nhau."""
    la = bung_dia_ban(tach_truy_van(f"{KW} Quận 1"))
    thieu_tu_khoa = bung_dia_ban(tach_truy_van("Việt Nam"))

    assert la.ly_do and "Không nhận ra địa bàn" in la.ly_do
    assert thieu_tu_khoa.ly_do and "từ khoá" in thieu_tu_khoa.ly_do


# =====================================================================
# Lập kế hoạch cho cả nhóm dòng đang chọn
# =====================================================================
@dataclass
class DongGia:
    """Lần quét gần nhất của một chuỗi truy vấn — đủ trường cho hàm thuần."""

    stop_reason: str | None
    hl: str = "vi"
    gl: str = "vn"


def ke_hoach(cap_dong: dict[str, DongGia], thu_tu: list[str] | None = None):  # noqa: ANN201
    return lap_ke_hoach(thu_tu if thu_tu is not None else list(cap_dong), cap_dong)


def test_cut_off_thi_chia_nho_dia_ban():
    q = f"{KW} Thành phố Hồ Chí Minh, Việt Nam"
    kq = ke_hoach({q: DongGia("cut_off")})

    assert kq.muc[0].hanh_dong == CHIA_NHO
    assert kq.muc[0].so_truy_van == 168
    assert len(kq.truy_van) == 168
    assert kq.truy_van[0].query.startswith(f"{KW} Phường ")


def test_cap_chi_chay_lai_nguyen_van_chu_khong_chia_nho():
    """Dừng vì chạm trần của CHÍNH người dùng — địa bàn không có lỗi gì, chia nhỏ
    nó ra 168 truy vấn chỉ tốn thêm hai tiếng."""
    q = f"{KW} Thành phố Hồ Chí Minh, Việt Nam"
    kq = ke_hoach({q: DongGia("cap")})

    assert kq.muc[0].hanh_dong == NANG_TRAN
    assert [t.query for t in kq.truy_van] == [q]


def test_unknown_chay_lai_nguyen_van():
    q = f"{KW} Thành phố Cần Thơ, Việt Nam"
    kq = ke_hoach({q: DongGia("unknown")})

    assert kq.muc[0].hanh_dong == CHAY_LAI
    assert [t.query for t in kq.truy_van] == [q]


def test_dong_da_duoc_chay_lai_thanh_cong_thi_bo_qua():
    """Trang có thể đã mở từ sáng. Tin theo `stop_reason` giao diện gửi lên là đi
    bung một tỉnh đã trọn vẹn thành 168 truy vấn thừa."""
    q = f"{KW} Thành phố Hồ Chí Minh, Việt Nam"
    kq = ke_hoach({q: DongGia("exhausted")})

    assert kq.muc[0].hanh_dong == BO_QUA
    assert kq.truy_van == []
    assert kq.muc[0].ly_do is not None


def test_truy_van_chua_tung_chay_thi_bo_qua():
    kq = lap_ke_hoach([f"{KW} Thành phố Cần Thơ, Việt Nam"], {})

    assert kq.muc[0].hanh_dong == BO_QUA
    assert kq.truy_van == []


@pytest.mark.parametrize(
    "truy_van",
    [
        "fruit wholesaler Krung Thep Maha Nakhon, Thailand",  # nước ngoài: hết cấp
        "fruit wholesaler Bangkok, Thailand",                 # không nhận ra tỉnh
        f"{KW} Phường Bến Thành, Thành phố Hồ Chí Minh, Việt Nam",  # đã nhỏ nhất
        f"{KW} Quận 1",                                       # không nhận ra gì
    ],
)
def test_cut_off_khong_bung_duoc_thi_bao_chu_khong_tao_job_y_het(truy_van: str):
    """Đúng yêu cầu nghiệp vụ: thà không sinh dòng nào còn hơn âm thầm sinh lại
    chính truy vấn vừa bị cắt."""
    kq = ke_hoach({truy_van: DongGia("cut_off", hl="en", gl="th")})

    assert kq.muc[0].so_truy_van == 0
    assert kq.muc[0].ly_do is not None
    assert kq.truy_van == []


def test_khong_sinh_truy_van_trung_nhau():
    """Chọn cả tỉnh (cut_off) lẫn một phường của nó (unknown): phường đó nằm trong
    phần bung ra của tỉnh. Trùng khoá (job_id, query) là job tạo không nổi."""
    tinh = f"{KW} Thành phố Hồ Chí Minh, Việt Nam"
    phuong = f"{KW} Phường Bến Thành, Thành phố Hồ Chí Minh, Việt Nam"
    kq = ke_hoach({tinh: DongGia("cut_off"), phuong: DongGia("unknown")}, [tinh, phuong])

    assert len({t.query for t in kq.truy_van}) == len(kq.truy_van) == 168
    # Dòng bị nuốt vẫn phải giải thích, nếu không con số 0 trông như hỏng.
    assert kq.muc[1].so_truy_van == 0
    assert kq.muc[1].ly_do is not None


def test_truy_van_ghep_ra_qua_dai_thi_bo_va_bao():
    """`job_queries.query` là varchar(300). Ghép tên phường vào một truy vấn vốn
    đã dài là Postgres từ chối cả câu INSERT — job không tạo nổi, và thông điệp
    lỗi chẳng nói gì về việc người dùng vừa bấm."""
    dai = "x" * 290
    q = f"{dai} Thành phố Hồ Chí Minh, Việt Nam"
    kq = ke_hoach({q: DongGia("cut_off")})

    assert kq.truy_van == []
    assert kq.muc[0].ly_do and "300 ký tự" in kq.muc[0].ly_do
    # Và không được ghi từ khoá rác đó vào params của job.
    assert kq.tu_khoa == []


def test_dong_trung_nhau_trong_cung_mot_lan_chon_chi_tinh_mot():
    q = f"{KW} Thành phố Cần Thơ, Việt Nam"
    kq = lap_ke_hoach([q, q, "  "], {q: DongGia("unknown")})

    assert len(kq.muc) == 1
    assert len(kq.truy_van) == 1


def test_ngon_ngu_tinh_lai_theo_quoc_gia_cua_dia_ban():
    """Lần chạy trước để `gl=vn` cho một địa bàn Thái Lan (mặc định của job cũ) —
    chạy lại y như vậy là lặp lại đúng cái sai đã đo được: "fruit wholesaler
    Bangkok" với gl=vn ra cửa hàng ở TP.HCM."""
    q = "fruit wholesaler Bangkok, Thailand"
    kq = ke_hoach({q: DongGia("unknown", hl="vi", gl="vn")})

    assert (kq.truy_van[0].hl, kq.truy_van[0].gl) == ("en", "th")


def test_khong_nhan_ra_dia_ban_thi_giu_nguyen_ngon_ngu_lan_truoc():
    """Không biết địa bàn ở đâu thì đổi `gl` là đổi luôn địa bàn Google tìm —
    "chạy lại y nguyên" sẽ không còn y nguyên nữa."""
    q = "fruit wholesaler Quận 1"
    kq = ke_hoach({q: DongGia("unknown", hl="en", gl="th")})

    assert (kq.truy_van[0].hl, kq.truy_van[0].gl) == ("en", "th")


def test_ghi_lai_tu_khoa_va_dia_diem_de_dung_cho_params():
    q = f"{KW} Thành phố Cần Thơ, Việt Nam"
    kq = ke_hoach({q: DongGia("cut_off")})

    assert kq.tu_khoa == [KW]
    assert kq.dia_diem == kq.dia_diem[: len(kq.dia_diem)] and len(kq.dia_diem) > 1


def test_dong_khong_tach_duoc_van_gop_du_tu_khoa_cho_params():
    """`JobCreateRequest` đòi tối thiểu một từ khoá. Dòng không tách được mà không
    góp gì thì job toàn dòng như vậy sẽ chết ở tầng validate với một thông điệp
    chẳng liên quan gì tới việc người dùng vừa làm."""
    q = f"{KW} Quận 1"
    kq = ke_hoach({q: DongGia("unknown")})

    assert kq.truy_van and kq.tu_khoa == [q]
    assert kq.dia_diem == []


def test_uoc_tinh_thoi_gian_lam_tron_len():
    """Báo thiếu thời gian còn tệ hơn báo thừa."""
    assert so_phut_uoc_tinh(0) == 0
    assert so_phut_uoc_tinh(1) == 1        # 40 giây -> 1 phút
    assert so_phut_uoc_tinh(3) == 2        # 120 giây -> đúng 2 phút
    assert so_phut_uoc_tinh(168) == 112    # một tỉnh Việt Nam: gần hai tiếng


# =====================================================================
# Ghép vào job mới
# =====================================================================
class RepoGia:
    """Repository giả — chặn đúng hai việc ghi mà service thực hiện."""

    def __init__(self, lan_gan_nhat: dict[str, DongGia]) -> None:
        self._lan_gan_nhat = lan_gan_nhat
        self.da_ghi: list = []

    def latest_runs_for(self, queries):  # noqa: ANN001, ANN201
        return {q: r for q, r in self._lan_gan_nhat.items() if q in set(queries)}

    def add(self, job):  # noqa: ANN001, ANN201
        # Những cột có `default=` chỉ được điền lúc flush; test không có DB nên
        # phải tự điền, nếu không `JobResponse` ném lỗi ép kiểu chứ không phải lỗi thật.
        job.id = 1
        for cot in ("done_queries", "total_places", "done_places", "failed_places",
                    "new_places", "blocked_count"):
            setattr(job, cot, 0)
        job.created_at = job.updated_at = datetime(2026, 9, 23, tzinfo=UTC)
        self.job = job
        return job

    def add_queries(self, job_id: int, queries) -> int:  # noqa: ANN001
        self.da_ghi = list(queries)
        return len(self.da_ghi)


class DbGia:
    def commit(self) -> None: ...
    def refresh(self, obj) -> None: ...  # noqa: ANN001


def dung_service(monkeypatch, lan_gan_nhat: dict[str, DongGia]) -> tuple:
    repo = RepoGia(lan_gan_nhat)
    monkeypatch.setattr(job_service, "JobRepository", lambda db: repo)  # noqa: ARG005
    return job_service.JobService(DbGia()), repo


def test_job_moi_luon_tat_co_che_bo_qua_truy_van(monkeypatch):
    """Điều kiện SỐNG CÒN: `CO_THE_BO_QUA` có cả `cut_off`, nên để mặc định True
    thì chính những truy vấn vừa sinh ra ở đây bị bỏ qua sạch — job chạy xong
    trong vài giây, báo "hoàn tất", và không quét được gì."""
    q = f"{KW} Thành phố Cần Thơ, Việt Nam"
    service, repo = dung_service(monkeypatch, {q: DongGia("cut_off")})

    job = service.create_split(RemainingSplitRequest(queries=[q], max_results_per_query=300))

    assert job.params["skip_recent_queries"] is False
    assert job.params["max_results_per_query"] == 300
    assert job.total_queries == len(repo.da_ghi) > 1


def test_job_moi_ghi_dung_nhung_truy_van_da_xem_truoc(monkeypatch):
    """Xem trước và tạo phải ra cùng một danh sách — nếu không thì con số hiện ra
    trước khi bấm chẳng còn nghĩa gì."""
    q = f"{KW} Thành phố Cần Thơ, Việt Nam"
    service, repo = dung_service(monkeypatch, {q: DongGia("cut_off")})

    xem_truoc = service.plan_split(RemainingSplitRequest(queries=[q]))
    service.create_split(RemainingSplitRequest(queries=[q]))

    assert xem_truoc.total_queries == len(repo.da_ghi)
    assert all(t.hl == "vi" and t.gl == "vn" for t in repo.da_ghi)


def test_khong_sinh_duoc_truy_van_nao_thi_bao_loi_chu_khong_tao_job_rong(monkeypatch):
    q = f"{KW} Thành phố Cần Thơ, Việt Nam"
    service, _ = dung_service(monkeypatch, {q: DongGia("exhausted")})

    with pytest.raises(AppError) as loi:
        service.create_split(RemainingSplitRequest(queries=[q]))

    assert loi.value.status_code == 422


def test_ten_bo_trong_thi_tu_dat_theo_ngay_gio(monkeypatch):
    q = f"{KW} Thành phố Cần Thơ, Việt Nam"
    service, _ = dung_service(monkeypatch, {q: DongGia("unknown")})

    job = service.create_split(RemainingSplitRequest(queries=[q]))

    assert job.name.startswith("Chia nhỏ địa bàn còn sót")
