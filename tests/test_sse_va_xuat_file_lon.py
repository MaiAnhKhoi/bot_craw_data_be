"""Bốn cái bẫy chỉ lộ ra khi bảng đã lớn hoặc máy đã chạy qua nửa đêm.

Chạy THUẦN: không DB, không mạng. Session/repository đều được thay bằng bản giả,
còn câu lệnh SQL thì chỉ được DỊCH ra để đọc chứ không gửi đi đâu — đúng tinh thần
của bộ test này: những lỗi dưới đây phải bắt được trên máy chưa dựng hạ tầng, chứ
không phải đợi tới lúc bảng đủ 65.535 dòng ngoài production.
"""
from __future__ import annotations

import asyncio
import csv
import math
import threading
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.dialects import postgresql

from app.core.config import get_settings
from app.modules.scraper.place import router as place_router
from app.modules.scraper.place.entity import Place
from app.modules.scraper.place.export import HEADERS, export_csv
from app.modules.scraper.place.repository import KEYWORD_BATCH, PlaceFilter, PlaceRepository
from app.modules.stats import router as stats_router

# Trần cứng của giao thức PostgreSQL: số tham số của một câu lệnh đếm bằng int16.
TRAN_THAM_SO = 65_535


# =====================================================================
# Lỗi 3 — `keywords_for` với số id vượt trần tham số của PostgreSQL
# =====================================================================
class SessionGiaGhiLaiIn:
    """Session giả: ghi lại danh sách id của TỪNG câu `IN (...)` đã chạy."""

    def __init__(self, tu_khoa: dict[int, list[str]] | None = None) -> None:
        self.tu_khoa = tu_khoa or {}
        self.cac_lo: list[list[int]] = []

    def execute(self, stmt):  # noqa: ANN001, ANN201
        # Lấy tham số đúng như lúc gửi xuống Postgres. SQLAlchemy 2.0 dựng `IN` kiểu
        # "expanding" (__[POSTCOMPILE_...]) nên nhìn chuỗi SQL thì không thấy được
        # bao nhiêu tham số; phải đọc từ `compile().params`.
        params = stmt.compile(dialect=postgresql.dialect()).params
        ids = next(v for v in params.values() if isinstance(v, list))
        self.cac_lo.append(list(ids))
        return KetQuaGia([(pid, kw) for pid in ids for kw in self.tu_khoa.get(pid, [])])


class KetQuaGia:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return self._rows

    def scalar_one(self):  # noqa: ANN201
        return self._rows[0][0] if self._rows else 0


def test_lo_khong_bao_gio_cham_tran_tham_so():
    """100.000 dòng là hợp lệ về nghiệp vụ (export_max_rows mặc định), nên câu
    `IN (...)` phải tự chia — không thì đúng ở dòng thứ 65.536 là 500."""
    so_id = 100_000
    db = SessionGiaGhiLaiIn()
    PlaceRepository(db).keywords_for(range(so_id))

    assert db.cac_lo, "phải có ít nhất một câu truy vấn"
    for lo in db.cac_lo:
        assert len(lo) <= KEYWORD_BATCH
        assert len(lo) < TRAN_THAM_SO


def test_chia_lo_khong_lam_rot_id_nao():
    """Chia lô mà sót id thì hậu quả không phải lỗi, mà là ô trống trong file xuất."""
    so_id = 100_000
    db = SessionGiaGhiLaiIn()
    PlaceRepository(db).keywords_for(range(so_id))

    gop = [pid for lo in db.cac_lo for pid in lo]
    assert gop == list(range(so_id))               # đủ, đúng thứ tự, không trùng
    assert len(db.cac_lo) == math.ceil(so_id / KEYWORD_BATCH)


def test_vuot_tran_van_ra_dung_ket_qua_nhu_khi_chua_chia():
    """Số id đặt VƯỢT HẲN 65.535 — đây chính là trường hợp trước đây ném
    `number of parameters must be between 0 and 65535`."""
    so_id = TRAN_THAM_SO + 10
    tu_khoa = {0: ["xoai", "chuoi"], so_id - 1: ["mit"], KEYWORD_BATCH: ["sau rieng"]}
    db = SessionGiaGhiLaiIn(tu_khoa)

    ket_qua = PlaceRepository(db).keywords_for(range(so_id))

    assert len(ket_qua) == so_id
    assert ket_qua[0] == ["xoai", "chuoi"]
    assert ket_qua[so_id - 1] == ["mit"]
    # Id nằm ngay ranh giới lô phải được tra đúng chứ không rơi vào khe.
    assert ket_qua[KEYWORD_BATCH] == ["sau rieng"]
    # Địa điểm chưa gắn từ khoá vẫn phải có khoá, với danh sách rỗng.
    assert ket_qua[1] == []


