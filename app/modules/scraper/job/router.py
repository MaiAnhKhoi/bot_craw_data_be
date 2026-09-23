from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.core.database import SessionLocal, get_db
from app.core.deps import current_user, current_user_query_token
from app.core.pagination import Page, PageParams, page_params
from app.core.response import ApiResponse
from app.modules.identity.entity import User
from app.modules.scraper.job.entity import JOB_CANCELLED, JOB_DONE, JOB_FAILED
from app.modules.scraper.job.request import JobCreateRequest
from app.modules.scraper.job.response import JobDetailResponse, JobResponse
from app.modules.scraper.job.service import JobService, rate_per_min

router = APIRouter(prefix="/jobs", tags=["jobs"])

FINAL_STATUSES = (JOB_DONE, JOB_FAILED, JOB_CANCELLED)


@router.post("", response_model=ApiResponse[JobResponse], summary="Tạo job quét mới")
def create_job(
    payload: JobCreateRequest,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
) -> ApiResponse[JobResponse]:
    return ApiResponse.ok(JobService(db).create(payload))


@router.get("", response_model=ApiResponse[Page[JobResponse]], summary="Danh sách job")
def list_jobs(
    params: PageParams = Depends(page_params),
    status: str | None = Query(None, description="Lọc theo trạng thái"),
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
) -> ApiResponse[Page[JobResponse]]:
    return ApiResponse.ok(JobService(db).list(params, status))


@router.get("/{job_id}", response_model=ApiResponse[JobDetailResponse], summary="Chi tiết job")
def get_job(
    job_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
) -> ApiResponse[JobDetailResponse]:
    return ApiResponse.ok(JobService(db).get(job_id))


@router.post("/{job_id}/pause", response_model=ApiResponse[JobResponse], summary="Tạm dừng job")
def pause_job(job_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):  # noqa: ANN201
    return ApiResponse.ok(JobService(db).pause(job_id))


@router.post("/{job_id}/resume", response_model=ApiResponse[JobResponse], summary="Chạy tiếp job")
def resume_job(job_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):  # noqa: ANN201
    return ApiResponse.ok(JobService(db).resume(job_id))


@router.post("/{job_id}/cancel", response_model=ApiResponse[JobResponse], summary="Huỷ job")
def cancel_job(job_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):  # noqa: ANN201
    return ApiResponse.ok(JobService(db).cancel(job_id))


def _progress_payload(job) -> dict:  # noqa: ANN001
    return {
        "id": job.id,
        "status": job.status,
        "phase": job.phase,
        "total_queries": job.total_queries,
        "done_queries": job.done_queries,
        "total_places": job.total_places,
        "done_places": job.done_places,
        "failed_places": job.failed_places,
        "new_places": job.new_places,
        "blocked_count": job.blocked_count,
        "rate_per_min": rate_per_min(job),
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


@router.get("/{job_id}/events", summary="Luồng tiến độ thời gian thực (SSE)")
async def job_events(job_id: int, _: User = Depends(current_user_query_token)):  # noqa: ANN201
    """Đẩy tiến độ mỗi 2 giây.

    Mỗi vòng mở một Session ngắn rồi đóng ngay: kết nối SSE sống hàng giờ, ôm một
    connection suốt thời gian đó sẽ làm cạn pool khi vài người cùng xem.
    """

    async def stream():  # noqa: ANN202
        last = None
        while True:
            db = SessionLocal()
            try:
                from app.modules.scraper.job.repository import JobRepository

                job = JobRepository(db).by_id(job_id)
                if job is None:
                    yield {"event": "error", "data": json.dumps({"code": "NOT_FOUND"})}
                    return
                payload = _progress_payload(job)
                status = job.status
            finally:
                db.close()

            if payload != last:
                yield {"event": "progress", "data": json.dumps(payload, ensure_ascii=False)}
                last = payload
            if status in FINAL_STATUSES:
                yield {"event": "done", "data": json.dumps({"id": job_id, "status": status})}
                return
            await asyncio.sleep(2)

    return EventSourceResponse(stream())
