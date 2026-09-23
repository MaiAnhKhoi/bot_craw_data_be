"""Chuẩn hoá chuỗi để tìm kiếm không phụ thuộc dấu tiếng Việt."""
from __future__ import annotations

import re

from unidecode import unidecode


def fold_text(value: str | None) -> str:
    """Bỏ dấu, viết thường, gom khoảng trắng — để 'quan 1' tìm ra 'Quận 1'."""
    if not value:
        return ""
    return re.sub(r"\s+", " ", unidecode(value).lower()).strip()
