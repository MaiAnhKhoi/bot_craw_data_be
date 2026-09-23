from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.exceptions import AppError, NotFoundError
from app.core.pagination import Page, PageParams
from app.modules.scraper.job.entity import (
    JOB_CANCELLED,
    JOB_PAUSED,
    JOB_QUEUED,
    JOB_RUNNING,
    ScrapeJob,
)
from app.modules.scraper.job.repository import JobRepository
from app.modules.scraper.job.request import JobCreateRequest
from app.modules.scraper.job.response import JobDetailResponse, JobResponse


def expand_queries(keywords: list[str], locations: list[str] | None) -> list[str]:
    """từ khoá × địa điểm, giữ thứ tự và bỏ trùng.

    Đây là cách duy nhất vượt trần ~120 kết quả mỗi truy vấn của Google: chia nhỏ
    địa bàn ra thành nhiều truy vấn hẹp hơn.
    """
    kws = [k.strip() for k in keywords if k and k.strip() and not k.strip().startswith("#")]
    locs = [x.strip() for x in (locations or []) if x and x.strip() and not x.strip().startswith("#")]
    out: list[str] = []
    for k in kws:
        for q in ([f"{k} {loc}" for loc in locs] if locs else [k]):
            if q not in out:
                out.append(q)
    return out


def rate_per_min(job: ScrapeJob) -> float | None:
    """Số địa điểm hoàn tất mỗi phút, tính từ lúc job bắt đầu."""
    if not job.started_at or not job.done_places:
        return None
    end = job.finished_at or datetime.now(UTC)
    minutes = max((end - job.started_at).total_seconds() / 60, 0.1)
    return round(job.done_places / minutes, 2)


class JobService:
    """Transaction nằm ở đây: router không commit, repository không commit."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = JobRepository(db)

    def create(self, payload: JobCreateRequest) -> JobResponse:
        queries = expand_queries(payload.keywords, payload.locations)
        if not queries:
            raise AppError("Cần ít nhất một từ khoá hợp lệ", code="VALIDATION_ERROR", status_code=422)
        job = self.repo.add(
            ScrapeJob(
                name=payload.name,
                status=JOB_QUEUED,
                phase="idle",
                params=payload.model_dump(),
                total_queries=len(queries),
            )
        )
        self.repo.add_queries(job.id, queries)
        self.db.commit()
        self.db.refresh(job)
        return JobResponse.of(job, rate_per_min(job))

    def get(self, job_id: int) -> JobDetailResponse:
        job = self.repo.by_id(job_id, with_queries=True)
        if job is None:
            raise NotFoundError(f"Không tìm thấy job {job_id}")
        return JobDetailResponse.of_detail(job, rate_per_min(job))

    def list(self, params: PageParams, status: str | None) -> Page[JobResponse]:
        rows, total = self.repo.list(params, status)
        return Page.build([JobResponse.of(j, rate_per_min(j)) for j in rows], params, total)

    # ----- đổi trạng thái -----
    def _transition(self, job_id: int, target: str, allowed_from: tuple[str, ...]) -> JobResponse:
        job = self.repo.by_id(job_id)
        if job is None:
            raise NotFoundError(f"Không tìm thấy job {job_id}")
        if job.status not in allowed_from:
            raise AppError(
                f"Job đang ở trạng thái '{job.status}', không thể chuyển sang '{target}'",
                code="JOB_INVALID_STATE",
                status_code=409,
            )
        job.status = target
        if target == JOB_CANCELLED:
            job.finished_at = datetime.now(UTC)
            job.phase = "idle"
        self.db.commit()
        self.db.refresh(job)
        return JobResponse.of(job, rate_per_min(job))

    def pause(self, job_id: int) -> JobResponse:
        return self._transition(job_id, JOB_PAUSED, (JOB_RUNNING, JOB_QUEUED))

    def resume(self, job_id: int) -> JobResponse:
        return self._transition(job_id, JOB_QUEUED, (JOB_PAUSED,))

    def cancel(self, job_id: int) -> JobResponse:
        return self._transition(job_id, JOB_CANCELLED, (JOB_QUEUED, JOB_RUNNING, JOB_PAUSED))
