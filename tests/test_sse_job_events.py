"""Luồng SSE của một job không được chạm DB ngay trên event loop.

Cùng một cái bẫy đã phải sửa ở `/places/events`, chỉ khác là ở đây câu truy vấn
rẻ hơn hẳn — tra đúng một dòng theo khoá chính, đo được 0,23 ms. Chính vì rẻ nên
nó dễ bị bỏ qua: SQLAlchemy ĐỒNG BỘ nằm trong `async def` thì nhanh cỡ nào cũng
vẫn chặn event loop, và một lần DB chậm bất thường (khoá, checkpoint) là cả tiến
trình đứng im — kể cả `/health` mà Docker dùng để đo container còn sống.

Chạy THUẦN: Session và repository đều được thay bằng bản giả, không có Postgres
nào ở đây.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import threading
from datetime import UTC, datetime
from types import SimpleNamespace

from app.modules.scraper.job import repository as job_repository
from app.modules.scraper.job import router as job_router


def job_gia(job_id: int = 7, status: str = "done") -> SimpleNamespace:
    """Đủ trường để `_progress_payload` dựng được payload."""
    return SimpleNamespace(
        id=job_id, status=status, phase="idle",
        total_queries=3, done_queries=3, total_places=10, done_places=10,
        failed_places=0, new_places=4, blocked_count=0,
        started_at=None, finished_at=None,
        updated_at=datetime(2026, 9, 23, tzinfo=UTC),
    )


class SessionGia:
    def __init__(self) -> None:
        self.da_dong = False

    def close(self) -> None:
        self.da_dong = True


def gan_db_gia(monkeypatch, job, luong: list[int] | None = None) -> list[SessionGia]:
    """Thay Session + repository bằng bản giả; ghi lại luồng đã chạy câu truy vấn."""
    cac_session: list[SessionGia] = []

    def mo_session():  # noqa: ANN202
        s = SessionGia()
        cac_session.append(s)
        return s

    class RepoGia:
        def __init__(self, db) -> None:  # noqa: ANN001, ARG002
            pass

        def by_id(self, job_id: int):  # noqa: ANN202, ARG002
            if luong is not None:
                luong.append(threading.get_ident())
            return job

    monkeypatch.setattr(job_router, "SessionLocal", mo_session)
    monkeypatch.setattr(job_repository, "JobRepository", RepoGia)
    return cac_session


async def doc_luong(job_id: int = 7) -> tuple[int, list[dict]]:
    """Chạy hết luồng SSE, trả (luồng của event loop, các sự kiện đã bắn)."""
    response = await job_router.job_events(job_id=job_id, _=None)
    events = [event async for event in response.body_iterator]
    return threading.get_ident(), events


def test_phan_cham_db_chay_o_luong_khac_event_loop(monkeypatch):
    luong_da_chay: list[int] = []
    gan_db_gia(monkeypatch, job_gia(), luong_da_chay)

    luong_event_loop, _ = asyncio.run(doc_luong())

    assert luong_da_chay, "chưa hề gọi tới repository"
    assert all(t != luong_event_loop for t in luong_da_chay)


def test_van_bao_du_tien_do_roi_ket_thuc(monkeypatch):
    """Đẩy việc sang threadpool không được làm đổi thứ giao diện nhận về."""
    gan_db_gia(monkeypatch, job_gia(job_id=7, status="done"))

    _, events = asyncio.run(doc_luong(7))

    assert [e["event"] for e in events] == ["progress", "done"]
    assert json.loads(events[0]["data"])["id"] == 7
    assert json.loads(events[1]["data"]) == {"id": 7, "status": "done"}


def test_khong_co_job_thi_bao_loi_chu_khong_treo(monkeypatch):
    """Job bị xoá giữa chừng: phải bắn `error` rồi đóng, không im lặng lặp mãi."""
    gan_db_gia(monkeypatch, None)

    _, events = asyncio.run(doc_luong(404))

    assert [e["event"] for e in events] == ["error"]
    assert json.loads(events[0]["data"]) == {"code": "NOT_FOUND"}


def test_session_luon_duoc_dong_lai(monkeypatch):
    """Kết nối SSE sống hàng giờ; giữ lại một connection mỗi vòng là cạn pool."""
    cac_session = gan_db_gia(monkeypatch, job_gia())

    asyncio.run(doc_luong())

    assert cac_session and all(s.da_dong for s in cac_session)


def test_endpoint_sse_khong_con_mo_session_ngay_trong_event_loop():
    """Lưới an toàn cho lần sửa sau: viết lại `SessionLocal()` vào trong `async def`
    là lặp lại đúng lỗi cũ, mà triệu chứng (API lâu lâu đơ) không chỉ về đây."""
    nguon = inspect.getsource(job_router.job_events)

    assert "SessionLocal(" not in nguon
    assert "run_in_threadpool(_doc_tien_do" in nguon
    # Và hàm đồng bộ phải là hàm thường, không phải coroutine — `run_in_threadpool`
    # nhận một coroutine function thì chỉ tạo ra coroutine chứ không chạy gì.
    assert not inspect.iscoroutinefunction(job_router._doc_tien_do)
