from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core import ai
from app.core.database import get_db
from app.core.deps import current_user, require_admin
from app.core.response import ApiResponse
from app.modules.identity.entity import User
from app.modules.keyword.service import (
    KeywordService,
    KeywordSetService,
    countries_of_locations,
    normalize,
    source_hash,
)

router = APIRouter(prefix="/keywords", tags=["keywords"])


class LocalizeRequest(BaseModel):
    keywords: list[str] = Field(min_length=1)
    locations: list[str] = Field(
        default_factory=list,
        description="Danh sách địa điểm; quốc gia được suy ra từ đuôi mỗi dòng",
    )
    countries: list[str] = Field(
        default_factory=list, description="Hoặc truyền thẳng mã quốc gia ISO alpha-2"
    )


class CountryKeywords(BaseModel):
    country_code: str
    country_name: str
    language: str
    keywords: list[str]
    # Dùng cho việc NGƯỢC với `keywords`: `keywords` để đi tìm, `categories` để
    # loại kết quả lạc đề sau khi tìm. Rỗng nghĩa là KHÔNG lọc gì — mọi thứ
    # Google trả về đều được ghi, kể cả tiệm bánh kem.
    categories: list[str] = Field(
        default_factory=list, description="Tên ngành nghề Google Maps được phép giữ"
    )
    source: str = Field(description="ai | cache | user | original | fallback")


class LocalizeResponse(BaseModel):
    items: list[CountryKeywords]
    ai_available: bool
    warning: str | None = None


class SaveRequest(BaseModel):
    keywords: list[str] = Field(min_length=1, description="Bộ từ khoá gốc, để tính khoá đệm")
    country_code: str
    language: str = "en"
    translated: list[str]
    # Bỏ trống (None) = lần lưu này không nói gì về danh mục, giữ nguyên cái đang
    # có. Gửi `[]` mới là cố ý xoá sạch. Hai ca này phải khác nhau, nếu không mọi
    # lần sửa từ khoá sẽ âm thầm tắt bộ lọc ngành nghề.
    categories: list[str] | None = None


def _countries_of(payload: LocalizeRequest) -> list[dict]:
    """Danh sách quốc gia của một yêu cầu: suy từ đuôi các dòng địa điểm, cộng
    thêm mã truyền thẳng (nếu có). Dùng chung cho cả `/plan` lẫn `/localize` để
    hai nơi không bao giờ đếm ra hai con số khác nhau."""
    from app.modules.geo import service as geo

    countries = countries_of_locations(payload.locations)
    known = {c["code"] for c in countries}
    for code in payload.countries:
        c = geo.load().country(code.upper())
        if c and c["code"] not in known:
            countries.append(c)
            known.add(c["code"])
    return countries


class PlanResponse(BaseModel):
    """Xem trước một lượt dịch, không gọi AI."""

    total: int = Field(description="Tổng số quốc gia trong danh sách địa điểm")
    home: list[str] = Field(description="Việt Nam — dùng thẳng từ khoá gốc, không cần dịch")
    cached: list[str] = Field(description="Đã có bản dịch lưu sẵn, không tốn lượt gọi AI")
    need: list[str] = Field(description="Thật sự cần gọi AI lần này")
    limit: int = Field(description="Trần số quốc gia cho MỘT lượt gọi")
    over_limit: bool = Field(description="`need` vượt `limit` -> phải bớt địa điểm đi")
    ai_available: bool