def test_khong_co_id_thi_khong_chay_cau_nao():
    db = SessionGiaGhiLaiIn()
    assert PlaceRepository(db).keywords_for([]) == {}
    assert db.cac_lo == []


def test_moi_tu_khoa_cua_mot_dia_diem_nam_gon_trong_mot_lo():
    """Cắt lô theo place_id, nên thứ tự `ORDER BY keyword` của từng danh sách không
    đổi so với lúc chạy một câu duy nhất."""
    db = SessionGiaGhiLaiIn()
    PlaceRepository(db).keywords_for(range(KEYWORD_BATCH * 3))

    cho_cua = {}
    for i, lo in enumerate(db.cac_lo):
        for pid in lo:
            assert pid not in cho_cua, "một id xuất hiện ở hai lô khác nhau"
            cho_cua[pid] = i


# =====================================================================
# Lỗi 4 — `/places/export` phải duyệt bảng ĐÚNG MỘT LƯỢT
# =====================================================================
class RepoGia:
    """Repository giả, đếm số lượt duyệt bảng và số lần tra từ khoá."""

    def __init__(self, places: list[Place]) -> None:
        self.places = places
        self.so_luot_stream = 0
        self.cac_lo_tu_khoa: list[list[int]] = []

    def stream(self, f: PlaceFilter, chunk: int = 1000):  # noqa: ANN201, ARG002
        self.so_luot_stream += 1
        yield from self.places

    def keywords_for(self, ids) -> dict[int, list[str]]:  # noqa: ANN001
        ids = list(ids)
        self.cac_lo_tu_khoa.append(ids)
        return {i: [f"kw-{i}"] for i in ids}


def dung_places(n: int) -> list[Place]:
    return [Place(id=i, name=f"cong-ty-{i}") for i in range(n)]


def test_chi_duyet_bang_dung_mot_luot():
    """Hai lượt SELECT riêng biệt trên một bảng mà worker đang ghi vào liên tục thì
    dòng chèn vào giữa hai lượt sẽ có trong file nhưng không có từ khoá."""
    repo = RepoGia(dung_places(25))
    kho: dict[int, list[str]] = {}

    list(place_router._dong_kem_tu_khoa(repo, PlaceFilter(), kho, lo=10))

    assert repo.so_luot_stream == 1


def test_tu_khoa_cua_mot_dong_luon_co_mat_truoc_khi_dong_do_duoc_nha_ra():
    """Đây là giao kèo giữ cho bên ghi file đọc theo luồng vẫn ra đủ dữ liệu."""
    repo = RepoGia(dung_places(25))
    kho: dict[int, list[str]] = {}

    for place in place_router._dong_kem_tu_khoa(repo, PlaceFilter(), kho, lo=10):
        assert kho.get(place.id) == [f"kw-{place.id}"]


def test_khong_gom_toan_bo_id_truoc_khi_ghi():
    """Gom hết id rồi mới ghi là giữ cả 100.000 đối tượng Place trong RAM — đúng thứ
    mà `yield_per` + `constant_memory` sinh ra để tránh."""
    repo = RepoGia(dung_places(100))
    kho: dict[int, list[str]] = {}

    it = place_router._dong_kem_tu_khoa(repo, PlaceFilter(), kho, lo=10)
    next(it)   # mới lấy đúng MỘT dòng

    assert len(repo.cac_lo_tu_khoa) == 1, "đã đi tra từ khoá cho cả bảng trước khi ghi"
    assert len(kho) == 10


def test_van_tra_tu_khoa_theo_lo_chu_khong_n_cong_mot():
    repo = RepoGia(dung_places(25))
    kho: dict[int, list[str]] = {}

    list(place_router._dong_kem_tu_khoa(repo, PlaceFilter(), kho, lo=10))

    assert [len(lo) for lo in repo.cac_lo_tu_khoa] == [10, 10, 5]


