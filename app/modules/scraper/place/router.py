from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import current_user, current_user_query_token
from app.core.exceptions import AppError
from app.core.pagination import Page, PageParams, page_params
from app.core.response import ApiResponse
from app.modules.identity.entity import User
from app.modules.scraper.place.export import EXPORTERS, MEDIA_TYPES
from app.modules.scraper.place.repository import PlaceFilter, PlaceRepository
from app.modules.scraper.place.response import PlaceResponse
from app.modules.scraper.place.service import PlaceService

router = APIRouter(prefix="/places", tags=["places"])


def place_filter(
    q: str | None = Query(None, description="Tìm trong tên + địa chỉ, không dấu cũng khớp"),
    job_id: int | None = Query(None),
    keyword: str | None = Query(None),
    liveness: list[str] = Query(default=[], description="ACTIVE | SUSPECT | DEAD"),
    business_status: str | None = Query(None),
    has_phone: bool | None = Query(None),
    has_website: bool | None = Query(None),
    min_rating: float | None = Query(None, ge=0, le=5),
    sort: str = Query("-liveness"),
) -> PlaceFilter:
    return PlaceFilter(
        q=q, job_id=job_id, keyword=keyword, liveness=liveness, business_status=business_status,
        has_phone=has_phone, has_website=has_website, min_rating=min_rating, sort=sort,
    )


@router.get("", response_model=ApiResponse[Page[PlaceResponse]], summary="Danh sách địa điểm")
def list_places(
    f: PlaceFilter = Depends(place_filter),
    params: PageParams = Depends(page_params),
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
) -> ApiResponse[Page[PlaceResponse]]:
    return ApiResponse.ok(PlaceService(db).list(f, params))


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

    # Lấy trước từ khoá theo lô id để tránh N+1 khi ghi file.
    ids = [p.id for p in repo.stream(f)]
    keywords = repo.keywords_for(ids)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"danh-sach-{stamp}.{fmt}"
    path = Path(settings.export_dir) / filename
    EXPORTERS[fmt](repo.stream(f), keywords, path)
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
