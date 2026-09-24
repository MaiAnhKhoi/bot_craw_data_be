from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import run_in_threadpool

from app.core.database import SessionLocal, get_db
from app.core.deps import current_user, current_user_query_token, require_admin
from app.core.pagination import Page, PageParams, page_params
from app.core.response import ApiResponse
from app.modules.identity.entity import User
from app.modules.scraper.job.entity import JOB_CANCELLED, JOB_DONE, JOB_FAILED
from app.modules.scraper.job.request import JobCreateRequest, RemainingSplitRequest
from app.modules.scraper.job.response import (
    JobDetailResponse,
    JobResponse,
    PlaceRejectResponse,
    RemainingAreaResponse,
    SplitPlanResponse,
)
from app.modules.scraper.job.service import JobService, rate_per_min

router = APIRouter(prefix="/jobs", tags=["jobs"])

FINAL_STATUSES = (JOB_DONE, JOB_FAILED, JOB_CANCELLED)


@router.post("", response_model=ApiResponse[JobResponse], summary="Tạo job quét mới")
def create_job(
    payload: JobCreateRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
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


@router.get(
    "/remaining-areas",
    response_model=ApiResponse[Page[RemainingAreaResponse]],
    summary="Địa bàn còn sót — truy vấn cần chia nhỏ hoặc chạy lại",
)
def list_remaining_areas(
    params: PageParams = Depends(page_params),
    stop_reason: str | None = Query(
        None, description="Lọc theo lý do dừng: cut_off | cap | unknown. Bỏ trống = cả ba."
    ),
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
) -> ApiResponse[Page[RemainingAreaResponse]]:
    """PHẢI khai TRƯỚC `/{job_id}`.

    FastAPI so khớp route theo thứ tự khai báo, nên nếu đứng sau thì
    `GET /jobs/remaining-areas` rơi vào `/{job_id}` và chết ở bước ép kiểu
    `job_id: int` — trả 422 cho một đường dẫn hoàn toàn đúng.
    """
    return ApiResponse.ok(JobService(db).remaining_areas(params, stop_reason))


@router.post(
    "/remaining-areas/split-preview",
    response_model=ApiResponse[SplitPlanResponse],
    summary="Xem trước: các dòng đang chọn sẽ sinh ra bao nhiêu truy vấn",
)
def preview_remaining_split(
    payload: RemainingSplitRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> ApiResponse[SplitPlanResponse]:
    """Cùng một bản kế hoạch với `/split`, chỉ khác là không ghi gì.

    Tách ra hai endpoint thay vì thêm cờ `dry_run`: một cái GHI, một cái không —
    trộn vào một chỗ thì người đọc log lẫn người gọi nhầm đều phải đoán.
    """
    return ApiResponse.ok(JobService(db).plan_split(payload))


@router.post(
    "/remaining-areas/split",
    response_model=ApiResponse[JobResponse],
    summary="Tạo job mới từ các địa bàn còn sót đang chọn",
)
def create_remaining_split(
    payload: RemainingSplitRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> ApiResponse[JobResponse]:
    return ApiResponse.ok(JobService(db).create_split(payload))


@router.get("/{job_id}", response_model=ApiResponse[JobDetailResponse], summary="Chi tiết job")
def get_job(
    job_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
) -> ApiResponse[JobDetailResponse]:
    return ApiResponse.ok(JobService(db).get(job_id))


@router.post("/{job_id}/pause", response_model=ApiResponse[JobResponse], summary="Tạm dừng job")
def pause_job(job_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):  # noqa: ANN201
    return ApiResponse.ok(JobService(db).pause(job_id))


@router.post("/{job_id}/resume", response_model=ApiResponse[JobResponse], summary="Chạy tiếp job")
def resume_job(job_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):  # noqa: ANN201
    return ApiResponse.ok(JobService(db).resume(job_id))


@router.post("/{job_id}/cancel", response_model=ApiResponse[JobResponse], summary="Huỷ job")
def cancel_job(job_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):  # noqa: ANN201
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


def _doc_tien_do(job_id: int) -> dict | None:
    """Phần CHẠM DB của luồng SSE. Đồng bộ — chỉ được gọi qua threadpool.

    Trả None khi job không còn, để bên gọi phân biệt được "chưa đổi gì" với
    "không có job này".

    Dựng luôn payload ở trong Session: đọc thuộc tính của một đối tượng ORM sau
    khi Session đã đóng là chuyện may rủi, không phải một phép gán vô hại.
    """
    db = SessionLocal()
    try:
        from app.modules.scraper.job.repository import JobRepository

        job = JobRepository(db).by_id(job_id)
        return _progress_payload(job) if job is not None else None
    finally:
        db.close()


@router.get(
    "/{job_id}/rejects",
    response_model=ApiResponse[Page[PlaceRejectResponse]],
    summary="Các thẻ đã bị loại vì ngoài danh mục ngành nghề",
)
def list_job_rejects(
    job_id: int,
    params: PageParams = Depends(page_params),
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
) -> ApiResponse[Page[PlaceRejectResponse]]:
    """Đối trọng của `rejected_count`: con số nói bao nhiêu, màn này nói CÁI GÌ.

    Có nó thì người dùng mới trả lời được câu "bộ lọc có đang siết quá tay không"
    bằng cách nhìn, thay vì đoán. Danh mục ngành nghề do AI sinh rồi người sửa
    tay — thiếu một nhãn là mất cả một loại doanh nghiệp thật.
    """
    return ApiResponse.ok(JobService(db).rejects(job_id, params))


@router.get("/{job_id}/events", summary="Luồng tiến độ thời gian thực (SSE)")
async def job_events(job_id: int, _: User = Depends(current_user_query_token)):  # noqa: ANN201
    """Đẩy tiến độ mỗi 2 giây.

    Mỗi vòng mở một Session ngắn rồi đóng ngay: kết nối SSE sống hàng giờ, ôm một
    connection suốt thời gian đó sẽ làm cạn pool khi vài người cùng xem.

    Phần chạm DB đi qua threadpool, theo đúng khuôn của `/places/events`. Câu hỏi
    ở đây rẻ hơn hẳn bên kia — tra đúng một dòng theo khoá chính, đo được 0,23 ms
    — nhưng SQLAlchemy ĐỒNG BỘ nằm trong `async def` thì nhanh cỡ nào cũng vẫn
    chặn event loop của uvicorn, và một lần DB chậm bất thường (khoá, checkpoint)
    là cả tiến trình đứng im, kể cả `/health` mà Docker dùng để đo container còn
    sống. Hai luồng SSE cùng dự án cũng không nên có hai cách làm khác nhau.

    KHÔNG có bộ đệm dùng chung như bên places: ở đó N tab cùng hỏi một câu duy
    nhất nên gom lại là lãi; ở đây mỗi tab xem một job khác nhau, gom lại chẳng
    tiết kiệm được gì mà còn phải nuôi một dict theo job_id.
    """

    async def stream():  # noqa: ANN202
        last = None
        while True:
            payload = await run_in_threadpool(_doc_tien_do, job_id)
            if payload is None:
                yield {"event": "error", "data": json.dumps({"code": "NOT_FOUND"})}
                return
            status = payload["status"]

            if payload != last:
                yield {"event": "progress", "data": json.dumps(payload, ensure_ascii=False)}
                last = payload
            if status in FINAL_STATUSES:
                yield {"event": "done", "data": json.dumps({"id": job_id, "status": status})}
                return
            await asyncio.sleep(2)

    return EventSourceResponse(stream())
