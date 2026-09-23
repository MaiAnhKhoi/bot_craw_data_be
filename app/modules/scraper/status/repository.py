from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.scraper.status.entity import WorkerStatus

SINGLETON_ID = 1


class WorkerStatusRepository:
    """Một dòng duy nhất mô tả worker. Worker ghi, API đọc."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_or_create(self) -> WorkerStatus:
        row = self.db.execute(
            select(WorkerStatus).where(WorkerStatus.id == SINGLETON_ID)
        ).scalar_one_or_none()
        if row is None:
            row = WorkerStatus(id=SINGLETON_ID)
            self.db.add(row)
            self.db.flush()
        return row

    def heartbeat(
        self,
        *,
        job_id: int | None,
        phase: str,
        pace_seconds: float,
        pages_last_hour: int,
        night_rest: bool,
        blocked_increment: int = 0,
    ) -> WorkerStatus:
        row = self.get_or_create()
        now = datetime.now(UTC)
        today = now.strftime("%Y-%m-%d")
        if row.blocked_day != today:
            row.blocked_day = today
            row.blocked_today = 0
        row.blocked_today += blocked_increment
        row.last_heartbeat = now
        row.current_job_id = job_id
        row.current_phase = phase
        row.pace_seconds = pace_seconds
        row.pages_last_hour = pages_last_hour
        row.night_rest = night_rest
        self.db.commit()
        return row
