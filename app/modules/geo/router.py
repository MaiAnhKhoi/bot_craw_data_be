from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.core.deps import current_user
from app.core.response import ApiResponse
from app.modules.geo import service
from app.modules.identity.entity import User

router = APIRouter(prefix="/geo", tags=["geo"])


class GeoItem(BaseModel):
    code: str
    name: str


class CountryItem(GeoItem):
    name_en: str
    continent: str
    levels: int = Field(description="1 = chỉ quốc gia, 2 = có cấp tỉnh, 3 = có cả phường/xã")


class ExpandRequest(BaseModel):
    continent: str | None = Field(None, description="Mã châu lục, hoặc ALL, hoặc bỏ trống")
    country: str | None = Field(None, description="Mã quốc gia, hoặc ALL")
    province: str | None = Field(None, description="Mã tỉnh, ALL, hoặc bỏ trống để dừng ở cấp quốc gia")
    ward: str | None = Field(None, description="Mã phường/xã, ALL, hoặc bỏ trống để dừng ở cấp tỉnh")


class ExpandResponse(BaseModel):
    locations: list[str]
    total: int = Field(description="Tổng số địa điểm thật; có thể lớn hơn số dòng trả về")
    truncated: bool


@router.get("/continents", response_model=ApiResponse[list[GeoItem]], summary="Danh sách châu lục")
def list_continents(_: User = Depends(current_user)) -> ApiResponse[list[GeoItem]]:
    return ApiResponse.ok([GeoItem(**c) for c in service.continents()])


@router.get("/countries", response_model=ApiResponse[list[CountryItem]], summary="Danh sách quốc gia")
def list_countries(
    continent: str | None = Query(None),
    q: str | None = Query(None, description="Tìm theo tên, không dấu cũng khớp"),
    _: User = Depends(current_user),
) -> ApiResponse[list[CountryItem]]:
    return ApiResponse.ok([CountryItem(**c) for c in service.countries(continent, q)])


@router.get("/provinces", response_model=ApiResponse[list[GeoItem]], summary="Tỉnh/thành của một quốc gia")
def list_provinces(
    country: str = Query(..., description="Mã quốc gia ISO alpha-2"),
    q: str | None = Query(None),
    _: User = Depends(current_user),
) -> ApiResponse[list[GeoItem]]:
    return ApiResponse.ok([GeoItem(**p) for p in service.provinces(country, q)])


@router.get("/wards", response_model=ApiResponse[list[GeoItem]], summary="Phường/xã của một tỉnh")
def list_wards(
    province: str = Query(..., description="Mã tỉnh, ví dụ VN-79"),
    q: str | None = Query(None),
    _: User = Depends(current_user),
) -> ApiResponse[list[GeoItem]]:
    return ApiResponse.ok([GeoItem(**w) for w in service.wards(province, q)])


@router.post(
    "/expand",
    response_model=ApiResponse[ExpandResponse],
    summary="Biến lựa chọn theo cấp thành danh sách địa điểm cụ thể",
)
def expand(payload: ExpandRequest, _: User = Depends(current_user)) -> ApiResponse[ExpandResponse]:
    """Tính ở server chứ không ở trình duyệt.

    Chọn "tất cả" ở cấp phường/xã có thể ra hơn 3.000 dòng; để trình duyệt tự nhân
    tổ hợp thì phải tải toàn bộ cây địa giới về máy khách chỉ để làm một phép ghép chuỗi.
    """
    locations, total = service.expand(payload.continent, payload.country, payload.province, payload.ward)
    return ApiResponse.ok(
        ExpandResponse(locations=locations, total=total, truncated=total > len(locations))
    )