def test_cot_tu_khoa_day_du_ca_o_lo_cuoi(tmp_path):
    """Chạy với BỘ GHI FILE THẬT: lô cuối (không đầy) là chỗ dễ rơi nhất, và nếu rơi
    thì file vẫn mở được bình thường, chỉ thiếu dữ liệu — không ai phát hiện ra."""
    repo = RepoGia(dung_places(25))
    kho: dict[int, list[str]] = {}
    path = tmp_path / "danh-sach.csv"

    export_csv(place_router._dong_kem_tu_khoa(repo, PlaceFilter(), kho, lo=10), kho, path)

    cot = HEADERS.index("Từ khoá tìm ra")
    with path.open(encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    assert len(rows) == 26                       # 1 tiêu đề + 25 dòng
    assert [r[cot] for r in rows[1:]] == [f"kw-{i}" for i in range(25)]


# =====================================================================
# Lỗi 1 — luồng SSE không được chạm DB trên event loop
# =====================================================================
@pytest.fixture(autouse=True)
def xoa_bo_dem_nhip():
    """Bộ đệm `pulse` là biến cấp module — không dọn thì test này ăn kết quả của test kia."""
    place_router._pulse_cache = None
    yield
    place_router._pulse_cache = None


def test_phan_cham_db_chay_o_luong_khac_event_loop(monkeypatch):
    """Câu `pulse()` phải quét toàn bảng. Gọi thẳng trên event loop là cả tiến trình
    đứng im suốt lượt quét — kể cả /health mà Docker dùng để đo container còn sống."""
    luong_da_chay = []
    monkeypatch.setattr(
        place_router, "_doc_nhip",
        lambda: (luong_da_chay.append(threading.get_ident()), ({"total": 1}, False))[1],
    )

    async def chay():  # noqa: ANN202
        return threading.get_ident(), await place_router._nhip_dia_diem()

    luong_event_loop, _ = asyncio.run(chay())

    assert luong_da_chay, "_doc_nhip chưa được gọi"
    assert luong_da_chay[0] != luong_event_loop


def test_nhieu_tab_cung_mo_van_chi_mot_cau_truy_van(monkeypatch):
    """Mỗi tab là một kết nối SSE riêng nhưng hỏi cùng một câu và nhận cùng một đáp
    án; không gom lại thì tải xuống DB tăng thẳng theo số tab."""
    so_lan = []
    monkeypatch.setattr(
        place_router, "_doc_nhip",
        lambda: (so_lan.append(1), ({"total": len(so_lan)}, True))[1],
    )

    async def ba_tab():  # noqa: ANN202
        return [await place_router._nhip_dia_diem() for _ in range(3)]

    ket_qua = asyncio.run(ba_tab())

    assert len(so_lan) == 1
    assert {k[0]["total"] for k in ket_qua} == {1}


def test_bo_dem_het_han_thi_di_hoi_lai(monkeypatch):
    """Đệm là để gom tab, không phải để đóng băng bảng: hết hạn phải hỏi lại."""
    so_lan = []
    monkeypatch.setattr(
        place_router, "_doc_nhip",
        lambda: (so_lan.append(1), ({"total": len(so_lan)}, True))[1],
    )

    async def hai_lan_cach_xa():  # noqa: ANN202
        await place_router._nhip_dia_diem()
        # Đẩy mốc của bộ đệm lùi quá hạn thay vì `sleep` thật — test không được
        # chậm đi chỉ vì hằng số TTL có ngày bị chỉnh lên.
        cu = place_router._pulse_cache
        place_router._pulse_cache = (cu[0] - place_router._PULSE_TTL - 1, cu[1])
        return await place_router._nhip_dia_diem()

    ket_qua = asyncio.run(hai_lan_cach_xa())

    assert len(so_lan) == 2
    assert ket_qua[0]["total"] == 2


def test_endpoint_sse_khong_con_mo_session_ngay_trong_event_loop():
    """Lưới an toàn cho lần sửa sau: viết lại `SessionLocal()` vào trong `async def`
    là lặp lại đúng lỗi cũ, mà triệu chứng (API lâu lâu đơ) không chỉ về đây."""
    import inspect

    nguon = inspect.getsource(place_router.place_events)
    assert "SessionLocal(" not in nguon
    assert "_nhip_dia_diem" in nguon


# =====================================================================
# Lỗi 2 — biểu đồ 14 ngày phải cắt ngày theo MỘT múi giờ
# =====================================================================
class DbGiaTheoThuTu:
    """Session giả trả kết quả đã dọn sẵn theo ĐÚNG THỨ TỰ endpoint gọi."""

    def __init__(self, ket_qua: list[list]) -> None:
        self._ket_qua = ket_qua
        self.cac_cau: list = []

    def execute(self, stmt):  # noqa: ANN001, ANN201
        self.cac_cau.append(stmt)
        return KetQuaGia(self._ket_qua[len(self.cac_cau) - 1])


def goi_overview(day_rows: list) -> tuple[object, DbGiaTheoThuTu]:
    # Thứ tự endpoint hỏi: trạng thái, sống/chết, 3 lượt đếm độ đầy đủ, 14 ngày, top.
    db = DbGiaTheoThuTu([[("done", 5)], [("ACTIVE", 5)], [(1,)], [(2,)], [(3,)], day_rows, []])
    return stats_router.overview(db=db, _=None).data, db


def cau_gom_theo_ngay(db: DbGiaTheoThuTu):  # noqa: ANN201
    return db.cac_cau[5].compile(dialect=postgresql.dialect())


def test_cat_ngay_bang_mui_gio_trong_cau_hoi_chu_khong_pho_mac_cho_postgres():
    """Container `db` không đặt TZ nên `date(first_seen_at)` cắt theo UTC, trong khi
    container app chạy giờ VN — lệch 7 tiếng, và cả hai đều không báo gì."""
    _, db = goi_overview([])
    da_dich = cau_gom_theo_ngay(db)

    assert "timezone(" in str(da_dich), "vẫn để Postgres tự quyết ngày theo TZ của session"
    assert get_settings().timezone in da_dich.params.values()


def test_gom_nhom_va_cot_chon_dung_mot_bieu_thuc_ngay():
    """SELECT một kiểu mà GROUP BY một kiểu thì Postgres báo lỗi ngay, nhưng chỉ khi
    có dữ liệu thật — test này bắt trước điều đó."""
    _, db = goi_overview([])
    sql = str(cau_gom_theo_ngay(db))

    truoc_from, sau_group = sql.split("FROM")[0], sql.split("GROUP BY")[-1]
    assert "timezone(" in truoc_from
    assert "timezone(" in sau_group


def test_moc_duoi_la_00_gio_dia_phuong_cua_ngay_xa_nhat():
    """Lấy "13 ngày trước tính từ bây giờ" là cắt mất phần đầu của chính ngày xa nhất
    trên biểu đồ — cột đó lúc nào cũng thấp hơn thực tế."""
    _, db = goi_overview([])
    mui_gio = ZoneInfo(get_settings().timezone)
    hom_nay = datetime.now(mui_gio).date()

    moc = next(v for v in cau_gom_theo_ngay(db).params.values() if isinstance(v, datetime))
    assert moc == datetime.combine(hom_nay - timedelta(days=13), time.min, tzinfo=mui_gio)


def test_loc_van_chay_tren_cot_goc_de_con_dung_duoc_chi_muc():
    sql = str(cau_gom_theo_ngay(goi_overview([])[1]))
    dieu_kien = sql.split("WHERE")[-1].split("GROUP BY")[0]
    assert "timezone(" not in dieu_kien
    assert "first_seen_at >=" in dieu_kien


def test_o_hom_nay_doc_theo_ngay_viet_nam():
    """Ca đêm 00:00-07:00 giờ VN là lúc lệch múi giờ ăn trọn: ô "Hôm nay" hiện 0
    trong khi worker vừa cào được vài chục dòng."""
    hom_nay_vn = datetime.now(ZoneInfo(get_settings().timezone)).date()
    data, _ = goi_overview([(hom_nay_vn, 42)])

    assert data.today_new == 42
    assert data.last_14_days[-1] == stats_router.DayCount(date=str(hom_nay_vn), count=42)


def test_khung_bieu_do_du_14_ngay_lien_tuc():
    data, _ = goi_overview([])
    ngay = [date.fromisoformat(d.date) for d in data.last_14_days]

    assert len(ngay) == 14
    assert ngay == sorted(ngay)
    assert ngay[-1] - ngay[0] == timedelta(days=13)


def test_hai_ve_cung_di_theo_mot_cau_hinh_duy_nhat(monkeypatch):
    """Phép thử quyết định: đổi `settings.timezone` sang một múi giờ cực đoan.

    Kiritimati là UTC+14, lệch với UTC tới 14 tiếng — nếu còn sót một vế nào lấy
    ngày theo UTC (hay theo đồng hồ máy đang chạy test) thì hai vế lệch nhau thấy
    ngay, bất kể chạy vào giờ nào trong ngày. Đây đúng là hình dạng của lỗi cũ, chỉ
    khác là lệch 7 tiếng nên mỗi ngày chỉ sai trong khung 00:00-07:00.
    """
    mui_gio_la = "Pacific/Kiritimati"
    monkeypatch.setattr(
        stats_router, "get_settings", lambda: SimpleNamespace(timezone=mui_gio_la)
    )
    hom_nay_la = datetime.now(ZoneInfo(mui_gio_la)).date()

    data, db = goi_overview([(hom_nay_la, 7)])

    assert mui_gio_la in cau_gom_theo_ngay(db).params.values()   # vế SQL
    assert data.last_14_days[-1].date == str(hom_nay_la)         # vế Python
    assert data.today_new == 7
