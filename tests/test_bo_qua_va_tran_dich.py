"""Bỏ qua truy vấn vừa quét, và trần số quốc gia cho một lượt dịch.

Chạy offline: không DB thật, không mạng, không AI. Những chỗ cần chạm DB được
thay bằng monkeypatch đúng một hàm truy vấn.
"""
from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.modules.keyword.service import KeywordService
from app.modules.scraper.job.repository import JobRepository


# ---------- bỏ qua truy vấn vừa quét ----------
def test_tat_ttl_thi_khong_bo_qua_gi_ca():
    """`ttl_days = 0` nghĩa là "luôn quét lại". Phải thoát TRƯỚC khi chạm DB —
    đó cũng là lý do test này truyền `db=None` mà vẫn chạy được."""
    repo = JobRepository(None)
    assert repo.recently_scraped_at("vựa trái cây Hà Nội, Việt Nam", 0, 1) is None
    assert repo.recently_scraped_at("vựa trái cây Hà Nội, Việt Nam", -5, 1) is None


# ---------- trần số quốc gia mỗi lượt dịch ----------
def _quoc_gia(*codes: str) -> list[dict]:
    return [{"code": c, "name": c, "name_en": c} for c in codes]


@pytest.fixture
def service(monkeypatch):
    """KeywordService với bộ nhớ đệm rỗng — mọi nước đều cần gọi AI."""
    svc = KeywordService(None)
    monkeypatch.setattr(KeywordService, "_cached", lambda self, digest, codes: {})
    return svc


def test_viet_nam_khong_tinh_vao_tran(service):
    """Việt Nam dùng thẳng từ khoá gốc nên không bao giờ tốn lượt gọi AI."""
    plan = service.plan(["vựa trái cây"], _quoc_gia("VN", "TH", "JP"))
    assert plan["home"] == ["VN"]
    assert plan["need"] == ["TH", "JP"]
    assert plan["total"] == 3


def test_vuot_tran_thi_bao_ro(service):
    limit = get_settings().ai_max_countries
    codes = [f"X{i:02d}" for i in range(limit + 5)]
    plan = service.plan(["vựa trái cây"], _quoc_gia(*codes))
    assert plan["limit"] == limit
    assert len(plan["need"]) == limit + 5
    assert plan["over_limit"] is True


def test_dung_bang_tran_thi_van_cho_chay(service):
    limit = get_settings().ai_max_countries
    codes = [f"X{i:02d}" for i in range(limit)]
    assert service.plan(["k"], _quoc_gia(*codes))["over_limit"] is False


def test_nuoc_da_co_ban_dich_khong_tinh_vao_tran(monkeypatch):
    """Điểm mấu chốt của cả cơ chế này.

    Trần đặt trên số nước THẬT SỰ CẦN GỌI AI, không phải tổng số nước. 60 nước mà
    50 nước đã có bản dịch lưu sẵn thì chỉ còn 10 nước cần dịch — chặn ở con số
    60 là chặn oan và người dùng không hiểu vì sao mình bị cấm.
    """
    limit = get_settings().ai_max_countries
    codes = [f"X{i:02d}" for i in range(limit + 5)]
    da_co = set(codes[:10])
    monkeypatch.setattr(
        KeywordService,
        "_cached",
        lambda self, digest, asked: {c: DongDem(["Fruit wholesaler"]) for c in asked if c in da_co},
    )
    plan = KeywordService(None).plan(["k"], _quoc_gia(*codes))
    assert len(plan["cached"]) == 10
    assert len(plan["need"]) == limit - 5
    assert plan["over_limit"] is False


class DongDem:
    """Một dòng trong bộ nhớ đệm, rút gọn còn đúng thứ `plan` nhìn tới."""

    def __init__(self, categories: list[str]) -> None:
        self.categories = categories


def test_ban_dich_cu_thieu_danh_muc_nganh_nghe_van_phai_goi_lai_ai(monkeypatch):
    """Có từ khoá nhưng RỖNG danh mục ngành nghề thì KHÔNG tính là đã có.

    Mọi bản dịch sinh ra trước khi có bộ lọc ngành đều ở trạng thái này. Tính
    chúng là "đã đủ" thì job chạy mà không lọc gì — đúng cái lỗi vừa phải sửa
    (tiệm bánh kem lẫn vào công ty trái cây) — và lần này nó hỏng IM LẶNG, vì
    giao diện báo "đã có bản dịch, không tốn token" nên không ai đi kiểm.

    Nhầm về phía gọi lại AI thì mất ít token. Nhầm về phía bỏ qua thì cả lượt
    quét ra dữ liệu bẩn mà không có dấu hiệu nào.
    """
    monkeypatch.setattr(
        KeywordService,
        "_cached",
        lambda self, digest, asked: {
            "TH": DongDem(["Fruit wholesaler"]),   # đủ dùng
            "JP": DongDem([]),                     # bản dịch cũ, thiếu danh mục
        },
    )
    plan = KeywordService(None).plan(["k"], _quoc_gia("TH", "JP", "KR"))
    assert plan["cached"] == ["TH"]
    assert plan["need"] == ["JP", "KR"]


def test_khong_co_tu_khoa_thi_khong_chan(service):
    """Chưa gõ từ khoá thì chưa có gì để dịch — đừng doạ người dùng bằng cảnh báo
    vượt trần ngay khi họ vừa chọn xong địa bàn."""
    plan = service.plan([], _quoc_gia(*[f"X{i:02d}" for i in range(100)]))
    assert plan["over_limit"] is False
    assert plan["need"] == []


def test_plan_khong_bao_gio_goi_ai(monkeypatch):
    """Nếu `plan` lỡ gọi AI thì mỗi phím gõ ở ô từ khoá là một lần tiêu tiền."""
    from app.core import ai

    def no_goi(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("plan() không được phép gọi AI")

    monkeypatch.setattr(ai, "localize_keywords", no_goi)
    monkeypatch.setattr(KeywordService, "_cached", lambda self, digest, codes: {})
    KeywordService(None).plan(["vựa trái cây"], _quoc_gia("TH", "JP", "KR"))
