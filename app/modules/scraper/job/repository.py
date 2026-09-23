from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.pagination import PageParams
from app.modules.scraper.job.entity import JOB_QUEUED, JOB_RUNNING, JobQuery, ScrapeJob

# Lý do dừng mà CHẠY LẠI CŨNG RA Y HỆT — chỉ những ca này mới được bỏ qua.
#
# Cố ý liệt kê cái ĐƯỢC PHÉP thay vì loại trừ cái không được: thêm một lý do dừng
# mới trong tương lai sẽ mặc định là "phải chạy lại", tức là nhầm về phía tốn thêm
# thời gian, không phải về phía âm thầm bỏ sót dữ liệu.
#
# Hai lý do KHÔNG có trong danh sách, và vì sao:
#   cap     — dừng vì chạm trần của CHÍNH NGƯỜI DÙNG. Giao diện đang bảo họ
#             "nâng trần rồi chạy lại"; nâng trần xong mà vẫn bỏ qua thì lời
#             khuyên đó thành bẫy.
#   unknown — danh sách kết quả không hiện ra (mạng chậm, màn consent, bị nghi).
#             Giao diện bảo "nên chạy lại truy vấn này". Một hỏng hóc tạm thời 30
#             giây mà khoá luôn địa bàn đó suốt `ttl_days` (mặc định 90 ngày).
#
# `stop_reason` NULL cũng không được bỏ qua: đó là dữ liệu quét từ trước khi có
# cột này, không biết lần đó có trọn vẹn hay không. Thà quét lại một lần.
CO_THE_BO_QUA = ("exhausted", "cut_off", "empty")

# Lý do dừng nghĩa là "địa bàn này còn việc phải làm" — xem `engine.models.STOP_*`.
#   cut_off  Google ngắt giữa chừng  -> chia nhỏ địa bàn xuống phường/xã
#   cap      chạm trần của chính người dùng -> nâng trần rồi chạy lại
#   unknown  danh sách không hiện ra -> chạy lại
# `exhausted` và `empty` KHÔNG nằm đây: đã lấy trọn hoặc thật sự không có gì.
CAN_HANH_DONG = ("cut_off", "cap", "unknown")

# Những lý do dừng chứng tỏ đã có MỘT LẦN QUÉT THẬT xảy ra.
#
# Đây là điều kiện để một dòng được tính là "lần chạy gần nhất" của truy vấn, và
# nó loại ra hai thứ có `finished_at` mới tinh nhưng KHÔNG hề chạm tới Google:
#   recent — bị bỏ qua vì chính truy vấn này vừa quét gần đây. Oái oăm ở chỗ
#            `CO_THE_BO_QUA` phía trên có cả `cut_off`, nên một tỉnh bị Google
#            cắt vẫn bị các job sau bỏ qua. Nếu tính lượt bỏ qua đó là lần chạy
#            mới nhất thì tỉnh còn sót sẽ BIẾN MẤT khỏi danh sách này đúng vào
#            lúc nó vẫn còn sót — hỏng nặng hơn là không có trang.
#   NULL   — truy vấn lỗi giữa chừng (`status = failed`) hoặc chưa chạy. Một lần
#            hỏng cũng không chứng minh được địa bàn đã trọn vẹn.
LAN_QUET_THAT = ("exhausted", "cut_off", "cap", "empty", "unknown")


class LanChay(Protocol):
    """Hình dạng tối thiểu của một dòng `job_queries` để gộp theo truy vấn.

    Khai bằng Protocol để hai hàm thuần dưới đây nhận được cả `Row` của SQLAlchemy
    lẫn object giả trong test — chỗ dễ sai nhất của tính năng này là luật "lần
    chạy gần nhất", mà luật đó không cần Postgres mới kiểm được.
    """

    id: int
    query: str
    stop_reason: str | None
    finished_at: datetime | None


def _thu_tu_lan_chay(row: LanChay) -> tuple:
    """Khoá so sánh "lần nào mới hơn".

    `finished_at` đứng trước nhưng có thể NULL, nên phần tử đầu là cờ "đã có mốc
    thời gian chưa" — thiếu nó thì so None với datetime là ném TypeError. `id`
    đứng cuối để hai lần chạy trùng mốc thời gian vẫn ra thứ tự xác định, không
    phụ thuộc thứ tự dòng trả về từ DB.
    """
    return (row.finished_at is not None, row.finished_at, row.id)


def latest_run_per_query(rows: Iterable[LanChay]) -> list[LanChay]:
    """Mỗi CHUỖI TRUY VẤN chỉ còn lại lần quét thật gần nhất của nó.

    Một truy vấn như "vựa trái cây Đồng Tháp" xuất hiện ở mọi job từng quét tỉnh
    đó. Chỉ lần gần nhất mới nói lên hiện trạng: chạy lại mà ra `exhausted` thì
    địa bàn đã xong, dù mười lần trước đều `cut_off`.
    """
    moi_nhat: dict[str, LanChay] = {}
    for row in rows:
        if row.stop_reason not in LAN_QUET_THAT:
            continue
        cu = moi_nhat.get(row.query)
        if cu is None or _thu_tu_lan_chay(row) > _thu_tu_lan_chay(cu):
            moi_nhat[row.query] = row
    return list(moi_nhat.values())


