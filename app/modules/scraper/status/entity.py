from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class WorkerStatus(Base):
    """Một dòng duy nhất (id=1): worker còn sống không, đang làm gì, nhịp bao nhiêu.

    API đọc bảng này để vẽ trạng thái; worker ghi mỗi `heartbeat_seconds`.
    """

    __tablename__ = "worker_status"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    worker_name: Mapped[str] = mapped_column(String(64), default="worker-1")
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_job_id: Mapped[int | None] = mapped_column(Integer)
    current_phase: Mapped[str] = mapped_column(String(16), default="idle")
    pace_seconds: Mapped[float] = mapped_column(Float, default=6.0)
    blocked_today: Mapped[int] = mapped_column(Integer, default=0)
    blocked_day: Mapped[str | None] = mapped_column(String(10))   # YYYY-MM-DD, để reset bộ đếm
    pages_last_hour: Mapped[int] = mapped_column(Integer, default=0)
    night_rest: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
