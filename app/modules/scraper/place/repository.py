from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.core.pagination import PageParams
from app.modules.scraper.place.entity import JobPlace, Place, PlaceKeyword

# Cột cho phép sắp xếp. Danh sách trắng — không bao giờ ghép chuỗi cột từ query
# string của người dùng vào SQL.
SORTABLE = {
    "liveness": Place.liveness_score,
    "name": Place.name,
    "rating": Place.rating,
    "review_count": Place.review_count,
    "scraped_at": Place.scraped_at,
}


@dataclass
class PlaceFilter:
    q: str | None = None
    job_id: int | None = None
    keyword: str | None = None
    liveness: list[str] = field(default_factory=list)
    business_status: str | None = None
    has_phone: bool | None = None
    has_website: bool | None = None
    min_rating: float | None = None
    sort: str = "-liveness"


class PlaceRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ----- truy vấn -----
    def _apply(self, stmt: Select, f: PlaceFilter) -> Select:
        if f.job_id is not None:
            stmt = stmt.join(JobPlace, JobPlace.place_id == Place.id).where(JobPlace.job_id == f.job_id)
        if f.keyword:
            stmt = stmt.join(PlaceKeyword, PlaceKeyword.place_id == Place.id).where(
                PlaceKeyword.keyword == f.keyword
            )
        if f.q:
            from app.modules.scraper.place.service import fold_text

            needle = f"%{fold_text(f.q)}%"
            stmt = stmt.where(or_(Place.search_text.ilike(needle), Place.name.ilike(f"%{f.q}%")))
        if f.liveness:
            stmt = stmt.where(Place.liveness_label.in_(f.liveness))
        if f.business_status:
            stmt = stmt.where(Place.business_status == f.business_status)
        if f.has_phone is True:
            stmt = stmt.where(Place.phone_e164.isnot(None))
        elif f.has_phone is False:
            stmt = stmt.where(Place.phone_e164.is_(None))
        if f.has_website is True:
            stmt = stmt.where(Place.website.isnot(None))
        elif f.has_website is False:
            stmt = stmt.where(Place.website.is_(None))
        if f.min_rating is not None:
            stmt = stmt.where(Place.rating >= f.min_rating)
        return stmt

    def _order(self, stmt: Select, sort: str) -> Select:
        desc = sort.startswith("-")
        column = SORTABLE.get(sort.lstrip("-"), Place.liveness_score)
        return stmt.order_by(column.desc().nullslast() if desc else column.asc().nullslast(), Place.id)

    def count(self, f: PlaceFilter) -> int:
        stmt = self._apply(select(func.count(func.distinct(Place.id))), f)
        return int(self.db.execute(stmt).scalar_one())

    def page(self, f: PlaceFilter, params: PageParams) -> list[Place]:
        stmt = self._order(self._apply(select(Place).distinct(), f), f.sort)
        return list(self.db.execute(stmt.offset(params.offset).limit(params.size)).scalars().all())

    def stream(self, f: PlaceFilter, chunk: int = 1000) -> Iterator[Place]:
        """Duyệt theo lô cho việc xuất file — không nạp cả trăm nghìn dòng vào RAM."""
        stmt = self._order(self._apply(select(Place).distinct(), f), f.sort)
        result = self.db.execute(stmt.execution_options(stream_results=True, yield_per=chunk))
        yield from result.scalars()

    def by_id(self, place_id: int) -> Place | None:
        return self.db.execute(select(Place).where(Place.id == place_id)).scalar_one_or_none()

    def keywords_for(self, ids: Iterable[int]) -> dict[int, list[str]]:
        ids = list(ids)
        if not ids:
            return {}
        out: dict[int, list[str]] = {i: [] for i in ids}
        rows = self.db.execute(
            select(PlaceKeyword.place_id, PlaceKeyword.keyword)
            .where(PlaceKeyword.place_id.in_(ids))
            .order_by(PlaceKeyword.keyword)
        ).all()
        for place_id, keyword in rows:
            out.setdefault(place_id, []).append(keyword)
        return out
