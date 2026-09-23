from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.modules.scraper.job.entity import JobQuery, ScrapeJob


class JobQueryResponse(BaseModel):
    id: int
    query: str
    status: str
    results_found: int | None
    error: str | None

    @classmethod
    def of(cls, q: JobQuery) -> JobQueryResponse:
        return cls(id=q.id, query=q.query, status=q.status, results_found=q.results_found, error=q.error)


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


class JobDetailResponse(JobResponse):
    queries: list[JobQueryResponse] = []

    @classmethod
    def of_detail(cls, job: ScrapeJob, rate_per_min: float | None = None) -> JobDetailResponse:
        base = JobResponse.of(job, rate_per_min).model_dump()
        return cls(**base, queries=[JobQueryResponse.of(q) for q in job.queries])
