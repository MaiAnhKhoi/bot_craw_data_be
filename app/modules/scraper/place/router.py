from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.database import SessionLocal, get_db
from app.core.deps import current_user, current_user_query_token
from app.core.exceptions import AppError
from app.core.pagination import Page, PageParams, page_params
from app.core.response import ApiResponse
from app.modules.identity.entity import User
from app.modules.scraper.place.entity import Place
from app.modules.scraper.place.export import EXPORTERS, MEDIA_TYPES
from app.modules.scraper.place.repository import PlaceFilter, PlaceRepository
from app.modules.scraper.place.response import (
    CountryCountResponse,
    PlaceResponse,
    QueryCountResponse,
)
from app.modules.scraper.place.service import PlaceService

router = APIRouter(prefix="/places", tags=["places"])


def place_filter(
    q: str | None = Query(None, description="Tìm trong tên + địa chỉ, không dấu cũng khớp"),
    job_id: int | None = Query(None),
    keyword: str | None = Query(None),
    country: str | None = Query(None, description="Mã ISO alpha-2, ví dụ TH; không phân biệt hoa thường"),
    contact_status: str | None = Query(
        None, description="Trạng thái chăm sóc: new | called | interested | rejected"
    ),
    liveness: list[str] = Query(default=[], description="ACTIVE | SUSPECT | DEAD"),
    business_status: str | None = Query(None),
    has_phone: bool | None = Query(None),
    has_website: bool | None = Query(None),
    min_rating: float | None = Query(None, ge=0, le=5),
    sort: str = Query("-liveness"),
) -> PlaceFilter:
    return PlaceFilter(
        q=q, job_id=job_id, keyword=keyword, country=country,
        contact_status=contact_status, liveness=liveness,
        business_status=business_status, has_phone=has_phone, has_website=has_website,
        min_rating=min_rating, sort=sort,
    )


@router.get("", response_model=ApiResponse[Page[PlaceResponse]], summary="Danh sách địa điểm")
def list_places(
    f: PlaceFilter = Depends(place_filter),
    params: PageParams = Depends(page_params),
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
) -> ApiResponse[Page[PlaceResponse]]:
    return ApiResponse.ok(PlaceService(db).list(f, params))


# ⚠️ Mọi đường dẫn CỐ ĐỊNH ("/countries", "/export") phải khai báo TRƯỚC
# "/{place_id}": FastAPI khớp route theo thứ tự khai báo, đặt sau thì "countries"
# rơi vào chỗ của place_id và endpoint trả 422 thay vì dữ liệu.
class ContactRequest(BaseModel):
    """Cập nhật việc chăm sóc một lead."""

    status: str = Field(description="new | called | interested | rejected")
    # None = giữ nguyên ghi chú đang có; chuỗi rỗng = xoá ghi chú.
    note: str | None = Field(None, max_length=2000)


@router.patch(
    "/{place_id}/contact",
    response_model=ApiResponse[PlaceResponse],
    summary="Ghi lại đã liên hệ tới đâu (đã gọi / quan tâm / loại)",
)
def set_contact(
    place_id: int,
    payload: ContactRequest,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
) -> ApiResponse[PlaceResponse]:
    return ApiResponse.ok(PlaceService(db).set_contact(place_id, payload.status, payload.note))


@router.get(
    "/queries",
    response_model=ApiResponse[list[QueryCountResponse]],
    summary="Mọi lượt tìm đã sinh ra dữ liệu, kèm số địa điểm",
)
def list_queries(
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
) -> ApiResponse[list[QueryCountResponse]]:
    """Nguồn cho ô lọc "Lượt tìm" ở màn Địa điểm.

    Phải là endpoint riêng chứ không mượn `overview.top_keywords`: cái đó có
    LIMIT 10 nên từ lượt tìm thứ 11 trở đi sẽ không lọc được, và không có gì báo.
    """
    return ApiResponse.ok(PlaceService(db).queries())


@router.get(
    "/countries",
    response_model=ApiResponse[list[CountryCountResponse]],
    summary="Các quốc gia đang có dữ liệu, kèm số địa điểm",
)
def list_countries(
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
) -> ApiResponse[list[CountryCountResponse]]:
    return ApiResponse.ok(PlaceService(db).countries())


