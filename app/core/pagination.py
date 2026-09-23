"""Phân trang dùng chung. Phân trang luôn ở SERVER — không bao giờ tải hết rồi lọc."""
from __future__ import annotations

import math
from collections.abc import Sequence

from fastapi import Query
from pydantic import BaseModel

MAX_SIZE = 200


class PageParams(BaseModel):
    page: int = 1
    size: int = 50

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size


def page_params(
    page: int = Query(1, ge=1, description="Trang, bắt đầu từ 1"),
    size: int = Query(50, ge=1, le=MAX_SIZE, description=f"Số dòng mỗi trang, tối đa {MAX_SIZE}"),
) -> PageParams:
    return PageParams(page=page, size=size)


class Page[T](BaseModel):
    items: list[T]
    page: int
    size: int
    total: int
    pages: int

    @classmethod
    def build(cls, items: Sequence[T], params: PageParams, total: int) -> Page[T]:
        return cls(
            items=list(items),
            page=params.page,
            size=params.size,
            total=total,
            pages=max(1, math.ceil(total / params.size)) if total else 0,
        )
