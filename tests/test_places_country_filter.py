"""Lọc địa điểm theo quốc gia: chuẩn hoá tham số, câu SQL sinh ra, thứ tự khai route.

Chạy THUẦN: không DB, không mạng. Câu lệnh chỉ được DỊCH sang SQL của Postgres để
đọc chứ không hề gửi đi đâu, nên bộ test này chạy được trên máy chưa dựng hạ tầng.
"""
from __future__ import annotations

import inspect

import pytest
from sqlalchemy import Select, select
from sqlalchemy.dialects import postgresql

from app.modules.scraper.place.entity import Place
from app.modules.scraper.place.repository import (
    COUNTRY_COUNTS,
    PlaceFilter,
    PlaceRepository,
    normalize_country,
)
from app.modules.scraper.place.response import CountryCountResponse
from app.modules.scraper.place.router import export_places, list_places, place_filter, router
from app.modules.scraper.place.service import sort_countries


def sql_of(stmt: Select) -> str:
    """SQL đúng như gửi xuống Postgres, tham số nhúng sẵn để so chuỗi cho gọn."""
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def where_of(f: PlaceFilter) -> str:
    """SQL của bộ lọc, chỉ chọn cột id để chuỗi còn lại chính là phần điều kiện.

    Chọn `select(Place)` thì tên `country_code` xuất hiện sẵn ở danh sách cột và
    phép kiểm tra "không lọc thì không có country_code" sẽ luôn đúng một cách giả.
    """
    # `_apply` không chạm tới session nên không cần DB để dựng câu truy vấn.
    return sql_of(PlaceRepository(None)._apply(select(Place.id), f))


# ---------- chuẩn hoá mã quốc gia ----------
@pytest.mark.parametrize("raw", ["TH", "th", "Th", " th ", "\tth\n"])
def test_moi_cach_go_deu_ra_cung_mot_ma(raw):
    assert normalize_country(raw) == "TH"


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_bo_trong_thi_khong_loc(raw):
    assert normalize_country(raw) is None


def test_bo_loc_tu_chuan_hoa_khong_cho_router_nho_ho():
    """Router, API xuất file và worker đều dựng PlaceFilter; chuẩn hoá phải nằm
    trong chính nó thì mới không có đường nào lọt."""
    assert PlaceFilter(country="th").country == "TH"
    assert PlaceFilter(country=" vn ").country == "VN"
    assert PlaceFilter(country="").country is None
    assert PlaceFilter().country is None


# ---------- điều kiện sinh ra trong SQL ----------
def test_loc_theo_ma_da_viet_hoa():
    assert "places.country_code = 'TH'" in where_of(PlaceFilter(country="th"))


def test_khong_truyen_thi_khong_them_dieu_kien_nao():
    assert "country_code" not in where_of(PlaceFilter())


def test_dung_chung_duoc_voi_moi_bo_loc_san_co():
    """Quốc gia phải là MỘT điều kiện nữa trong cùng câu truy vấn, không phải một
    nhánh riêng — lọc kèm từ khoá/SĐT/job mà mất điều kiện nào cũng là ra sai số."""
    sql = where_of(
        PlaceFilter(
            q="tran hung dao", job_id=7, keyword="xoai", country="th", liveness=["ACTIVE"],
            business_status="OPERATIONAL", has_phone=True, has_website=False, min_rating=4.0,
        )
    )
    for dieu_kien in (
        "job_places.job_id = 7",
        "place_keywords.keyword = 'xoai'",
        "places.search_text ILIKE",
        "places.country_code = 'TH'",
        "places.liveness_label IN ('ACTIVE')",
        "places.business_status = 'OPERATIONAL'",
        "places.phone_e164 IS NOT NULL",
        "places.website IS NULL",
        "places.rating >= 4.0",
    ):
        assert dieu_kien in sql, dieu_kien


def test_sap_xep_va_phan_trang_van_giu_dieu_kien_quoc_gia():
    f = PlaceFilter(country="th", sort="-rating")
    repo = PlaceRepository(None)
    stmt = repo._order(repo._apply(select(Place.id), f), f.sort)
    assert "places.country_code = 'TH'" in sql_of(stmt)
    assert "ORDER BY places.rating DESC" in sql_of(stmt)


def test_xuat_file_dung_chung_bo_loc_voi_danh_sach():
    """Lọc trên màn hình mà file tải về vẫn đủ mọi nước là kiểu sai không ai kiểm
    lại: sale cứ thế gọi nhầm cả danh sách nước khác."""
    assert "country" in inspect.signature(place_filter).parameters
    for endpoint in (list_places, export_places):
        assert inspect.signature(endpoint).parameters["f"].default.dependency is place_filter


# ---------- endpoint danh sách quốc gia ----------
def test_countries_phai_khai_truoc_place_id():
    """Khai sau thì FastAPI nhét "countries" vào place_id và trả 422."""
    paths = [r.path for r in router.routes]
    assert paths.index("/places/countries") < paths.index("/places/{place_id}")


def test_dem_bang_group_by_chu_khong_nap_ca_bang():
    sql = sql_of(COUNTRY_COUNTS)
    assert "count(places.id)" in sql
    assert "GROUP BY places.country_code" in sql
    assert "places.country_code IS NOT NULL" in sql


def test_ten_tieng_viet_di_kem_ma():
    item = CountryCountResponse.of("TH", 6)
    assert (item.code, item.name, item.count) == ("TH", "Thái Lan", 6)
    # Mã lạ vẫn phải có tên để hiện, thà "ZZ" còn hơn một ô trống.
    assert CountryCountResponse.of("ZZ", 1).name == "ZZ"


def test_nhieu_du_lieu_len_dau_roi_moi_den_ten():
    items = [
        CountryCountResponse.of("VN", 3),
        CountryCountResponse.of("TH", 6),
        CountryCountResponse.of("JP", 3),
    ]
    assert [c.code for c in sort_countries(items)] == ["TH", "JP", "VN"]


def test_ten_co_dau_khong_bi_day_xuong_cuoi():
    """Chữ Ấ nằm sau chữ Z trong bảng mã, xếp thô thì Ấn Độ rơi xuống tận đáy."""
    items = [CountryCountResponse.of("ZW", 1), CountryCountResponse.of("IN", 1)]
    assert [c.code for c in sort_countries(items)] == ["IN", "ZW"]