# --- nhịp cho luồng SSE ở dưới ---
#
# `pulse()` giờ đã rẻ (V0006 thêm chỉ mục cho ba cột `max()`, và câu lệnh bỏ hẳn
# `count(*)` — thứ ép quét toàn bảng mà không ai dùng tới). Đo ở 300.000 dòng:
# 70 ms trước, 2,4 ms sau.
#
# Hai lớp bảo vệ dưới đây vẫn giữ, vì chúng chống lại thứ khác chứ không phải
# chống chậm:
#   1. SQLAlchemy ĐỒNG BỘ nằm trong `async def` thì dù nhanh cỡ nào cũng vẫn chặn
#      event loop của uvicorn. 2,4 ms mỗi giây là không đáng kể, nhưng một lần DB
#      chậm bất thường (khoá, checkpoint) sẽ làm đứng cả tiến trình. Threadpool là
#      để chuyện đó không bao giờ thành sự cố.
#   2. Mỗi tab là một kết nối SSE riêng, và N tab luôn hỏi cùng một câu để nhận
#      cùng một đáp án. Bộ đệm gom chúng lại: tải xuống DB bị chặn trên chứ không
#      tăng theo số tab.
#
# Cố ý KHÔNG khoá (asyncio.Lock) quanh bộ đệm: hai kết nối trùng đúng khoảnh khắc
# hết hạn sẽ cùng đi hỏi, và đó chính xác là hành vi cũ — không tệ hơn. Đổi lại
# không phải ôm một Lock cấp module, thứ gắn chặt vào event loop tạo ra nó và sẽ nổ
# nếu tiến trình từng chạy trên một loop khác (test, script).
_PULSE_TTL = 1.0
_pulse_cache: tuple[float, tuple[dict[str, object], bool]] | None = None


def _doc_nhip() -> tuple[dict[str, object], bool]:
    """Phần CHẠM DB của luồng SSE. Đồng bộ — chỉ được gọi qua threadpool."""
    db = SessionLocal()
    try:
        from app.modules.scraper.status.repository import WorkerStatusRepository

        max_id, last_change = PlaceRepository(db).pulse()
        worker = WorkerStatusRepository(db).get_or_create()
        dang_quet = worker.current_job_id is not None
    finally:
        db.close()
    return (
        {
            "max_id": max_id,
            "last_change": last_change.isoformat() if last_change else None,
        },
        dang_quet,
    )


async def _nhip_dia_diem() -> tuple[dict[str, object], bool]:
    global _pulse_cache

    now = time.monotonic()
    if _pulse_cache is not None and now - _pulse_cache[0] < _PULSE_TTL:
        return _pulse_cache[1]
    ket_qua = await run_in_threadpool(_doc_nhip)
    _pulse_cache = (time.monotonic(), ket_qua)
    return ket_qua


@router.get("/events", summary="Báo bảng địa điểm có dữ liệu mới (SSE)")
async def place_events(_: User = Depends(current_user_query_token)):  # noqa: ANN201
    """Giữ một kết nối, chỉ bắn tin KHI dữ liệu thật sự đổi.

    Bảng địa điểm trước đây đứng im trong lúc worker đang cào: không có nhịp làm
    mới, `refetchOnWindowFocus` lại tắt toàn cục, nên phải tự bấm F5 mới thấy
    dòng mới — rất dễ tưởng job bị treo.

    Vì sao SSE chứ không để trình duyệt tự hỏi lại: mỗi lượt hỏi của trình duyệt
    kéo theo câu truy vấn DANH SÁCH đầy đủ (lọc + sắp + đếm phân trang) trên bảng
    có thể lên tới hàng trăm nghìn dòng. Ở đây chỉ chạy câu `pulse()` — rẻ hơn hẳn,
    dù vẫn không miễn phí (xem ghi chú ở `_nhip_dia_diem` phía trên) — và chỉ khi nó
    đổi mới báo cho giao diện đi nạp lại. Đứng yên thì không tốn gì.

    Nhịp bám theo worker: worker rảnh thì dữ liệu KHÔNG THỂ đổi (chỉ worker mới
    ghi vào bảng places), nên giãn ra 10 giây. Máy văn phòng mở cả ngày, hỏi 2
    giây một lần suốt 8 tiếng để chờ một thứ không thể xảy ra là lãng phí thuần.
    """
    NHIP_DANG_QUET = 2.0
    NHIP_RANH = 10.0

    async def stream():  # noqa: ANN202
        truoc = None
        while True:
            hien_tai, dang_quet = await _nhip_dia_diem()

            if hien_tai != truoc:
                # Lần đầu cũng bắn: giao diện dùng nó để biết kết nối đã sống.
                yield {"event": "places", "data": json.dumps(hien_tai, ensure_ascii=False)}
                truoc = hien_tai

            await asyncio.sleep(NHIP_DANG_QUET if dang_quet else NHIP_RANH)

    return EventSourceResponse(stream())


