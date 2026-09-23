from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

# Trạng thái job. Lưu dạng chuỗi (không dùng ENUM của Postgres) để thêm trạng thái
# mới không phải viết migration đổi kiểu.
JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_PAUSED = "paused"
JOB_DONE = "done"
JOB_FAILED = "failed"
JOB_CANCELLED = "cancelled"

ACTIVE_JOB_STATUSES = (JOB_QUEUED, JOB_RUNNING, JOB_PAUSED)


class ScrapeJob(Base):
    __tablename__ = "scrape_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default=JOB_QUEUED, index=True)
    phase: Mapped[str] = mapped_column(String(16), default="idle")
    params: Mapped[dict] = mapped_column(JSONB, default=dict)

    total_queries: Mapped[int] = mapped_column(Integer, default=0)
    done_queries: Mapped[int] = mapped_column(Integer, default=0)
    total_places: Mapped[int] = mapped_column(Integer, default=0)
    done_places: Mapped[int] = mapped_column(Integer, default=0)
    failed_places: Mapped[int] = mapped_column(Integer, default=0)
    new_places: Mapped[int] = mapped_column(Integer, default=0)
    blocked_count: Mapped[int] = mapped_column(Integer, default=0)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    queries: Mapped[list[JobQuery]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobQuery.id"
    )


class JobQuery(Base):
    """Một truy vấn đã mở rộng: `<từ khoá> <địa điểm>`."""

    __tablename__ = "job_queries"
    __table_args__ = (UniqueConstraint("job_id", "query", name="uq_job_queries_job_query"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("scrape_jobs.id", ondelete="CASCADE"), index=True)
    query: Mapped[str] = mapped_column(String(300))
    # Ngôn ngữ + quốc gia ưu tiên khi tìm, tính theo ĐỊA ĐIỂM của chính truy vấn này.
    # Để chung một giá trị cho cả job là sai ngay khi job trải nhiều nước: tìm
    # "fruit wholesaler Bangkok" với gl=vn ra cửa hàng ở TP.HCM (đã đo thực tế).
    hl: Mapped[str] = mapped_column(String(8), default="vi")
    gl: Mapped[str] = mapped_column(String(8), default="vn")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    results_found: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job: Mapped[ScrapeJob] = relationship(back_populates="queries")