@router.post(
    "/plan",
    response_model=ApiResponse[PlanResponse],
    summary="Xem trước lượt dịch: bao nhiêu nước cần gọi AI, có vượt trần không",
)
def plan(
    payload: LocalizeRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> ApiResponse[PlanResponse]:
    """Rẻ và không đụng tới AI — chỉ đếm và tra bộ nhớ đệm. Nhờ vậy giao diện gọi
    được mỗi khi người dùng sửa từ khoá/địa điểm để chặn ngay tại chỗ."""
    result = KeywordService(db).plan(payload.keywords, _countries_of(payload))
    return ApiResponse.ok(PlanResponse(**result))


class KeywordSetResponse(BaseModel):
    """Một bộ từ khoá đã lưu."""

    id: int
    name: str
    keywords: list[str]
    # Các nước ĐÃ có bản dịch sẵn cho đúng bộ này. Chọn nước nằm trong danh sách
    # này thì KHÔNG tốn lượt gọi AI — đó là toàn bộ lý do tính năng này tồn tại.
    translated_countries: list[str]
    created_at: datetime
    updated_at: datetime


class KeywordSetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    keywords: list[str] = Field(min_length=1)


@router.get(
    "/sets",
    response_model=ApiResponse[list[KeywordSetResponse]],
    summary="Các bộ từ khoá đã lưu, kèm số nước đã dịch sẵn",
)
def list_sets(
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
) -> ApiResponse[list[KeywordSetResponse]]:
    return ApiResponse.ok([KeywordSetResponse(**b) for b in KeywordSetService(db).list()])


@router.post(
    "/sets",
    response_model=ApiResponse[KeywordSetResponse],
    summary="Lưu bộ từ khoá (trùng tên thì ghi đè)",
)
def save_set(
    payload: KeywordSetRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> ApiResponse[KeywordSetResponse]:
    """Ghi đè theo TÊN, không phân biệt hoa thường.

    Lưu lại cùng một bộ từ khoá KHÔNG làm mất bản dịch: bản dịch nằm ở bảng khác,
    nối qua `source_hash` của chính bộ từ khoá đó.
    """
    bo = KeywordSetService(db).save(payload.name, payload.keywords)
    return ApiResponse.ok(KeywordSetResponse(**bo))


@router.delete(
    "/sets/{set_id}",
    response_model=ApiResponse[dict],
    summary="Xoá một bộ từ khoá đã lưu",
)
def delete_set(
    set_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> ApiResponse[dict]:
    """CHỈ xoá bộ, KHÔNG đụng tới bản dịch đã có — đó là thứ đã trả tiền để có."""
    KeywordSetService(db).delete(set_id)
    return ApiResponse.ok({"deleted": True})


@router.get("/status", response_model=ApiResponse[dict], summary="AI có sẵn sàng không")
def status(_: User = Depends(current_user)) -> ApiResponse[dict]:
    return ApiResponse.ok({"ai_available": ai.is_enabled()})


@router.post(
    "/localize",
    response_model=ApiResponse[LocalizeResponse],
    summary="Gợi ý từ khoá tìm kiếm bản địa theo từng quốc gia",
)
def localize(
    payload: LocalizeRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> ApiResponse[LocalizeResponse]:
    """Không bao giờ trả lỗi vì AI.

    AI tắt, sai khoá hay quá hạn mức thì vẫn trả về từ khoá gốc kèm `warning` —
    người dùng tạo job được như thường, chỉ là không có bản địa hoá.
    """
    countries = _countries_of(payload)
    items, warning = KeywordService(db).localize(payload.keywords, countries)
    return ApiResponse.ok(
        LocalizeResponse(
            items=[CountryKeywords(**i) for i in items],
            ai_available=ai.is_enabled(),
            warning=warning,
        )
    )


@router.post(
    "/save",
    response_model=ApiResponse[dict],
    summary="Lưu bản từ khoá người dùng đã sửa tay",
)
def save(
    payload: SaveRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> ApiResponse[dict]:
    """Bản người sửa được đánh dấu `edited_by_user` và KHÔNG bị AI ghi đè lần sau."""
    keywords = normalize(payload.keywords)
    service = KeywordService(db)
    service.save(
        source_hash(keywords),
        keywords,
        payload.country_code.upper(),
        payload.language,
        normalize(payload.translated),
        model=None,
        edited=True,
        categories=(
            None if payload.categories is None else normalize(payload.categories)
        ),
    )
    db.commit()
    return ApiResponse.ok({"saved": True})
