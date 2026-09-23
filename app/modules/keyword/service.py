"""Sinh từ khoá tìm kiếm theo từng quốc gia, có bộ nhớ đệm.

Vì sao cần: từ khoá tiếng Việt gần như vô dụng ở nước ngoài. Đo thực tế với
"xuất nhập khẩu trái cây Bangkok" (gl=vn) chỉ ra 2 kết quả, và cả hai đều nằm ở
TP.HCM — sai địa bàn hoàn toàn. Cùng ý đó viết "fruit wholesaler" với gl=th ra
đầy kết quả Thái Lan.

Luật vận hành: AI chỉ chạy lúc NGƯỜI DÙNG BẤM, ở màn tạo job, và kết quả luôn
hiện ra cho họ sửa trước khi chạy. Worker cào không bao giờ gọi AI.
"""
from __future__ import annotations

import hashlib

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import ai
from app.core.config import get_settings
from app.modules.geo import service as geo
from app.modules.keyword.entity import KeywordTranslation

# Việt Nam dùng thẳng từ khoá gốc — không dịch gì cả.
HOME_COUNTRY = "VN"


def normalize(keywords: list[str]) -> list[str]:
    out: list[str] = []
    for k in keywords:
        k = " ".join((k or "").split())
        if k and k.lower() not in {x.lower() for x in out}:
            out.append(k)
    return out


def source_hash(keywords: list[str]) -> str:
    """Khoá đệm theo BỘ từ khoá, không theo từng từ.

    AI nhận cả bộ rồi trả về các cụm bao quát chung, nên đệm theo từng từ riêng lẻ
    sẽ không ghép lại đúng được.
    """
    joined = "|".join(sorted(k.lower() for k in normalize(keywords)))
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:40]


def countries_of_locations(locations: list[str]) -> list[dict]:
    """Các quốc gia xuất hiện trong danh sách địa điểm, giữ thứ tự gặp đầu tiên."""
    seen: dict[str, dict] = {}
    for loc in locations:
        country = geo.resolve_country(loc)
        if country and country["code"] not in seen:
            seen[country["code"]] = country
    return list(seen.values())


