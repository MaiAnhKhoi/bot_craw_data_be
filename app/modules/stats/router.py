from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import current_user
from app.core.response import ApiResponse
from app.modules.identity.entity import User
from app.modules.scraper.place.entity import PLACE_DONE, PLACE_FAILED, PLACE_PENDING, Place, PlaceKeyword
from app.modules.scraper.status.repository import WorkerStatusRepository

router = APIRouter(tags=["stats"])


class DayCount(BaseModel):
    date: str
    count: int


class KeywordCount(BaseModel):
    keyword: str
    count: int


class OverviewResponse(BaseModel):
    total: int
    done: int
    pending: int
    failed: int
    by_liveness: dict[str, int]
    completeness: dict[str, int]
    today_new: int
    last_14_days: list[DayCount]
    top_keywords: list[KeywordCount]


class WorkerStatusResponse(BaseModel):
    alive: bool
    last_heartbeat: datetime | None
    current_job_id: int | None
    current_phase: str
    pace_seconds: float
    blocked_today: int
    pages_last_hour: int
    night_rest: bool


@router.get("/stats/overview", response_model=ApiResponse[OverviewResponse], summary="Số liệu tổng quan")
def overview(db: Session = Depends(get_db), _: User = Depends(current_user)) -> ApiResponse[OverviewResponse]:
    by_status = dict(
        db.execute(select(Place.status, func.count()).group_by(Place.status)).all()
    )
    by_liveness = dict(
        db.execute(
            select(Place.liveness_label, func.count())
            .where(Place.status == PLACE_DONE)
            .group_by(Place.liveness_label)
        ).all()
    )
    completeness = {
        "phone": int(db.execute(select(func.count()).where(Place.phone_e164.isnot(None))).scalar_one()),
        "website": int(db.execute(select(func.count()).where(Place.website.isnot(None))).scalar_one()),
        "address": int(db.execute(select(func.count()).where(Place.address.isnot(None))).scalar_one()),
    }

    # ⚠️ "Ngày" ở đây phải do MỘT múi giờ duy nhất định nghĩa, và múi giờ đó là
    # `settings.timezone` (giờ VN) — giờ mà người xem biểu đồ đang sống.
    #
    # Trước đây hai vế so với nhau bằng hai lịch khác nhau: `func.date()` để
    # Postgres tự quyết, mà container `db` không đặt TZ nên nó cắt ngày theo UTC;
    # còn `date.today()` chạy trong container app (ENV TZ=Asia/Ho_Chi_Minh) nên trả
    # về ngày VN. Lệch 7 tiếng, và hậu quả rơi đúng vào ca đêm: mọi địa điểm quét
    # trong khoảng 00:00-07:00 giờ VN bị Postgres xếp vào ngày HÔM TRƯỚC, nên ô
    # "Hôm nay" hiện 0 trong khi worker vừa cào được vài chục dòng.
    #
    # Ép ngay trong câu truy vấn thay vì đặt TZ cho container db: cắt ngày là quy
    # tắc NGHIỆP VỤ của biểu đồ này, để nó phụ thuộc vào biến môi trường của một
    # container khác thì một hôm nào đó dựng lại hạ tầng là sai lại, âm thầm.
    ten_mui_gio = get_settings().timezone
    mui_gio = ZoneInfo(ten_mui_gio)
    today = datetime.now(mui_gio).date()
    # Mốc dưới là 00:00 giờ VN của ngày đầu khung, không phải "13 ngày trước tính từ
    # bây giờ" — kiểu cũ cắt mất phần đầu của chính ngày xa nhất trên biểu đồ.
    since = datetime.combine(today - timedelta(days=13), time.min, tzinfo=mui_gio)
    # Lọc vẫn chạy trên cột GỐC (`first_seen_at >= since`) để còn dùng được chỉ mục;
    # chỉ phần gom nhóm mới đổi sang giờ VN.
    ngay_vn = func.date(func.timezone(ten_mui_gio, Place.first_seen_at))
    day_rows = db.execute(
        select(ngay_vn, func.count())
        .where(Place.first_seen_at >= since)
        .group_by(ngay_vn)
    ).all()
    per_day = {str(d): int(c) for d, c in day_rows}
    last_14 = [
        DayCount(date=str(today - timedelta(days=i)), count=per_day.get(str(today - timedelta(days=i)), 0))
        for i in range(13, -1, -1)
    ]

    top = db.execute(
        select(PlaceKeyword.keyword, func.count().label("n"))
        .group_by(PlaceKeyword.keyword)
        .order_by(func.count().desc())
        .limit(10)
    ).all()

    return ApiResponse.ok(
        OverviewResponse(
            total=sum(by_status.values()),
            done=by_status.get(PLACE_DONE, 0),
            pending=by_status.get(PLACE_PENDING, 0),
            failed=by_status.get(PLACE_FAILED, 0),
            by_liveness={
                "ACTIVE": by_liveness.get("ACTIVE", 0),
                "SUSPECT": by_liveness.get("SUSPECT", 0),
                "DEAD": by_liveness.get("DEAD", 0),
            },
            completeness=completeness,
            today_new=per_day.get(str(today), 0),
            last_14_days=last_14,
            top_keywords=[KeywordCount(keyword=k, count=int(n)) for k, n in top],
        )
    )


@router.get("/stats/worker", response_model=ApiResponse[WorkerStatusResponse], summary="Tình trạng worker")
def worker_status(
    db: Session = Depends(get_db), _: User = Depends(current_user)
) -> ApiResponse[WorkerStatusResponse]:
    row = WorkerStatusRepository(db).get_or_create()
    db.commit()
    settings = get_settings()
    # Coi là còn sống nếu nhịp tim gần đây hơn 3 chu kỳ — đủ rộng để không báo
    # động giả khi worker đang bận một trang chậm.
    alive = False
    if row.last_heartbeat:
        age = (datetime.now(UTC) - row.last_heartbeat).total_seconds()
        alive = age < settings.heartbeat_seconds * 3
    return ApiResponse.ok(
        WorkerStatusResponse(
            alive=alive,
            last_heartbeat=row.last_heartbeat,
            current_job_id=row.current_job_id,
            current_phase=row.current_phase,
            pace_seconds=row.pace_seconds,
            blocked_today=row.blocked_today,
            pages_last_hour=row.pages_last_hour,
            night_rest=row.night_rest,
        )
    )