def remaining_areas(rows: Iterable[LanChay], stop_reason: str | None = None) -> list[LanChay]:
    """Địa bàn còn sót: gộp theo truy vấn -> lần gần nhất -> lọc nhóm cần hành động.

    Thứ tự LỌC SAU KHI GỘP là bắt buộc, không phải tuỳ ý. Lọc trước rồi mới gộp
    sẽ lôi lại lần `cut_off` cũ của một truy vấn đã chạy lại thành công — đúng
    thứ trang này sinh ra để loại bỏ.

    Xếp mới nhất lên đầu: quét xong một lượt 34 tỉnh thì việc cần làm ngay là
    nhìn kết quả của chính lượt vừa chạy.
    """
    # Bộ lọc chỉ được THU HẸP nhóm cần hành động, không mở rộng ra ngoài: hỏi
    # `stop_reason=exhausted` ở đây là hỏi sai câu — trang này không nói về
    # những địa bàn đã xong.
    can_lay = tuple(r for r in CAN_HANH_DONG if stop_reason in (None, r))
    con_sot = [row for row in latest_run_per_query(rows) if row.stop_reason in can_lay]
    con_sot.sort(key=_thu_tu_lan_chay, reverse=True)
    return con_sot


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

    def list_remaining_areas(
        self, params: PageParams, stop_reason: str | None = None
    ) -> tuple[list, int]:
        """Địa bàn còn sót trên TOÀN BỘ các job, đã phân trang.

        Gộp theo chuỗi truy vấn nên phải nhìn qua ranh giới job: câu hỏi của
        người dùng sau một lượt quét 34 tỉnh là "tỉnh nào còn sót", không phải
        "job số 12 còn sót gì".

        SQL chỉ làm việc THU HẸP, còn luật "lần chạy nào mới nhất" để cho hàm
        thuần ở trên xử (xem `remaining_areas`). Thu hẹp bằng cách nào cũng phải
        an toàn: chỉ những chuỗi truy vấn từng có ít nhất một lần dừng vì lý do
        đang hỏi mới có cửa lọt vào kết quả, nên lọc trước ở tầng chuỗi truy vấn
        không bỏ sót dòng nào. Nhưng vẫn phải kéo về MỌI lần chạy của các chuỗi
        đó — kể cả lần `exhausted` mới nhất — nếu không sẽ không biết truy vấn
        nào đã được chạy lại thành công.
        """
        can_tim = tuple(r for r in CAN_HANH_DONG if stop_reason in (None, r))
        ung_vien = select(JobQuery.query).where(JobQuery.stop_reason.in_(can_tim)).distinct()
        rows = self.db.execute(
            select(
                JobQuery.id,
                JobQuery.query,
                JobQuery.stop_reason,
                JobQuery.results_found,
                JobQuery.finished_at,
                JobQuery.job_id,
                ScrapeJob.name.label("job_name"),
            )
            .join(ScrapeJob, ScrapeJob.id == JobQuery.job_id)
            .where(JobQuery.query.in_(ung_vien))
        ).all()
        con_sot = remaining_areas(rows, stop_reason)
        return con_sot[params.offset : params.offset + params.size], len(con_sot)

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

    def recently_scraped_at(
        self, query: str, within_days: int, exclude_query_id: int, gl: str | None = None
    ) -> datetime | None:
        """Lần gần nhất CHÍNH truy vấn này chạy xong, nếu còn trong hạn.

        Đây là chỗ tiết kiệm lớn nhất khi chạy lại một địa bàn rộng. `ttl_days`
        sẵn có chỉ chặn ở pha CHI TIẾT: pha tìm kiếm vẫn mở trang, cuộn hết
        danh sách, bóc từng thẻ — tốn 1-3 phút — rồi mới phát hiện cả 120 địa
        điểm đều đã có trong kho. Quét lại cả nước là hàng nghìn truy vấn như vậy.

        Cố ý so khớp theo CHUỖI TRUY VẤN chứ không theo job: giá trị nằm ở chỗ
        job hôm nay nhận ra job tuần trước đã quét địa bàn đó rồi.
        """
        if within_days <= 0:
            return None
        moc = datetime.now(UTC) - timedelta(days=within_days)
        dieu_kien = [
            JobQuery.query == query,
            JobQuery.status == "done",
            # Chỉ bỏ qua khi lần trước dừng vì một lý do mà chạy lại cũng ra y hệt.
            JobQuery.stop_reason.in_(CO_THE_BO_QUA),
            JobQuery.finished_at.isnot(None),
            JobQuery.finished_at >= moc,
            JobQuery.id != exclude_query_id,
        ]
        if gl:
            # Cùng một chuỗi truy vấn nhưng khác `gl` là HAI lần tìm khác nhau:
            # "fruit wholesaler Bangkok" với gl=vn trả về cửa hàng ở TP.HCM, với
            # gl=th mới ra Thái Lan (đã đo). Không so `gl` thì người dùng phát
            # hiện mình chọn sai nước, sửa lại rồi chạy lại — và bị bỏ qua.
            dieu_kien.append(JobQuery.gl == gl)
        return self.db.execute(
            select(func.max(JobQuery.finished_at)).where(*dieu_kien)
        ).scalar_one_or_none()

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
