from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.pagination import PageParams
from app.modules.scraper.job.entity import JOB_QUEUED, JOB_RUNNING, JobQuery, ScrapeJob


class JobRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ----- đọc -----
    def by_id(self, job_id: int, with_queries: bool = False) -> ScrapeJob | None:
        stmt = select(ScrapeJob).where(ScrapeJob.id == job_id)
        if with_queries:
            stmt = stmt.options(selectinload(ScrapeJob.queries))
        return self.db.execute(stmt).scalar_one_or_none()

    def list(self, params: PageParams, status: str | None = None) -> tuple[list[ScrapeJob], int]:
        base = select(ScrapeJob)
        count_stmt = select(func.count(ScrapeJob.id))
        if status:
            base = base.where(ScrapeJob.status == status)
            count_stmt = count_stmt.where(ScrapeJob.status == status)
        total = int(self.db.execute(count_stmt).scalar_one())
        rows = (
            self.db.execute(
                base.order_by(ScrapeJob.id.desc()).offset(params.offset).limit(params.size)
            )
            .scalars()
            .all()
        )
        return list(rows), total

    def next_pending_query(self, job_id: int) -> JobQuery | None:
        """Lấy MỘT truy vấn còn chờ. `running` cũng được nhận lại: worker có thể đã
        chết giữa chừng, để nguyên thì truy vấn đó treo vĩnh viễn."""
        return (
            self.db.execute(
                select(JobQuery)
                .where(JobQuery.job_id == job_id, JobQuery.status.in_(("pending", "running")))
                .order_by(JobQuery.id)
                .limit(1)
            )
            .scalars()
            .first()
        )

    # ----- ghi -----
    def add(self, job: ScrapeJob) -> ScrapeJob:
        self.db.add(job)
        self.db.flush()
        return job

    def add_queries(self, job_id: int, queries: list) -> int:
        """`queries` là danh sách ExpandedQuery (query + hl + gl)."""
        for q in queries:
            self.db.add(JobQuery(job_id=job_id, query=q.query, hl=q.hl, gl=q.gl))
        self.db.flush()
        return len(queries)

    def claim_next(self) -> ScrapeJob | None:
        """Nhận một job để chạy — Postgres chính là hàng đợi.

        `FOR UPDATE SKIP LOCKED` cho phép thêm worker thứ hai (máy khác, IP khác)
        về sau mà không phải dựng thêm message broker nào.
        """
        job = (
            self.db.execute(
                select(ScrapeJob)
                .where(ScrapeJob.status.in_((JOB_QUEUED, JOB_RUNNING)))
                .order_by(ScrapeJob.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            .scalars()
            .first()
        )
        if job is None:
            return None
        if job.status == JOB_QUEUED:
            job.status = JOB_RUNNING
            job.started_at = job.started_at or datetime.now(UTC)
        self.db.flush()
        return job
