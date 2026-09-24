from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import AppError, NotFoundError
from app.core.pagination import Page, PageParams
from app.modules.geo import service as geo
from app.modules.scraper.job import subdivide
from app.modules.scraper.job.entity import (
    JOB_CANCELLED,
    JOB_PAUSED,
    JOB_QUEUED,
    JOB_RUNNING,
    ScrapeJob,
)
from app.modules.scraper.job.repository import CAN_HANH_DONG, JobRepository
from app.modules.scraper.job.request import JobCreateRequest, RemainingSplitRequest
from app.modules.scraper.job.response import (
    JobDetailResponse,
    JobResponse,
    PlaceRejectResponse,
    RemainingAreaResponse,
    SplitPlanItemResponse,
    SplitPlanResponse,
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
        params = payload.model_dump()
        params["category_map"] = self._danh_muc(payload, queries)
        job = self.repo.add(
            ScrapeJob(
                name=payload.name,
                status=JOB_QUEUED,
                phase="idle",
                params=params,
                total_queries=len(queries),
            )
        )
        self.repo.add_queries(job.id, queries)
        self.db.commit()
        self.db.refresh(job)
        return JobResponse.of(job, rate_per_min(job))

    def _danh_muc(
        self, payload: JobCreateRequest, queries: list[ExpandedQuery]
    ) -> dict[str, list[str]]:
        """Danh mục ngành nghề được phép giữ, theo từng quốc gia của job.

        CHỐT MỘT LẦN LÚC TẠO JOB, không tra lại lúc chạy. Job chạy hàng giờ; nếu
        worker tra bảng dịch mỗi lần ghi thì người dùng sửa danh mục giữa chừng
        sẽ khiến nửa đầu job và nửa sau job lọc theo hai luật khác nhau — và
        không có gì trong dữ liệu nói ra điều đó.

        Giao diện gửi lên được thì ưu tiên (đó là bản người dùng vừa nhìn thấy và
        sửa tay). Thiếu thì tra từ bản dịch đã lưu của chính bộ từ khoá này —
        cần thiết vì tạo job từ "bộ từ khoá đã lưu" thì giao diện không hề mở màn
        dịch nên chẳng có gì để gửi. Vẫn không có thì để trống, nghĩa là KHÔNG lọc
        quốc gia đó; thà giữ cả rác còn hơn lấy một danh mục bịa ra mà vứt lead.
        """
        ra = {
            ma.upper(): list(ds)
            for ma, ds in (payload.category_map or {}).items()
            if ma and ds
        }
        thieu = [q.gl.upper() for q in queries if q.gl and q.gl.upper() not in ra]
        if thieu:
            from app.modules.keyword.service import HOME_COUNTRY, KeywordService

            ra.update(KeywordService(self.db).categories_of(payload.keywords, thieu))
            # Việt Nam không bao giờ đi qua AI nên không có dòng nào trong bảng
            # dịch — nếu dừng ở đây thì sân nhà là nơi DUY NHẤT không được lọc.
            #
            # Lấy chính từ khoá làm danh mục CHỈ ở đây, không làm cho nước ngoài:
            # ở Việt Nam từ khoá và nhãn ngành nghề cùng một thứ tiếng nên chúng
            # khớp nhau ("vựa trái cây" ↔ "Cửa hàng bán buôn trái cây"), đo trên
            # 10 địa điểm thật ở Bến Thành thì đúng 10/10. Làm vậy với Ấn Độ là
            # sai ngay: từ khoá tiếng Hindi không có chữ nào chung với nhãn
            # "Fruit and vegetable store", đo được 15 cửa hàng trái cây thật bị
            # vứt oan.
            if HOME_COUNTRY in thieu and HOME_COUNTRY not in ra and payload.keywords:
                ra[HOME_COUNTRY] = list(payload.keywords)
        return ra

    def get(self, job_id: int) -> JobDetailResponse:
        job = self.repo.by_id(job_id, with_queries=True)
        if job is None:
            raise NotFoundError(f"Không tìm thấy job {job_id}")
        return JobDetailResponse.of_detail(job, rate_per_min(job))

    def rejects(self, job_id: int, params: PageParams) -> Page[PlaceRejectResponse]:
        """Ném 404 nếu job không tồn tại thay vì trả trang rỗng.

        Trang rỗng cho một job_id sai trông y hệt "bộ lọc không loại gì cả" —
        đúng cái kết luận nguy hiểm nhất mà màn này sinh ra để bác bỏ.
        """
        if self.repo.by_id(job_id) is None:
            raise NotFoundError(f"Không tìm thấy job {job_id}")
        rows, total = self.repo.list_rejects(job_id, params)
        return Page.build([PlaceRejectResponse.of(r) for r in rows], params, total)

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

    # ----- chia nhỏ địa bàn còn sót -----
    def _ke_hoach(self, queries: list[str]) -> subdivide.KeHoachChiaNho:
        return subdivide.lap_ke_hoach(queries, self.repo.latest_runs_for(queries))

    def plan_split(self, payload: RemainingSplitRequest) -> SplitPlanResponse:
        ke_hoach = self._ke_hoach(payload.queries)
        so_truy_van = len(ke_hoach.truy_van)
        return SplitPlanResponse(
            items=[SplitPlanItemResponse.of(m) for m in ke_hoach.muc],
            total_queries=so_truy_van,
            skipped=sum(1 for m in ke_hoach.muc if m.so_truy_van == 0),
            estimated_minutes=subdivide.so_phut_uoc_tinh(so_truy_van),
        )

    def create_split(self, payload: RemainingSplitRequest) -> JobResponse:
        """Tạo job từ các dòng đang chọn — cùng một bản kế hoạch với xem trước."""
        ke_hoach = self._ke_hoach(payload.queries)
        if not ke_hoach.truy_van:
            raise AppError(
                "Không sinh được truy vấn nào từ các dòng đã chọn. Xem cột lý do ở "
                "bản xem trước để biết vì sao.",
                code="VALIDATION_ERROR",
                status_code=422,
            )

        ten = (payload.name or "").strip() or self._ten_mac_dinh()
        # Dựng `params` qua chính JobCreateRequest để job này có cùng hình dạng
        # với job tạo bằng tay — giao diện đọc `params` theo đúng một kiểu.
        #
        # `skip_recent_queries = False` là điều kiện SỐNG CÒN chứ không phải một
        # lựa chọn: `CO_THE_BO_QUA` trong repository có cả `cut_off`, nên để mặc
        # định True thì chính những truy vấn vừa sinh ra ở đây bị bỏ qua sạch, job
        # chạy xong trong vài giây mà không quét gì.
        params = JobCreateRequest(
            name=ten,
            keywords=ke_hoach.tu_khoa,
            locations=ke_hoach.dia_diem,
            max_results_per_query=payload.max_results_per_query,
            skip_recent_queries=False,
        )
        job = self.repo.add(
            ScrapeJob(
                name=ten,
                status=JOB_QUEUED,
                phase="idle",
                params=params.model_dump(),
                total_queries=len(ke_hoach.truy_van),
            )
        )
        self.repo.add_queries(
            job.id,
            [ExpandedQuery(query=t.query, hl=t.hl, gl=t.gl) for t in ke_hoach.truy_van],
        )
        self.db.commit()
        self.db.refresh(job)
        return JobResponse.of(job, rate_per_min(job))

    @staticmethod
    def _ten_mac_dinh() -> str:
        """Tên gợi nhớ theo giờ ĐỊA PHƯƠNG: người dùng đối chiếu với đồng hồ trên
        tường chứ không với UTC."""
        gio = datetime.now(ZoneInfo(get_settings().timezone))
        return f"Chia nhỏ địa bàn còn sót {gio:%d/%m %H:%M}"

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
