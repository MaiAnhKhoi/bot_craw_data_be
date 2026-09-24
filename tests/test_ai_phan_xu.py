"""Tầng AI phân xử những thẻ luật cứng không quyết nổi.

KHÔNG GỌI AI THẬT. Cái đáng kiểm ở đây không phải AI phán đúng hay sai — đó là
việc của mô hình — mà là HỆ THỐNG CƯ XỬ RA SAO KHI AI KHÔNG TRẢ LỜI ĐƯỢC.

Tầng này là ngoại lệ duy nhất của luật "worker không bao giờ gọi AI". Luật đó tồn
tại để một lần AI hỏng không làm chết job đang quét. Nếu ngoại lệ này phá luật đó
thì mỗi lần mạng chập chờn là một lần mất lead vĩnh viễn — và mất im lặng, vì
dòng bị vứt không bao giờ xuất hiện trong bảng để mà nghi ngờ.
"""
from __future__ import annotations

import asyncio

import pytest

from app.core import ai
from app.core.config import get_settings
from app.modules.scraper.engine.models import CardResult
from app.workers.runner import Runner


@pytest.fixture
def runner() -> Runner:
    """Runner trần, không trình duyệt, không DB — `_phan_xu` không đụng tới chúng."""
    return Runner.__new__(Runner)


def _the(name: str, category: str) -> CardResult:
    return CardResult(name=name, maps_url="https://maps/x", feature_id="x", category=category)


PARAMS = {"keywords": ["Công ty trái cây"]}


def _goi(runner: Runner, the: list[CardResult]) -> dict:
    """Chạy coroutine bằng `asyncio.run` thay vì dựng pytest-asyncio.

    Cả bộ test của dự án đang chạy đồng bộ; thêm một plugin async chỉ vì sáu hàm
    ở đây là đổi cách chạy của 700 test còn lại để đỡ sáu dòng.
    """
    return asyncio.run(runner._phan_xu(PARAMS, the, "truy vấn thử"))


def test_ai_hong_thi_tra_ve_rong_chu_khong_nem_loi(runner, monkeypatch):
    """`AiUnavailable` phải bị nuốt tại đây.

    Ném tiếp ra ngoài thì nó chui vào nhánh `except` của vòng quét, truy vấn bị
    đánh `failed` và cả địa bàn đó coi như chưa quét — vì AI hỏng, không phải vì
    Google hỏng.
    """
    def hong(*a, **kw):  # noqa: ANN002, ANN003, ANN202
        raise ai.AiUnavailable("hết hạn mức")

    monkeypatch.setattr(ai, "judge_places", hong)
    monkeypatch.setattr(ai, "is_enabled", lambda: True)
    assert _goi(runner, [_the("X", "Company")]) == {}


def test_loi_la_cung_khong_duoc_giet_job(runner, monkeypatch):
    """Cả lỗi KHÔNG lường trước — SDK đổi, JSON hỏng, gì cũng vậy."""
    def no(*a, **kw):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("thứ chưa ai gặp bao giờ")

    monkeypatch.setattr(ai, "judge_places", no)
    monkeypatch.setattr(ai, "is_enabled", lambda: True)
    assert _goi(runner, [_the("X", "Company")]) == {}


def test_tat_ai_thi_khong_goi_gi_ca(runner, monkeypatch):
    da_goi = []
    monkeypatch.setattr(ai, "judge_places", lambda *a, **kw: da_goi.append(1) or {})
    monkeypatch.setattr(ai, "is_enabled", lambda: False)
    assert _goi(runner, [_the("X", "Company")]) == {}
    assert da_goi == []


def test_khong_co_the_ranh_gioi_thi_khong_ton_mot_luot_goi(runner, monkeypatch):
    """Phần lớn truy vấn không có thẻ ranh giới nào — đừng gọi AI cho vui."""
    da_goi = []
    monkeypatch.setattr(ai, "judge_places", lambda *a, **kw: da_goi.append(1) or {})
    monkeypatch.setattr(ai, "is_enabled", lambda: True)
    assert _goi(runner, []) == {}
    assert da_goi == []


def test_vuot_tran_thi_cat_bot_chu_khong_bo_ca_lo(runner, monkeypatch):
    """Cầu chì, không phải công tắc.

    Danh mục ngành nghề khai sai có thể đẩy gần như mọi thẻ vào diện ranh giới.
    Khi đó bỏ cả lô là mất trắng một truy vấn; phán được bao nhiêu hay bấy nhiêu,
    phần dư rơi về "chưa phán được" và vẫn được giữ lại.
    """
    nhan = []
    monkeypatch.setattr(ai, "is_enabled", lambda: True)
    monkeypatch.setattr(
        ai, "judge_places",
        lambda mat_hang, ung_vien: nhan.append(len(ung_vien)) or {},
    )
    tran = get_settings().ai_judge_max_per_query
    _goi(runner, [_the(f"X{i}", "Company") for i in range(tran + 25)])
    assert nhan == [tran]


def test_ai_tra_thieu_muc_thi_muc_do_khong_bi_coi_la_bi_loai(runner, monkeypatch):
    """Mô hình bỏ sót một `stt` là chuyện có thật và không báo lỗi.

    Coi "thiếu" là "bị loại" biến một lần trả lời không trọn vẹn thành một lần
    mất dữ liệu. Nơi gọi phải thấy rõ mục nào chưa được phán để còn giữ lại.
    """
    monkeypatch.setattr(ai, "is_enabled", lambda: True)
    monkeypatch.setattr(
        ai, "judge_places",
        lambda mat_hang, ung_vien: {0: ai.PhanXet(stt=0, giu=True, ly_do="ok")},
    )
    kq = _goi(runner, [_the("A", "Company"), _the("B", "General store")])
    assert set(kq) == {0}
    assert 1 not in kq


def test_bo_phan_xet_co_stt_la(monkeypatch):
    """`stt` không có trong lô thì phải vứt, không được ghép bừa.

    Ghép nhầm nghĩa là gán phán xét của mục này cho mục khác — một dòng bị loại
    kèm lý do của một dòng khác, và không có cách nào lần ra.
    """
    class Lo:
        results = [
            ai.PhanXet(stt=0, giu=True, ly_do="đúng"),
            ai.PhanXet(stt=99, giu=False, ly_do="bịa"),
        ]

    class Rsp:
        parsed_output = Lo()

        class usage:  # noqa: N801
            input_tokens = 1

    monkeypatch.setattr(ai, "is_enabled", lambda: True)
    monkeypatch.setattr(ai, "_client", lambda: type("C", (), {
        "messages": type("M", (), {"parse": staticmethod(lambda **kw: Rsp())})()
    })())
    kq = ai.judge_places(["trái cây"], [ai.UngVien(stt=0, name="A", category="Company")])
    assert set(kq) == {0}
