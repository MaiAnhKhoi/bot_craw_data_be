from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core import ai
from app.core.database import get_db
from app.core.deps import current_user
from app.core.response import ApiResponse
from app.modules.identity.entity import User
from app.modules.keyword.service import KeywordService, countries_of_locations, normalize, source_hash

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
    _: User = Depends(current_user),
) -> ApiResponse[LocalizeResponse]:
    """Không bao giờ trả lỗi vì AI.

    AI tắt, sai khoá hay quá hạn mức thì vẫn trả về từ khoá gốc kèm `warning` —
    người dùng tạo job được như thường, chỉ là không có bản địa hoá.
    """
    from app.modules.geo import service as geo

    countries = countries_of_locations(payload.locations)
    if payload.countries:
        known = {c["code"] for c in countries}
        for code in payload.countries:
            c = geo.load().country(code.upper())
            if c and c["code"] not in known:
                countries.append(c)

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
    _: User = Depends(current_user),
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
    )
    db.commit()
    return ApiResponse.ok({"saved": True})
