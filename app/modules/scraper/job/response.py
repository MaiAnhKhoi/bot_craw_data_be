from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.modules.scraper.job.entity import JobQuery, ScrapeJob


class JobQueryResponse(BaseModel):
    id: int
    query: str
    status: str
    results_found: int | None
    stop_reason: str | None      # exhausted | cut_off | cap | empty | unknown
    error: str | None

    @classmethod
    def of(cls, q: JobQuery) -> JobQueryResponse:
        return cls(
            id=q.id,
            query=q.query,
            status=q.status,
            results_found=q.results_found,
            stop_reason=q.stop_reason,
            error=q.error,
        )


class JobResponse(BaseModel):
    id: int
    name: str
    status: str
    phase: str
    params: dict
    total_queries: int
    done_queries: int
    total_places: int
    done_places: int
    failed_places: int
    new_places: int
    blocked_count: int
    rate_per_min: float | None = None
    started_at: datetime | None
    finished_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, job: ScrapeJob, rate_per_min: float | None = None) -> JobResponse:
        return cls(
            id=job.id, name=job.name, status=job.status, phase=job.phase, params=job.params or {},
            total_queries=job.total_queries, done_queries=job.done_queries,
            total_places=job.total_places, done_places=job.done_places,
            failed_places=job.failed_places, new_places=job.new_places,
            blocked_count=job.blocked_count, rate_per_min=rate_per_min,
            started_at=job.started_at, finished_at=job.finished_at, last_error=job.last_error,
            created_at=job.created_at, updated_at=job.updated_at,
        )


class RemainingAreaResponse(BaseModel):
    """Một địa bàn CÒN SÓT: chuỗi truy vấn + hiện trạng của lần quét gần nhất.

    Không phải một dòng `job_queries` — là kết quả gộp mọi lần chạy của cùng một
    chuỗi truy vấn trên khắp các job. `job_id`/`job_name` vì thế là job của LẦN
    GẦN NHẤT, dùng để mở ngược về đúng chỗ đã sinh ra con số này.
    """

    query: str
    stop_reason: str            # cut_off | cap | unknown
    results_found: int | None
    finished_at: datetime | None
    job_id: int
    job_name: str

    @classmethod
    def of(cls, row) -> RemainingAreaResponse:  # noqa: ANN001 — Row của SQLAlchemy
        return cls(
            query=row.query,
            stop_reason=row.stop_reason,
            results_found=row.results_found,
            finished_at=row.finished_at,
            job_id=row.job_id,
            job_name=row.job_name,
        )


class JobDetailResponse(JobResponse):
    queries: list[JobQueryResponse] = []

    @classmethod
    def of_detail(cls, job: ScrapeJob, rate_per_min: float | None = None) -> JobDetailResponse:
        base = JobResponse.of(job, rate_per_min).model_dump()
        return cls(**base, queries=[JobQueryResponse.of(q) for q in job.queries])
