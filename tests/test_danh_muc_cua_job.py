"""Danh mục ngành nghề của một job được chốt từ đâu, lúc nào.

Chạy THUẦN: `_danh_muc` chỉ đọc payload và gọi `KeywordService.categories_of`,
nên thay service bằng một bản giả là đủ. Cái đáng kiểm ở đây là LUẬT chọn nguồn,
không phải câu SQL.
"""
from __future__ import annotations

import pytest

from app.modules.scraper.job.request import JobCreateRequest
from app.modules.scraper.job.service import ExpandedQuery, JobService


@pytest.fixture
def service(monkeypatch):
    """JobService không có DB, với bảng dịch giả: chỉ Ấn Độ có sẵn danh mục."""

    class KeywordServiceGia:
        def __init__(self, db) -> None:  # noqa: ANN001
            pass

        def categories_of(self, keywords, codes):  # noqa: ANN001, ANN201
            return {"IN": ["Fruit wholesaler", "फल विक्रेता"]} if "IN" in codes else {}

    import app.modules.keyword.service as ks

    monkeypatch.setattr(ks, "KeywordService", KeywordServiceGia)
    sv = JobService.__new__(JobService)
    sv.db = None
    return sv


def _yeu_cau(**kw) -> JobCreateRequest:
    return JobCreateRequest(name="t", keywords=["vựa trái cây"], **kw)


def _tv(*gl: str) -> list[ExpandedQuery]:
    return [ExpandedQuery(query=f"q {g}", hl="vi", gl=g) for g in gl]


def test_giao_dien_gui_len_thi_uu_tien(service):
    """Bản người dùng vừa nhìn thấy và sửa tay thắng bản lưu trong DB."""
    ra = service._danh_muc(
        _yeu_cau(category_map={"in": ["Nguoi dung tu go"]}), _tv("in")
    )
    assert ra == {"IN": ["Nguoi dung tu go"]}


def test_thieu_thi_tra_tu_ban_dich_da_luu(service):
    """Tạo job từ "bộ từ khoá đã lưu" thì giao diện không mở màn dịch lần nào.

    Không tra bù ở server thì đúng những job tiện nhất lại là job không lọc gì.
    """
    ra = service._danh_muc(_yeu_cau(), _tv("in"))
    assert ra == {"IN": ["Fruit wholesaler", "फल विक्रेता"]}


def test_viet_nam_lay_chinh_tu_khoa_lam_danh_muc(service):
    """Sân nhà không đi qua AI nên không có dòng nào trong bảng dịch.

    Dừng ở bước tra DB thì Việt Nam là nơi DUY NHẤT không được lọc — và đó là
    nơi quét nhiều nhất.
    """
    ra = service._danh_muc(_yeu_cau(), _tv("vn"))
    assert ra == {"VN": ["vựa trái cây"]}


def test_nuoc_ngoai_KHONG_lay_tu_khoa_lam_danh_muc(service):
    """Chỗ dễ sai nhất, và sai thì im lặng.

    Từ khoá tiếng Hindi không có chữ nào chung với nhãn "Fruit and vegetable
    store" mà Google trả về, nên lấy từ khoá làm danh mục ở nước ngoài là vứt
    oan chính những cửa hàng trái cây thật — đo được 15 cái trên dữ liệu Andaman.
    Thà không lọc còn hơn lọc bằng một danh mục sai.
    """
    ra = service._danh_muc(_yeu_cau(), _tv("th"))
    assert "TH" not in ra


def test_danh_muc_chot_mot_lan_luc_tao_job(service):
    """Kết quả đi thẳng vào `params` của job, không tra lại lúc chạy.

    Job chạy hàng giờ. Tra lại mỗi lần ghi thì người dùng sửa danh mục giữa chừng
    sẽ khiến nửa đầu và nửa sau của cùng một job lọc theo hai luật khác nhau, mà
    không có gì trong dữ liệu nói ra điều đó.
    """
    import inspect

    from app.modules.scraper.job import service as mod

    nguon = inspect.getsource(mod.JobService.create)
    assert 'params["category_map"] = self._danh_muc(' in nguon