class KeywordService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _cached(self, digest: str, codes: list[str]) -> dict[str, KeywordTranslation]:
        if not codes:
            return {}
        rows = self.db.execute(
            select(KeywordTranslation).where(
                KeywordTranslation.source_hash == digest,
                KeywordTranslation.country_code.in_(codes),
            )
        ).scalars().all()
        return {r.country_code: r for r in rows}

    def plan(self, keywords: list[str], countries: list[dict]) -> dict:
        """Xem trước một lượt dịch sẽ tốn bao nhiêu, KHÔNG gọi AI.

        Có endpoint riêng vì trần `ai_max_countries` phải chặn TRƯỚC khi người
        dùng bấm nút, không phải báo lỗi sau khi đã chờ. Và trần đó tính trên số
        quốc gia THẬT SỰ CẦN GỌI AI: 60 nước mà 40 nước đã có sẵn trong bộ nhớ
        đệm thì chỉ còn 20 nước cần dịch, chặn ở con số 60 là chặn oan.
        """
        keywords = normalize(keywords)
        settings = get_settings()
        limit = settings.ai_max_countries
        if not keywords:
            return {
                "total": len(countries), "home": [], "cached": [], "need": [],
                "limit": limit, "over_limit": False, "ai_available": ai.is_enabled(),
            }

        home = [c["code"] for c in countries if c["code"] == HOME_COUNTRY]
        foreign = [c for c in countries if c["code"] != HOME_COUNTRY]
        da_co = self._cached(source_hash(keywords), [c["code"] for c in foreign])
        cached = [c["code"] for c in foreign if c["code"] in da_co]
        need = [c["code"] for c in foreign if c["code"] not in da_co]
        return {
            "total": len(countries),
            "home": home,
            "cached": cached,
            "need": need,
            "limit": limit,
            "over_limit": len(need) > limit,
            "ai_available": ai.is_enabled(),
        }

    def localize(self, keywords: list[str], countries: list[dict]) -> tuple[list[dict], str | None]:
        """Trả (danh sách gợi ý theo quốc gia, cảnh báo nếu có).

        Không bao giờ ném lỗi ra ngoài vì AI: hỏng thì trả về từ khoá gốc kèm câu
        cảnh báo, người dùng vẫn tạo job được như thường.
        """
        keywords = normalize(keywords)
        if not keywords:
            return [], None

        settings = get_settings()
        digest = source_hash(keywords)
        out: list[dict] = []
        need: list[dict] = []

        for c in countries:
            if c["code"] == HOME_COUNTRY:
                out.append({**self._row(c, keywords, "vi"), "source": "original"})
                continue
            need.append(c)

        cached = self._cached(digest, [c["code"] for c in need])
        missing: list[dict] = []
        for c in need:
            row = cached.get(c["code"])
            if row:
                out.append(
                    {
                        **self._row(c, list(row.keywords or []), row.language),
                        "source": "user" if row.edited_by_user else "cache",
                    }
                )
            else:
                missing.append(c)

        warning: str | None = None
        if missing:
            if not ai.is_enabled():
                warning = "Chưa bật AI (thiếu BCD_AI_API_KEY) nên dùng tạm từ khoá gốc."
                for c in missing:
                    out.append({**self._row(c, keywords, "vi"), "source": "fallback"})
            elif len(missing) > settings.ai_max_countries:
                warning = (
                    f"Có {len(missing)} quốc gia cần dịch, vượt trần {settings.ai_max_countries} "
                    "cho một lần. Hãy chọn ít quốc gia hơn."
                )
                for c in missing:
                    out.append({**self._row(c, keywords, "vi"), "source": "fallback"})
            else:
                out.extend(self._call_ai(digest, keywords, missing))
                if any(o["source"] == "fallback" for o in out):
                    warning = "Một phần quốc gia chưa dịch được, đang dùng từ khoá gốc cho những nơi đó."

        order = {c["code"]: i for i, c in enumerate(countries)}
        out.sort(key=lambda x: order.get(x["country_code"], 999))
        return out, warning

    def _call_ai(self, digest: str, keywords: list[str], missing: list[dict]) -> list[dict]:
        try:
            suggestions = ai.localize_keywords(keywords, missing)
        except ai.AiUnavailable as exc:
            logger.warning("Không dịch được từ khoá: {}", exc)
            return [{**self._row(c, keywords, "vi"), "source": "fallback"} for c in missing]

        by_code = {s.country_code: s for s in suggestions}
        model = get_settings().ai_model
        out: list[dict] = []
        for c in missing:
            s = by_code.get(c["code"])
            if not s or not s.keywords:
                # AI bỏ sót quốc gia này -> giữ từ khoá gốc thay vì bịa.
                out.append({**self._row(c, keywords, "vi"), "source": "fallback"})
                continue
            self.save(digest, keywords, c["code"], s.language, s.keywords, model, edited=False)
            out.append({**self._row(c, s.keywords, s.language), "source": "ai"})
        self.db.commit()
        return out

    def save(
        self,
        digest: str,
        keywords: list[str],
        country_code: str,
        language: str,
        translated: list[str],
        model: str | None,
        edited: bool,
    ) -> None:
        row = self.db.execute(
            select(KeywordTranslation).where(
                KeywordTranslation.source_hash == digest,
                KeywordTranslation.country_code == country_code,
            )
        ).scalar_one_or_none()
        if row is None:
            row = KeywordTranslation(source_hash=digest, country_code=country_code)
            self.db.add(row)
        row.source_keywords = keywords
        row.language = language
        row.keywords = translated
        row.model = model
        row.edited_by_user = edited
        self.db.flush()

    @staticmethod
    def _row(country: dict, keywords: list[str], language: str) -> dict:
        return {
            "country_code": country["code"],
            "country_name": country["name"],
            "language": language,
            "keywords": list(keywords),
        }