def _dong_kem_tu_khoa(
    repo: PlaceRepository,
    f: PlaceFilter,
    kho_tu_khoa: dict[int, list[str]],
    lo: int = 1_000,
) -> Iterator[Place]:
    """Duyệt bảng ĐÚNG MỘT LƯỢT, vừa đi vừa nạp từ khoá cho lô sắp ghi.

    Trước đây `repo.stream(f)` được gọi hai lần — một lượt gom id để tra từ khoá,
    một lượt nữa để ghi file. Worker thì ghi vào bảng places liên tục, nên dòng nào
    được chèn vào giữa hai lượt sẽ CÓ trong file nhưng KHÔNG có trong danh sách id:
    cột "Từ khoá tìm ra" của nó bỏ trắng, không lỗi, không cảnh báo, và người nhận
    file không có cách nào biết ô trống đó là "chưa gắn từ khoá" hay "đã mất".

    Không gom id trước rồi mới ghi được, vì làm thế là giữ cả 100.000 đối tượng
    Place trong RAM — đúng thứ mà `yield_per` + `constant_memory` sinh ra để tránh.
    Nên đệm theo lô: giữ tối đa `lo` dòng, tra từ khoá cho đúng lô đó, nhả ra cho
    bên ghi file, rồi quên.

    `kho_tu_khoa` được BỔ SUNG dần chứ không xoá đi làm lại từng lô. Nó là cùng một
    đối tượng dict mà `EXPORTERS` đang cầm, và từ khoá của một dòng luôn có mặt
    TRƯỚC khi dòng đó được nhả ra — nhờ vậy hàm này đúng cả khi bên ghi file đọc
    theo luồng (hiện tại) lẫn khi ai đó sau này nạp hết ra list rồi mới ghi. Dựng
    dict đầy đủ như vậy cũng không tốn hơn cách cũ, vì cách cũ đã tra sẵn từ khoá
    cho toàn bộ kết quả rồi.
    """
    dem: list[Place] = []
    for place in repo.stream(f, chunk=lo):
        dem.append(place)
        if len(dem) >= lo:
            kho_tu_khoa.update(repo.keywords_for([p.id for p in dem]))
            yield from dem
            dem = []
    if dem:
        kho_tu_khoa.update(repo.keywords_for([p.id for p in dem]))
        yield from dem


@router.get("/export", summary="Xuất Excel / CSV / JSON theo đúng bộ lọc đang xem")
def export_places(
    f: PlaceFilter = Depends(place_filter),
    fmt: str = Query("xlsx", alias="format", description="xlsx | csv | json"),
    db: Session = Depends(get_db),
    # Trình duyệt tải file bằng thẻ <a download> nên không gắn được header —
    # endpoint này chấp nhận token qua query string, xem app/core/deps.py.
    _: User = Depends(current_user_query_token),
):  # noqa: ANN201
    fmt = fmt.lower()
    if fmt not in EXPORTERS:
        raise AppError("Định dạng phải là xlsx, csv hoặc json", code="VALIDATION_ERROR", status_code=422)

    settings = get_settings()
    repo = PlaceRepository(db)
    total = repo.count(f)
    if total > settings.export_max_rows:
        raise AppError(
            f"Kết quả {total} dòng vượt mức cho phép {settings.export_max_rows}. Hãy lọc hẹp lại.",
            code="EXPORT_TOO_LARGE",
            status_code=413,
        )

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"danh-sach-{stamp}.{fmt}"
    path = Path(settings.export_dir) / filename
    # Một dict rỗng được truyền vào và ĐỔ ĐẦY dần trong lúc duyệt — xem
    # `_dong_kem_tu_khoa`. Vẫn theo lô để không N+1, nhưng chỉ một lượt SELECT.
    keywords: dict[int, list[str]] = {}
    EXPORTERS[fmt](_dong_kem_tu_khoa(repo, f, keywords), keywords, path)
    return FileResponse(path, media_type=MEDIA_TYPES[fmt], filename=filename)


@router.get("/{place_id}", response_model=ApiResponse[PlaceResponse], summary="Chi tiết địa điểm")
def get_place(
    place_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
) -> ApiResponse[PlaceResponse]:
    return ApiResponse.ok(PlaceService(db).get(place_id))


@router.post(
    "/{place_id}/reverify",
    response_model=ApiResponse[PlaceResponse],
    summary="Xếp hàng quét lại địa điểm này",
)
def reverify(
    place_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
) -> ApiResponse[PlaceResponse]:
    return ApiResponse.ok(PlaceService(db).reverify(place_id))
