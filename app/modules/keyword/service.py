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
from app.core.exceptions import AppError, NotFoundError
from app.modules.geo import service as geo
from app.modules.keyword.entity import KeywordSet, KeywordTranslation

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


def loc_cum_hong(values: list[str] | None) -> list[str]:
    """Bỏ những cụm trộn chữ Latinh vào giữa một hệ chữ khác.

    Áp dụng ở ĐƯỜNG ĐỌC chứ không chỉ lúc nhận từ AI. Lưới chặn ở `app.core.ai`
    chỉ lọc được thứ sinh ra TỪ BÂY GIỜ; những cụm hỏng đã nằm sẵn trong bộ nhớ
    đệm thì lần sau vẫn được trả về nguyên vẹn — và vì đệm không bao giờ hết hạn,
    chúng sẽ ở đó vĩnh viễn.

    Đo thật: bộ từ khoá tiếng Khmer cho Campuchia lưu `ឧmartinមាក់ផ្លែឈើ` (có
    nguyên chữ "martin" nằm giữa) và `អ vendor`. Gõ vào Google Maps ra con số
    không, không báo lỗi gì — job chạy xong, bảng trống.
    """
    return [v for v in (values or []) if not ai._lac_chu_viet(v)]


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
        # `du_dung` chứ không phải `có mặt`: một dòng đệm có từ khoá nhưng RỖNG
        # danh mục ngành nghề là dòng sinh ra trước khi có bộ lọc ngành. Tính nó
        # là "đã có" thì job chạy mà không lọc gì — đúng cái lỗi mà người dùng
        # vừa báo (tiệm bánh kem lẫn vào công ty trái cây), và lần này nó im lặng.
        du_dung = {ma for ma, row in da_co.items() if loc_cum_hong(row.categories)}
        cached = [c["code"] for c in foreign if c["code"] in du_dung]
        need = [c["code"] for c in foreign if c["code"] not in du_dung]
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
            tu_khoa = loc_cum_hong(list(row.keywords or [])) if row else []
            danh_muc = loc_cum_hong(list(row.categories or [])) if row else []
            # Lọc xong mà RỖNG thì coi như chưa có — gọi AI sinh lại bằng
            # prompt mới thay vì đưa người dùng một danh sách trống rỗng.
            if row and tu_khoa and danh_muc:
                out.append(
                    {
                        **self._row(c, tu_khoa, row.language, danh_muc),
                        "source": "user" if row.edited_by_user else "cache",
                    }
                )
            else:
                # Có từ khoá nhưng CHƯA có danh mục ngành nghề -> vẫn phải gọi AI.
                # Đây là mọi bản dịch sinh ra trước khi có bộ lọc ngành. Bỏ qua
                # chúng thì bộ lọc tắt lặng lẽ cho đúng những quốc gia đã quét
                # nhiều nhất, tức là chỗ có nhiều rác nhất.
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
        da_co = self._cached(digest, [c["code"] for c in missing])
        model = get_settings().ai_model
        out: list[dict] = []
        for c in missing:
            s = by_code.get(c["code"])
            if not s or not s.keywords:
                # AI bỏ sót quốc gia này -> giữ từ khoá gốc thay vì bịa.
                out.append({**self._row(c, keywords, "vi"), "source": "fallback"})
                continue
            # Dòng người dùng đã sửa tay thì CHỈ bổ sung danh mục, giữ nguyên từ
            # khoá và cờ `edited_by_user`. Lượt gọi này thường là để vá danh mục
            # cho bản dịch cũ; ghi đè luôn từ khoá là xoá mất công sửa của họ mà
            # không hỏi, và họ chỉ phát hiện khi job chạy ra kết quả lạ.
            cu = da_co.get(c["code"])
            giu_tu_khoa = bool(cu and cu.edited_by_user)
            self.save(
                digest,
                keywords,
                c["code"],
                cu.language if giu_tu_khoa else s.language,
                list(cu.keywords or []) if giu_tu_khoa else s.keywords,
                model,
                edited=giu_tu_khoa,
                categories=s.categories,
            )
            out.append(
                {
                    **self._row(
                        c,
                        list(cu.keywords or []) if giu_tu_khoa else s.keywords,
                        cu.language if giu_tu_khoa else s.language,
                        s.categories,
                    ),
                    "source": "user" if giu_tu_khoa else "ai",
                }
            )
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
        categories: list[str] | None = None,
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
        # `None` = lần ghi này không nói gì về danh mục -> giữ nguyên cái đang có.
        # Phân biệt với `[]` (người dùng cố ý xoá sạch) là cần thiết: nếu không,
        # mọi lần lưu từ màn sửa từ khoá cũ sẽ âm thầm xoá mất danh mục, và bộ
        # lọc ngành nghề tắt đi mà không ai thấy.
        if categories is not None:
            row.categories = list(categories)
        self.db.flush()

    @staticmethod
    def _row(
        country: dict, keywords: list[str], language: str, categories: list[str] | None = None
    ) -> dict:
        return {
            "country_code": country["code"],
            "country_name": country["name"],
            "language": language,
            "keywords": list(keywords),
            "categories": list(categories or []),
        }

    def categories_of(self, keywords: list[str], codes: list[str]) -> dict[str, list[str]]:
        """Danh mục ngành nghề đã lưu, theo từng quốc gia.

        Tra ở tầng service chứ không bắt giao diện gửi lên: người dùng có thể tạo
        job từ một bộ từ khoá đã lưu mà không mở lại màn dịch lần nào, và khi đó
        giao diện không có gì trong tay để gửi. Thiếu danh mục thì bộ lọc ngành
        nghề tắt hẳn — quét ra bao nhiêu ghi bấy nhiêu — nên chỗ này im lặng trả
        về rỗng là mất luôn tác dụng lọc mà không ai biết.
        """
        codes = [c.upper() for c in codes if c]
        if not keywords or not codes:
            return {}
        da_co = self._cached(source_hash(normalize(keywords)), codes)
        ra = {ma: loc_cum_hong(list(row.categories or [])) for ma, row in da_co.items()}
        return {ma: ds for ma, ds in ra.items() if ds}


