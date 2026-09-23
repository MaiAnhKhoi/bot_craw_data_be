from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.exceptions import AppError, NotFoundError
from app.core.pagination import Page, PageParams
from app.modules.geo import service as geo
from app.modules.scraper.job.entity import (
    JOB_CANCELLED,
    JOB_PAUSED,
    JOB_QUEUED,
    JOB_RUNNING,
    ScrapeJob,
)
from app.modules.scraper.job.repository import CAN_HANH_DONG, JobRepository
from app.modules.scraper.job.request import JobCreateRequest
from app.modules.scraper.job.response import (
    JobDetailResponse,
    JobResponse,
    RemainingAreaResponse,
)


@dataclass(frozen=True)
class ExpandedQuery:
    """Một truy vấn đã mở rộng, kèm ngôn ngữ/quốc gia riêng của nó."""

    query: str
    hl: str
    gl: str


def expand_queries(
    keywords: list[str],
    locations: list[str] | None,
    keyword_map: dict[str, list[str]] | None = None,
    default_locale: tuple[str, str] = ("vi", "vn"),
) -> list[ExpandedQuery]:
    """từ khoá × địa điểm, kèm ngôn ngữ/quốc gia tìm kiếm cho từng truy vấn.

    Đây là cách duy nhất vượt trần ~120 kết quả mỗi truy vấn của Google: chia nhỏ
    địa bàn ra thành nhiều truy vấn hẹp hơn.

    Hai điều làm ở đây mà nơi khác không làm được:
    1. Địa điểm ở nước nào thì dùng TỪ KHOÁ của nước đó (`keyword_map`) — từ khoá
       tiếng Việt gần như vô dụng ngoài Việt Nam.
    2. Mỗi truy vấn mang `hl`/`gl` riêng theo quốc gia của nó. Dùng chung một giá
       trị cho cả job là sai ngay khi job trải nhiều nước.
    """
    kws = [k.strip() for k in keywords if k and k.strip() and not k.strip().startswith("#")]
    locs = [x.strip() for x in (locations or []) if x and x.strip() and not x.strip().startswith("#")]
    keyword_map = {k.upper(): v for k, v in (keyword_map or {}).items() if v}

    out: list[ExpandedQuery] = []
    seen: set[str] = set()

    def add(text: str, hl: str, gl: str) -> None:
        if text not in seen:
            seen.add(text)
            out.append(ExpandedQuery(query=text, hl=hl, gl=gl))

    if not locs:
        for k in kws:
            add(k, *default_locale)
        return out

    for loc in locs:
        country = geo.resolve_country(loc)
        code = country["code"] if country else None
        hl, gl = geo.locale_for(code) if code else default_locale
        local_kws = keyword_map.get(code or "", []) or kws
        for k in local_kws:
            add(f"{k} {loc}", hl, gl)
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
        queries = expand_queries(
            payload.keywords,
            payload.locations,
            payload.keyword_map,
            default_locale=(payload.hl, payload.gl),
        )
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

    def remaining_areas(
        self, params: PageParams, stop_reason: str | None
    ) -> Page[RemainingAreaResponse]:
        """Danh sách địa bàn còn sót, gộp theo truy vấn trên mọi job.

        Lý do dừng lạ thì BÁO LỖI chứ không lặng lẽ trả về danh sách đầy đủ:
        gõ nhầm `?stop_reason=cutoff` mà vẫn thấy dữ liệu về là người dùng tin
        rằng mình đang nhìn đúng một nhóm, trong khi đang nhìn cả ba.
        """
        if stop_reason and stop_reason not in CAN_HANH_DONG:
            raise AppError(
                f"stop_reason phải là một trong {' | '.join(CAN_HANH_DONG)}",
                code="VALIDATION_ERROR",
                status_code=422,
            )
        rows, total = self.repo.list_remaining_areas(params, stop_reason)
        return Page.build([RemainingAreaResponse.of(r) for r in rows], params, total)

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
            # Chốt sổ địa điểm còn dang dở, nếu không vòng "kiểm tra lại" của
            # worker sẽ nhặt chúng lên quét tiếp và nút Huỷ thành vô nghĩa.
            from app.modules.scraper.place.writer import PlaceWriter

            PlaceWriter(self.db).close_pending_of_job(job_id)
        self.db.commit()
        self.db.refresh(job)
        return JobResponse.of(job, rate_per_min(job))

    def pause(self, job_id: int) -> JobResponse:
        return self._transition(job_id, JOB_PAUSED, (JOB_RUNNING, JOB_QUEUED))

    def resume(self, job_id: int) -> JobResponse:
        return self._transition(job_id, JOB_QUEUED, (JOB_PAUSED,))

    def cancel(self, job_id: int) -> JobResponse:
        return self._transition(job_id, JOB_CANCELLED, (JOB_QUEUED, JOB_RUNNING, JOB_PAUSED))
