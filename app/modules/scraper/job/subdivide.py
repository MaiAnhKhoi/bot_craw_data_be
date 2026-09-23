"""Biến những dòng "Địa bàn còn sót" thành danh sách truy vấn cho một job mới.

Đây là bước còn thiếu của cả quy trình: trang Địa bàn còn sót chỉ cho NHÌN thấy
tỉnh nào bị Google cắt, còn việc chia nhỏ tỉnh đó ra phường/xã thì người dùng
phải tự gõ lại từng dòng vào form tạo job.

Toàn bộ file này THUẦN dữ liệu — không DB, không mạng, chỉ đọc danh mục địa giới
đã nạp sẵn trong `app.modules.geo`. Tách riêng khỏi `service.py` vì luật tách
chuỗi `<từ khoá> <địa điểm>` là chỗ dễ sai nhất của tính năng, mà nó không cần
Postgres mới kiểm được.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from app.core.text import fold_text
from app.modules.geo import service as geo
from app.modules.scraper.job.repository import CAN_HANH_DONG

# Cấp địa bàn của một truy vấn, từ rộng xuống hẹp.
CAP_QUOC_GIA = "country"
CAP_TINH = "province"
CAP_PHUONG = "ward"

# Việc sẽ làm với một dòng đang chọn.
CHIA_NHO = "subdivide"      # cut_off  -> bung địa bàn xuống cấp dưới
NANG_TRAN = "raise_cap"     # cap      -> chạy lại y nguyên, chỉ khác trần
CHAY_LAI = "retry"          # unknown  -> chạy lại y nguyên
BO_QUA = "skip"             # dòng không còn việc phải làm

# Thời gian trung bình một truy vấn chiếm của worker (mở trang, cuộn hết danh
# sách, bóc từng thẻ). Dùng để báo trước "job này chạy bao lâu" — một tỉnh Việt
# Nam bung ra 168 phường là hơn hai tiếng, người dùng phải biết trước khi bấm.
GIAY_MOI_TRUY_VAN = 40

# Bằng đúng `JobQuery.query` (String(300)). Ghép thêm tên phường/xã vào một truy
# vấn vốn đã dài là có thể vượt qua — và Postgres từ chối cả câu INSERT, tức là
# job không tạo nổi, với một lỗi chẳng nói gì về việc người dùng vừa làm.
DAI_TOI_DA = 300


@dataclass(frozen=True)
class DiaBanTachRa:
    """Một chuỗi truy vấn đã tách lại thành từ khoá + địa bàn.

    `cap is None` nghĩa là KHÔNG tách được, và `ly_do` nói thẳng vì sao — phần
    giải thích tính ngay lúc tách, vì chỉ ở đây mới biết đã nhận ra tới đâu thì
    tắc. Ba ca đều phải báo cho người dùng chứ không được lặng lẽ bỏ qua.
    """

    tu_khoa: str
    dia_diem: str
    cap: str | None
    country: str | None = None
    province: str | None = None
    ward: str | None = None
    ly_do: str | None = None


@dataclass(frozen=True)
class KetQuaBung:
    """Kết quả bung một địa bàn xuống cấp dưới.

    `ly_do` khác None nghĩa là KHÔNG bung được, và chuỗi đó là câu giải thích
    hiện thẳng cho người dùng.
    """

    locations: list[str]
    ly_do: str | None = None


@dataclass(frozen=True)
class TruyVanMoi:
    """Một truy vấn sẽ được ghi vào job mới, kèm ngôn ngữ/quốc gia riêng của nó."""

    query: str
    hl: str
    gl: str


@dataclass(frozen=True)
class MucKeHoach:
    """Một dòng trong bản xem trước: dòng này sinh ra bao nhiêu truy vấn, vì sao."""

    query: str
    stop_reason: str | None
    hanh_dong: str
    tu_khoa: str | None
    dia_diem: str | None
    cap: str | None
    so_truy_van: int
    ly_do: str | None


@dataclass(frozen=True)
class KeHoachChiaNho:
    muc: list[MucKeHoach]
    truy_van: list[TruyVanMoi]
    # Từ khoá và địa điểm đã dùng, để ghi lại trong `params` của job mới.
    tu_khoa: list[str]
    dia_diem: list[str]


class DongConSot(Protocol):
    """Hình dạng tối thiểu của lần quét gần nhất mà bản kế hoạch cần đọc.

    Khai bằng Protocol để hàm thuần dưới đây nhận được cả `Row` của SQLAlchemy
    lẫn object giả trong test.
    """

    stop_reason: str | None
    hl: str
    gl: str


# ---------------------------------------------------------------------------
# Tách chuỗi truy vấn thành từ khoá + địa bàn
# ---------------------------------------------------------------------------
#
# Truy vấn được sinh ra bằng `f"{tu_khoa} {dia_diem}"`, còn địa điểm thì do
# `geo.expand()` ghép từ trong ra ngoài:
#     "Phường Bến Thành, Thành phố Hồ Chí Minh, Việt Nam"
# Nên tên cấp NGOÀI CÙNG (quốc gia) luôn nằm ở đuôi, còn tên cấp trong cùng thì
# DÍNH LIỀN với từ khoá, không có dấu phẩy nào ngăn cách. Vì thế không thể chỉ
# cắt chuỗi theo dấu phẩy: phải dò ngược từ đuôi vào bằng chính danh mục địa
# giới, mỗi cấp một lần.
#
# Dấu phẩy còn nằm ngay bên trong tên riêng ở cả hai phía: 29 tỉnh/bang mang dấu
# phẩy trong tên ISO ("Tatarstan, Respublika"), 15 quốc gia cũng vậy
# ("Korea, Republic of"), và từ khoá do người dùng gõ tay thì muốn có gì cũng
# được. Dò theo TỪ (token) chứ không theo dấu phẩy nên cả ba ca trên đều đúng.


@dataclass(frozen=True)
class BangTra:
    """Bảng tra tên địa bàn, HAI mức: nguyên văn trước, bỏ dấu sau.

    Bỏ dấu là bắt buộc để người dùng gõ tay vẫn khớp, nhưng nó cũng gộp mất
    những cặp tên chỉ khác nhau ở dấu — và cấp phường/xã của Việt Nam có đúng 8
    cặp như vậy ("Xã Văn Lăng" / "Xã Văn Lang", "Xã Ba Tơ" / "Xã Ba Tô"...).
    Chuỗi do bộ chọn sinh ra thì nguyên văn, nên thử nguyên văn trước là trả lại
    đúng đơn vị hành chính cho 100% số ca đó.
    """

    nguyen_van: dict[str, dict]
    bo_dau: dict[str, dict]


@lru_cache(maxsize=1)
def _chi_muc_quoc_gia() -> BangTra:
    """Tên quốc gia -> bản ghi.

    Dựng lại ở đây thay vì mượn bảng tra riêng tư của module geo: đây là module
    khác, và cả hai cùng nhìn vào một danh mục. Bảng chỉ có 249 dòng, dựng một lần.
    """
    return _chi_muc(geo.countries(), ("name", "name_en", "query"))


@lru_cache(maxsize=256)
def _chi_muc_tinh(country: str) -> BangTra:
    return _chi_muc(geo.provinces(country), ("name", "query"))


@lru_cache(maxsize=64)
def _chi_muc_phuong(province: str) -> BangTra:
    return _chi_muc(geo.wards(province), ("name", "query"))


def _chuan(text: str) -> str:
    """Gom khoảng trắng nhưng GIỮ dấu và chữ hoa — khoá của mức tra nguyên văn."""
    return " ".join(text.split())


def _chi_muc(items: Iterable[dict], truong: tuple[str, ...]) -> BangTra:
    nguyen_van: dict[str, dict] = {}
    bo_dau: dict[str, dict] = {}
    for item in items:
        for khoa in truong:
            ten = item.get(khoa)
            if ten:
                nguyen_van.setdefault(_chuan(ten), item)
                bo_dau.setdefault(fold_text(ten), item)
    return BangTra(nguyen_van, bo_dau)


def _khop_duoi(tokens: list[str], chi_muc: BangTra) -> tuple[dict, int] | None:
    """Đoạn ĐUÔI khớp một tên trong bảng tra; trả (bản ghi, vị trí token bắt đầu).

    Thử đuôi DÀI NHẤT trước. Đây là chỗ khác `geo.resolve_country` (nó đọc theo
    dấu phẩy nên lấy đuôi ngắn nhất là đủ), và khác vì một lý do đo được: rất
    nhiều tên địa bàn kết thúc bằng một tên địa bàn khác, mà ở đây không có dấu
    phẩy nào ngăn chúng lại.
        "DR Congo" chứa "Congo"          -> quét nhầm sang Cộng hoà Congo
        "South Sudan" chứa "Sudan"
        "Equatorial Guinea" chứa "Guinea"
        "American Samoa" chứa "Samoa"
        "Ciudad Autónoma de Buenos Aires" chứa "Buenos Aires"
    Lấy đuôi ngắn nhất là nhận nhầm cả 6 ca trên. Chiều ngược lại — từ khoá kết
    thúc đúng bằng mấy chữ đầu của một tên địa bàn dài hơn — thì phải bịa ra mới
    có ("... South" + "Sudan").
    """
    for lay, chuan_hoa in ((chi_muc.nguyen_van, _chuan), (chi_muc.bo_dau, fold_text)):
        for i in range(len(tokens)):
            found = lay.get(chuan_hoa(" ".join(tokens[i:])))
            if found is not None:
                return found, i
    return None


def _cat_dau(tokens: list[str], den: int) -> tuple[str, bool]:
    """Phần đứng TRƯỚC tên địa bàn vừa khớp, kèm cờ "nó có kết thúc bằng dấu phẩy".

    Cái cờ đó là tín hiệu quan trọng nhất còn lại của chuỗi: địa điểm ngăn các
    cấp bằng ", ", nên còn dấu phẩy nghĩa là phía trong còn một cấp nữa mà ta
    chưa nhận ra. Không đọc nó thì "fruit wholesaler Bangkok, Thailand" —
    "Bangkok" không phải tên ISO của tỉnh đó, danh mục ghi "Krung Thep Maha
    Nakhon" — trông y hệt một truy vấn cấp quốc gia với từ khoá "fruit wholesaler
    Bangkok", và cả 78 tỉnh Thái Lan sẽ được quét bằng từ khoá rác đó.
    """
    dau = " ".join(tokens[:den]).strip()
    return dau.rstrip(",").strip(), dau.endswith(",")


LY_DO_KHONG_NHAN_RA = (
    "Không nhận ra địa bàn ở cuối chuỗi truy vấn. Chuỗi này không do bộ chọn địa "
    "giới sinh ra nên không biết chia nhỏ theo cấp nào."
)
LY_DO_THIEU_TU_KHOA = "Chuỗi truy vấn chỉ có tên địa bàn, không tách ra được từ khoá nào."


def _ly_do_la(cap_duoi: str, trong: str) -> str:
    return (
        f"Nhận ra '{trong}' nhưng không nhận ra đơn vị hành chính đứng trước nó "
        f"('{cap_duoi}'). Danh mục dùng tên chuẩn ('Krung Thep Maha Nakhon' chứ "
        "không phải 'Bangkok'), nên chuỗi tự gõ tay phải tạo job thủ công."
    )


def tach_truy_van(query: str) -> DiaBanTachRa:
    """Tách "vựa trái cây Thành phố Cần Thơ, Việt Nam" thành từ khoá + địa bàn."""
    tokens = (query or "").split()
    if not tokens:
        return DiaBanTachRa("", "", None, ly_do=LY_DO_KHONG_NHAN_RA)

    khop = _khop_duoi(tokens, _chi_muc_quoc_gia())
    if khop is None:
        return DiaBanTachRa(" ".join(tokens), "", None, ly_do=LY_DO_KHONG_NHAN_RA)
    quoc_gia, vi_tri = khop
    # Dùng dạng `query` chuẩn của danh mục chứ không cắt lại chuỗi gốc: người
    # dùng có thể đã gõ "Korea, Republic of" trong khi bộ chọn sinh "South Korea".
    ten_nuoc, ma_nuoc = quoc_gia["query"], quoc_gia["code"]
    dau, con_cap_duoi = _cat_dau(tokens, vi_tri)
    if not dau:
        return DiaBanTachRa("", ten_nuoc, None, ma_nuoc, ly_do=LY_DO_THIEU_TU_KHOA)

    tokens_dau = dau.split()
    khop_tinh = _khop_duoi(tokens_dau, _chi_muc_tinh(ma_nuoc))
    if khop_tinh is None:
        # Thiếu dấu phẩy thì đúng là truy vấn cấp quốc gia. Còn dấu phẩy mà không
        # khớp tỉnh nào thì phần giữa là một đơn vị hành chính ta không nhận ra —
        # nuốt nó vào từ khoá là sinh ra hàng chục truy vấn vô nghĩa.
        if con_cap_duoi:
            return DiaBanTachRa("", ten_nuoc, None, ma_nuoc, ly_do=_ly_do_la(dau, ten_nuoc))
        return DiaBanTachRa(dau, ten_nuoc, CAP_QUOC_GIA, ma_nuoc)
    tinh, vi_tri_tinh = khop_tinh
    ma_tinh, ten_tinh = tinh["code"], f"{tinh['query']}, {ten_nuoc}"
    truoc_tinh, con_cap_phuong = _cat_dau(tokens_dau, vi_tri_tinh)
    if not truoc_tinh:
        return DiaBanTachRa("", ten_tinh, None, ma_nuoc, ma_tinh, ly_do=LY_DO_THIEU_TU_KHOA)

    tokens_tinh = truoc_tinh.split()
    khop_phuong = _khop_duoi(tokens_tinh, _chi_muc_phuong(ma_tinh))
    if khop_phuong is None:
        if con_cap_phuong:
            return DiaBanTachRa(
                "", ten_tinh, None, ma_nuoc, ma_tinh, ly_do=_ly_do_la(truoc_tinh, ten_tinh)
            )
        return DiaBanTachRa(truoc_tinh, ten_tinh, CAP_TINH, ma_nuoc, ma_tinh)
    phuong, vi_tri_phuong = khop_phuong
    ma_phuong, ten_phuong = phuong["code"], f"{phuong['query']}, {ten_tinh}"
    tu_khoa, _ = _cat_dau(tokens_tinh, vi_tri_phuong)
    if not tu_khoa:
        return DiaBanTachRa(
            "", ten_phuong, None, ma_nuoc, ma_tinh, ma_phuong, ly_do=LY_DO_THIEU_TU_KHOA
        )
    return DiaBanTachRa(tu_khoa, ten_phuong, CAP_PHUONG, ma_nuoc, ma_tinh, ma_phuong)


# ---------------------------------------------------------------------------
# Bung địa bàn xuống cấp dưới
# ---------------------------------------------------------------------------


def bung_dia_ban(dia_ban: DiaBanTachRa) -> KetQuaBung:
    """Danh sách địa điểm con của địa bàn này, hoặc lý do không bung được.

    Cái bẫy phải chặn ở đây: dữ liệu cấp phường/xã CHỈ có cho Việt Nam (xem chú
    thích đầu `geo/service.py`). Gọi `geo.expand(country="TH", province="TH-10",
    ward=ALL)` không hề báo lỗi — nó trả về đúng một dòng, chính là tỉnh cũ. Cứ
    thế mà tạo job thì người dùng nhận một job y hệt cái vừa chạy, chờ thêm 40
    giây nữa để lại bị Google cắt ở đúng chỗ đó.
    """
    if dia_ban.cap is None:
        return KetQuaBung([], dia_ban.ly_do or LY_DO_KHONG_NHAN_RA)

    if dia_ban.cap == CAP_PHUONG:
        return KetQuaBung(
            [], "Đã ở cấp phường/xã — không còn cấp hành chính nào nhỏ hơn để chia."
        )

    ten_nuoc = geo.country_name(dia_ban.country)
    if dia_ban.cap == CAP_TINH:
        if not geo.wards(dia_ban.province or ""):
            return KetQuaBung(
                [],
                f"Danh mục chỉ có tới cấp tỉnh/bang cho {ten_nuoc} — cấp phường/xã "
                "chỉ Việt Nam mới có. Hãy thu hẹp bằng từ khoá thay vì địa bàn.",
            )
        locations, _ = geo.expand(
            country=dia_ban.country, province=dia_ban.province, ward=geo.ALL
        )
        return KetQuaBung(locations)

    if not geo.provinces(dia_ban.country or ""):
        return KetQuaBung(
            [], f"Danh mục không có đơn vị hành chính cấp dưới nào của {ten_nuoc}."
        )
    locations, _ = geo.expand(country=dia_ban.country, province=geo.ALL)
    return KetQuaBung(locations)


# ---------------------------------------------------------------------------
# Lập kế hoạch cho cả nhóm dòng đang chọn
# ---------------------------------------------------------------------------


def _ngon_ngu(dia_ban: DiaBanTachRa, row: DongConSot) -> tuple[str, str]:
    """(hl, gl) cho các truy vấn sinh ra từ dòng này.

    Nhận ra quốc gia thì tính lại theo luật hiện hành — đúng thứ một job mới sẽ
    làm. Không nhận ra thì giữ nguyên `hl`/`gl` của chính lần chạy trước: chạy
    lại một truy vấn mà đổi `gl` là đổi luôn địa bàn Google tìm, "chạy lại y
    nguyên" sẽ không còn y nguyên nữa.
    """
    if dia_ban.country:
        return geo.locale_for(dia_ban.country)
    return row.hl, row.gl


def _vi_sao_hut(them: int, qua_dai: int, tong: int) -> str | None:
    """Vì sao số truy vấn sinh ra ít hơn số địa bàn con.

    Cả hai ca đều KHÔNG phải lỗi, nhưng đều phải nói ra: một con số hụt mà không
    giải thích thì trông y như hỏng, và người dùng sẽ đi bấm lại lần nữa.
    """
    if qua_dai:
        return (
            f"{qua_dai} địa bàn con bị bỏ vì chuỗi truy vấn dài quá {DAI_TOI_DA} ký "
            "tự — hãy rút ngắn từ khoá rồi tạo job thủ công cho phần đó."
        )
    if them < tong:
        return "Một phần địa bàn con đã do dòng khác trong danh sách này sinh ra."
    return None


def lap_ke_hoach(
    queries: Iterable[str], lan_gan_nhat: Mapping[str, DongConSot]
) -> KeHoachChiaNho:
    """Từ các dòng đang chọn ra danh sách truy vấn của job mới + giải trình từng dòng.

    Lý do dừng lấy từ `lan_gan_nhat` (lần quét thật gần nhất của chuỗi truy vấn
    đó) chứ không tin theo thứ giao diện gửi lên: trang có thể đã mở từ sáng,
    trong khi một job khác vừa quét lại xong chính địa bàn này.
    """
    muc: list[MucKeHoach] = []
    truy_van: list[TruyVanMoi] = []
    da_co: set[str] = set()
    tu_khoa: list[str] = []
    dia_diem: list[str] = []

    def them_truy_van(text: str, hl: str, gl: str) -> int:
        """Thêm một truy vấn, bỏ qua bản trùng. Trả về số dòng thật sự thêm được."""
        if text in da_co:
            return 0
        da_co.add(text)
        truy_van.append(TruyVanMoi(query=text, hl=hl, gl=gl))
        return 1

    def ghi_nho(gia_tri: str, kho: list[str]) -> None:
        if gia_tri and gia_tri not in kho:
            kho.append(gia_tri)

    da_xet: set[str] = set()
    for raw in queries:
        q = (raw or "").strip()
        if not q or q in da_xet:
            continue
        da_xet.add(q)

        row = lan_gan_nhat.get(q)
        if row is None:
            muc.append(
                MucKeHoach(
                    q, None, BO_QUA, None, None, None, 0,
                    "Chưa có lần quét thật nào của truy vấn này — không có gì để chạy lại.",
                )
            )
            continue
        if row.stop_reason not in CAN_HANH_DONG:
            muc.append(
                MucKeHoach(
                    q, row.stop_reason, BO_QUA, None, None, None, 0,
                    f"Lần quét gần nhất dừng vì '{row.stop_reason}', địa bàn này "
                    "không còn việc phải làm.",
                )
            )
            continue

        dia_ban = tach_truy_van(q)
        hl, gl = _ngon_ngu(dia_ban, row)

        if row.stop_reason == "cut_off":
            ket_qua = bung_dia_ban(dia_ban)
            if ket_qua.ly_do:
                muc.append(
                    MucKeHoach(
                        q, row.stop_reason, CHIA_NHO, dia_ban.tu_khoa or None,
                        dia_ban.dia_diem or None, dia_ban.cap, 0, ket_qua.ly_do,
                    )
                )
                continue
            them, qua_dai = 0, 0
            for loc in ket_qua.locations:
                text = f"{dia_ban.tu_khoa} {loc}"
                if len(text) > DAI_TOI_DA:
                    qua_dai += 1
                    continue
                them += them_truy_van(text, hl, gl)
                ghi_nho(loc, dia_diem)
            if them:
                ghi_nho(dia_ban.tu_khoa, tu_khoa)
            muc.append(
                MucKeHoach(
                    q, row.stop_reason, CHIA_NHO, dia_ban.tu_khoa, dia_ban.dia_diem,
                    dia_ban.cap, them,
                    _vi_sao_hut(them, qua_dai, len(ket_qua.locations)),
                )
            )
            continue

        # `cap` và `unknown` chạy lại NGUYÊN VĂN chuỗi cũ: cái sai không nằm ở
        # địa bàn nên chia nhỏ chỉ tốn thêm thời gian.
        them = them_truy_van(q, hl, gl)
        if dia_ban.cap is not None:
            ghi_nho(dia_ban.tu_khoa, tu_khoa)
            ghi_nho(dia_ban.dia_diem, dia_diem)
        else:
            # Không tách được thì giữ cả chuỗi làm "từ khoá": đó đúng là cách
            # `expand_queries` hiểu một job không có địa điểm nào.
            ghi_nho(q, tu_khoa)
        muc.append(
            MucKeHoach(
                q, row.stop_reason,
                NANG_TRAN if row.stop_reason == "cap" else CHAY_LAI,
                dia_ban.tu_khoa or None, dia_ban.dia_diem or None, dia_ban.cap, them,
                None if them else "Truy vấn này đã có trong danh sách sẽ chạy.",
            )
        )

    return KeHoachChiaNho(muc=muc, truy_van=truy_van, tu_khoa=tu_khoa, dia_diem=dia_diem)


def so_phut_uoc_tinh(so_truy_van: int) -> int:
    """Làm tròn LÊN: báo thiếu thời gian còn tệ hơn báo thừa."""
    return -(-so_truy_van * GIAY_MOI_TRUY_VAN // 60)