def ten_chuan(name: str) -> str:
    """Tên đã gọn khoảng trắng + viết thường, dùng để so trùng.

    Tách riêng khỏi tên hiển thị: người dùng gõ "Trái Cây Xuất Khẩu" hay
    "trái cây xuất khẩu" đều là một bộ, nhưng tên hiện ra phải giữ đúng cách họ gõ.
    """
    return " ".join((name or "").split()).lower()


class KeywordSetService:
    """Bộ từ khoá có tên — chỉ để GỌI LẠI CHÍNH XÁC, không lưu bản dịch.

    Bản dịch vẫn nằm ở `keyword_translations`; hai bảng nối nhau qua `source_hash`,
    nên chọn lại một bộ đã lưu là trúng đệm 100% cho mọi nước từng dịch.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def _da_dich(self, hashes: list[str]) -> dict[str, list[str]]:
        """Mỗi `source_hash` đã có bản dịch cho những nước nào.

        Một câu truy vấn cho TẤT CẢ các bộ, không phải mỗi bộ một câu — danh sách
        này hiện ngay trên ô chọn nên nó chạy mỗi lần mở form tạo job.
        """
        if not hashes:
            return {}
        rows = self.db.execute(
            select(KeywordTranslation.source_hash, KeywordTranslation.country_code)
            .where(KeywordTranslation.source_hash.in_(hashes))
            .order_by(KeywordTranslation.country_code)
        ).all()
        out: dict[str, list[str]] = {}
        for h, ma in rows:
            out.setdefault(h, []).append(ma)
        return out

    def list(self) -> list[dict]:
        bo = list(
            self.db.execute(select(KeywordSet).order_by(KeywordSet.updated_at.desc()))
            .scalars()
            .all()
        )
        da_dich = self._da_dich([b.source_hash for b in bo])
        return [
            {
                "id": b.id,
                "name": b.name,
                "keywords": list(b.keywords or []),
                "translated_countries": da_dich.get(b.source_hash, []),
                "created_at": b.created_at,
                "updated_at": b.updated_at,
            }
            for b in bo
        ]

    def save(self, name: str, keywords: list[str]) -> dict:
        """Ghi đè theo TÊN (không phân biệt hoa thường), không tạo bản trùng tên."""
        kw = normalize(keywords)
        if not kw:
            raise AppError(
                "Bộ từ khoá phải có ít nhất một từ", code="VALIDATION_ERROR", status_code=422
            )
        khoa = ten_chuan(name)
        if not khoa:
            raise AppError("Thiếu tên bộ từ khoá", code="VALIDATION_ERROR", status_code=422)

        bo = self.db.execute(
            select(KeywordSet).where(KeywordSet.name_key == khoa)
        ).scalar_one_or_none()
        if bo is None:
            bo = KeywordSet(name_key=khoa)
            self.db.add(bo)
        bo.name = " ".join(name.split())
        bo.keywords = kw
        bo.source_hash = source_hash(kw)
        self.db.commit()
        self.db.refresh(bo)
        da_dich = self._da_dich([bo.source_hash])
        return {
            "id": bo.id,
            "name": bo.name,
            "keywords": list(bo.keywords or []),
            "translated_countries": da_dich.get(bo.source_hash, []),
            "created_at": bo.created_at,
            "updated_at": bo.updated_at,
        }

    def delete(self, set_id: int) -> None:
        bo = self.db.get(KeywordSet, set_id)
        if bo is None:
            raise NotFoundError(f"Không tìm thấy bộ từ khoá {set_id}")
        # CHỈ xoá bộ, KHÔNG đụng `keyword_translations`: bản dịch là thứ đã trả
        # tiền để có. Lưu lại bộ cùng tên sau này là dùng lại được ngay.
        self.db.delete(bo)
        self.db.commit()
