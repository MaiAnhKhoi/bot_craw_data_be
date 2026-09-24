from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.core.pagination import PageParams
from app.modules.scraper.place.entity import JobPlace, Place, PlaceKeyword
from app.modules.scraper.place.relevance import NGHI_RAC

# Cột cho phép sắp xếp. Danh sách trắng — không bao giờ ghép chuỗi cột từ query
# string của người dùng vào SQL.
SORTABLE = {
    "liveness": Place.liveness_score,
    "name": Place.name,
    "rating": Place.rating,
    "review_count": Place.review_count,
    "scraped_at": Place.scraped_at,
}

# Số địa điểm theo từng quốc gia. Bỏ dòng chưa biết quốc gia: "chưa rõ" không phải
# một lựa chọn lọc, đưa vào ô chọn chỉ khiến người dùng bấm vào rồi không hiểu.
# GROUP BY chạy dưới Postgres (có ix_places_country_code) — bảng cỡ hàng trăm nghìn
# dòng mà nạp ra Python đếm thì endpoint này thành chỗ nghẽn.
# `count()` chứ KHÔNG phải `count(<cột>)` ở mọi câu đếm dưới đây.
#
# Nghe như chuyện cho đẹp, nhưng đo ở 100.000 dòng thì chênh 6,5 lần:
#
#     SELECT country_code, count(id)  GROUP BY country_code   122,9 ms  Seq Scan
#     SELECT country_code, count(*)   GROUP BY country_code    18,9 ms  Index Only Scan
#
# Lý do: `count(id)` bắt Postgres phải LẤY GIÁ TRỊ cột `id`, mà `id` không nằm
# trong `ix_places_country_code`. Không đọc được từ chỉ mục thì phải mở heap;
# mà đã mở hết heap rồi thì planner thấy quét toàn bảng còn rẻ hơn, nên bỏ luôn
# chỉ mục. `count(*)` chỉ đếm dòng, không cần cột nào, nên Index Only Scan với
# `Heap Fetches: 0`.
#
# Hai cách cho kết quả Y HỆT vì `id` là khoá chính NOT NULL.
#
# MỘT ĐIỀU KIỆN dễ quên: lợi ích này cần VISIBILITY MAP, tức bảng phải được
# VACUUM. Đo lại ngay sau khi nạp 100.000 dòng mà autovacuum chưa chạy thì
# `count(*)` cũng Seq Scan 64 ms, y hệt `count(<cột>)`. Chỉ sau khi
# `relallvisible` lên đủ thì planner mới đổi sang Index Only Scan. Nghĩa là
# ngay sau một lượt quét lớn, trang Tổng quan vẫn chậm cho tới lúc autovacuum
# bắt kịp — không phải lỗi, chỉ là đừng đo hiệu năng ở đúng thời điểm đó.
COUNTRY_COUNTS = (
    select(Place.country_code, func.count())
    .where(Place.country_code.isnot(None))
    .group_by(Place.country_code)
)


# Các LƯỢT TÌM đã sinh ra dữ liệu, kèm số địa điểm.
#
# `PlaceKeyword.keyword` lưu CẢ CHUỖI TRUY VẤN ("fruit wholesaler Phuket, Thailand"),
# không phải riêng từ khoá — đó là thứ duy nhất trả lời được "địa điểm này ra từ
# lượt tìm nào". CỐ Ý KHÔNG giới hạn số dòng: ô lọc trước đây mượn tạm
# `overview.top_keywords` vốn có LIMIT 10, nên quét từ tỉnh thứ 11 trở đi là những
# lượt đó biến mất khỏi ô lọc mà không có dấu hiệu gì — người dùng tưởng không lọc
# được, hoặc tệ hơn là tưởng không có dữ liệu.
QUERY_COUNTS = (
    select(PlaceKeyword.keyword, func.count())
    .group_by(PlaceKeyword.keyword)
    .order_by(func.count().desc(), PlaceKeyword.keyword)
)


# Số id tối đa nhét vào MỘT câu `IN (...)` khi tra từ khoá.
#
# Đây là TRẦN CỨNG của giao thức PostgreSQL chứ không phải chuyện nhanh chậm: số
# tham số của một câu lệnh được đếm bằng int16, nên câu thứ 65.536 trở đi chết bằng
# `number of parameters must be between 0 and 65535` — lỗi ở tầng giao thức, không
# có cách nào bắt rồi chạy tiếp. Mà `export_max_rows` mặc định là 100.000 và chỉ
# chặn khi VƯỢT, nên một lần xuất đúng 100.000 dòng là hợp lệ về nghiệp vụ nhưng
# 500 về kỹ thuật.
#
# 5.000 chứ không phải sát trần 65.535: chừa chỗ cho lần sau có ai thêm điều kiện
# (và tham số) vào cùng câu lệnh này, đồng thời câu `IN` ngắn thì Postgres lập kế
# hoạch nhanh hơn. Chia lô nhiều hơn vài lượt không đáng kể so với việc ghi file.
KEYWORD_BATCH = 5_000


def normalize_country(value: str | None) -> str | None:
    """Chuẩn hoá mã quốc gia trước khi đem đi so sánh.

    Trong DB `country_code` luôn viết hoa (xem `writer.resolve_place_country`), còn
    giao diện gửi lên "th", "Th", hay chuỗi rỗng khi người dùng bỏ chọn. So thẳng
    thì `country=th` trả về bảng RỖNG mà không có lỗi nào báo — trông y hệt như
    "nước này chưa quét được gì", nên sẽ không ai đi tìm nguyên nhân.
    """
    code = (value or "").strip().upper()
    return code or None


@dataclass
class PlaceFilter:
    q: str | None = None
    job_id: int | None = None
    keyword: str | None = None
    country: str | None = None
    contact_status: str | None = None
    relevance: str | None = None
    liveness: list[str] = field(default_factory=list)
    business_status: str | None = None
    has_phone: bool | None = None
    has_website: bool | None = None
    min_rating: float | None = None
    sort: str = "-liveness"

    def __post_init__(self) -> None:
        # Chuẩn hoá ở ngay chỗ dựng bộ lọc chứ không ở router: cả API danh sách lẫn
        # API xuất file đều dựng PlaceFilter, và nơi nào quên gọi thì hỏng âm thầm.
        self.country = normalize_country(self.country)


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

            # CHỈ so `search_text`, KHÔNG thêm `OR name ILIKE ...`.
            #
            # Vế `name` vừa thừa vừa đắt. Thừa vì `build_search_text` đã gộp sẵn
            # tên vào `search_text` ở dạng bỏ dấu, mà so bản bỏ dấu thì BAO TRÙM
            # bản có dấu — đo trên dữ liệu thật: 0 dòng khớp qua tên mà không
            # khớp qua `search_text`.
            #
            # Đắt vì `name` KHÔNG có chỉ mục trigram. Một vế OR không đánh chỉ
            # mục được là cả câu phải quét toàn bảng, nên
            # `ix_places_search_text_trgm` (448 KB, dựng riêng cho ô tìm kiếm
            # này) chưa được dùng lần nào — `idx_scan = 0`. Bỏ vế kia đi thì nó
            # mới có cơ hội chạy.
            stmt = stmt.where(Place.search_text.ilike(f"%{fold_text(f.q)}%"))
        if f.country:
            stmt = stmt.where(Place.country_code == f.country)
        if f.contact_status:
            stmt = stmt.where(Place.contact_status == f.contact_status)
        if f.relevance:
            # `weak` phải LOẠI luôn NULL chứ không chỉ so bằng: NULL là "chưa chấm
            # được" (job không khai danh mục ngành nghề), hoàn toàn khác "đã chấm
            # và thấy lạc đề". Gộp hai thứ lại là đẩy dữ liệu sạch vào ô nghi ngờ.
            stmt = stmt.where(Place.relevance == f.relevance)
        else:
            # KHÔNG lọc gì = vẫn phải giấu những dòng ĐÃ CHẤM và thấy lạc đề.
            #
            # `weak` chỉ xuất hiện ở địa điểm ĐÃ NẰM SẴN trong bảng từ trước khi
            # có bộ lọc: thẻ mới lạc đề thì bị chặn ngay lúc ghi, không bao giờ
            # vào tới đây. Với dòng cũ thì `upsert_from_card` cố ý không xoá (xoá
            # là để job này quyết thay job khác), nên nếu đường đọc cũng không
            # giấu thì chúng nằm lại trong bảng vĩnh viễn — đúng con tiệm bánh
            # kem mà người dùng chỉ ra.
            #
            # Giấu chứ KHÔNG xoá: muốn soi lại thì `?relevance=weak` vẫn ra đủ.
            # NULL ("chưa chấm được") vẫn hiện bình thường — nó khác hẳn "đã chấm
            # và thấy lạc đề".
            stmt = stmt.where(
                or_(Place.relevance.is_(None), Place.relevance != NGHI_RAC)
            )
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
        """Sắp xếp theo ĐÚNG hình dạng của chỉ mục, nếu không chỉ mục thành vô dụng.

        Postgres chỉ dùng được chỉ mục để sắp xếp khi thứ tự khai của chỉ mục
        TRÙNG KHÍT với `ORDER BY` — kể cả hướng của khoá phụ và vị trí của NULL.
        Lệch một chi tiết là nó quay về sắp xếp toàn bảng, im lặng, không lỗi.

        Đo trên 100.000 dòng, đúng câu giao diện gọi mỗi lần mở trang Địa điểm:

            liveness_score DESC NULLS LAST, id       44,9 ms   sắp toàn bảng
            liveness_score DESC,            id DESC   0,73 ms  dùng chỉ mục
            rating DESC NULLS LAST,         id DESC   0,24 ms  dùng chỉ mục
            rating DESC NULLS LAST,         id       15,9 ms   sắp lại một phần

        Hai chỗ phải khớp:

        1. KHOÁ PHỤ `id` phải DESC. Mọi chỉ mục đều khai `(cột DESC, id DESC)`;
           dùng `id` tăng dần là lệch ngay. Hướng của khoá phụ không ảnh hưởng
           nghiệp vụ — nó chỉ cần xác định để hai lần tải cùng một trang ra cùng
           thứ tự — nên chọn theo chỉ mục là lựa chọn miễn phí.

        2. `NULLS LAST` CHỈ đặt cho cột CÓ THỂ NULL. `DESC` trong Postgres ngầm
           là NULLS FIRST, nên chỉ mục `(liveness_score DESC)` không khớp với
           `DESC NULLS LAST`. Với cột NOT NULL thì `NULLS LAST` chẳng đổi kết
           quả gì — nó chỉ phá mất chỉ mục.
        """
        desc = sort.startswith("-")
        ten = sort.lstrip("-")
        column = SORTABLE.get(ten, Place.liveness_score)
        # Lấy tính NULL từ chính định nghĩa bảng thay vì chép tay một danh sách:
        # đổi cột thành nullable mà quên sửa ở đây là mất chỉ mục, không báo lỗi.
        co_the_null = Place.__table__.c[column.key].nullable
        if desc:
            huong = column.desc().nullslast() if co_the_null else column.desc()
        else:
            huong = column.asc().nullsfirst() if co_the_null else column.asc()
        return stmt.order_by(huong, Place.id.desc())

    @staticmethod
    def _can_khu_trung(f: PlaceFilter) -> bool:
        """Bộ lọc này có sinh ra JOIN làm nhân đôi dòng không.

        `DISTINCT` CHỈ cần khi có nối bảng: lọc theo job nối `job_places`, lọc
        theo lượt tìm nối `place_keywords` — một địa điểm nằm trong nhiều job
        hoặc ra từ nhiều lượt tìm sẽ hiện thành nhiều dòng. Không nối thì khoá
        chính đã bảo đảm mỗi địa điểm đúng một dòng, và `DISTINCT` lúc đó là
        phần việc thừa mà Postgres vẫn phải làm thật.

        Đo trên 100.000 dòng, đúng những câu giao diện gọi mỗi lần mở trang:
            SELECT DISTINCT *   93 ms  ->  bỏ DISTINCT   44 ms   (nhanh 2,1 lần)
            count(DISTINCT id)  40 ms  ->  count(*)       9 ms   (nhanh 4,5 lần)

        Mở một trang gọi cả hai, nên chỉ riêng chỗ này tiết kiệm ~80 ms mỗi lượt
        — và mỗi sale mở trang cả trăm lượt một ngày.
        """
        return f.job_id is not None or bool(f.keyword)

    def count(self, f: PlaceFilter) -> int:
        dem = func.count(func.distinct(Place.id)) if self._can_khu_trung(f) else func.count()
        return int(self.db.execute(self._apply(select(dem), f)).scalar_one())

    def page(self, f: PlaceFilter, params: PageParams) -> list[Place]:
        stmt = self._order(self._apply(self._chon(f), f), f.sort)
        return list(self.db.execute(stmt.offset(params.offset).limit(params.size)).scalars().all())

    def stream(self, f: PlaceFilter, chunk: int = 1000) -> Iterator[Place]:
        """Duyệt theo lô cho việc xuất file — không nạp cả trăm nghìn dòng vào RAM."""
        stmt = self._order(self._apply(self._chon(f), f), f.sort)
        result = self.db.execute(stmt.execution_options(stream_results=True, yield_per=chunk))
        yield from result.scalars()

    def _chon(self, f: PlaceFilter) -> Select:
        stmt = select(Place)
        return stmt.distinct() if self._can_khu_trung(f) else stmt

    def pulse(self) -> tuple[int | None, datetime | None]:
        """Hai con số đủ để biết bảng địa điểm có gì mới chưa.

        CỐ Ý KHÔNG đếm `count(*)`. Postgres bắt buộc quét toàn bảng cho phép đếm
        đó dù có chỉ mục hay không, và nó chính là toàn bộ chi phí của câu này:
        đo ở 300.000 dòng là 70ms có đếm, 2,4ms khi bỏ đếm và có chỉ mục.
        Mà con số đó KHÔNG AI DÙNG — giao diện chỉ lấy gói tin làm tín hiệu "có
        thay đổi" rồi tự nạp lại danh sách (xem use-place-events.ts).

          * `max(id)`      — có thêm địa điểm mới không
          * mốc đổi gần nhất — bắt cả trường hợp CẬP NHẬT bản ghi cũ (bổ sung
            website, chấm lại điểm sống/chết), thứ mà `max(id)` không thấy.
            Phải lấy CẢ BA cột: pha tìm kiếm chỉ ghi `last_seen_at`, còn pha chi
            tiết — pha DÀI NHẤT — chỉ ghi `last_verified_at`/`website_checked_at`.
            Chỉ nhìn `last_seen_at` thì bảng đứng im suốt pha đó.

        Giới hạn đã biết: XOÁ một địa điểm không làm đổi hai con số này. Chấp
        nhận được vì ứng dụng không có đường nào xoá địa điểm.
        """
        row = self.db.execute(
            select(
                func.max(Place.id),
                func.greatest(
                    func.max(Place.last_seen_at),
                    func.max(Place.last_verified_at),
                    func.max(Place.website_checked_at),
                ),
            )
        ).one()
        return row[0], row[1]

    def query_counts(self) -> list[tuple[str, int]]:
        """(chuỗi truy vấn, số địa điểm) cho MỌI lượt tìm đã có dữ liệu."""
        return [(kw, int(n)) for kw, n in self.db.execute(QUERY_COUNTS).all()]

    def country_counts(self) -> list[tuple[str, int]]:
        """(mã quốc gia, số địa điểm) cho những nước THẬT SỰ có dữ liệu."""
        return [(code, int(n)) for code, n in self.db.execute(COUNTRY_COUNTS).all()]

    def by_id(self, place_id: int) -> Place | None:
        return self.db.execute(select(Place).where(Place.id == place_id)).scalar_one_or_none()

    def keywords_for(self, ids: Iterable[int]) -> dict[int, list[str]]:
        """Từ khoá của từng địa điểm, tra theo LÔ (xem `KEYWORD_BATCH`).

        Chia lô nằm ở đây chứ không ở chỗ gọi: cả màn danh sách, màn chi tiết lẫn
        API xuất file đều đi qua hàm này, vá riêng một nơi thì hai nơi kia vẫn ôm
        nguyên quả bom chờ tới ngày dữ liệu đủ lớn mới nổ.

        Cắt lô theo place_id nên MỌI từ khoá của một địa điểm luôn nằm gọn trong
        cùng một lô — thứ tự `ORDER BY keyword` của từng danh sách vì thế không đổi
        so với lúc chạy một câu duy nhất.
        """
        ids = list(ids)
        out: dict[int, list[str]] = {i: [] for i in ids}
        for dau in range(0, len(ids), KEYWORD_BATCH):
            rows = self.db.execute(
                select(PlaceKeyword.place_id, PlaceKeyword.keyword)
                .where(PlaceKeyword.place_id.in_(ids[dau : dau + KEYWORD_BATCH]))
                .order_by(PlaceKeyword.keyword)
            ).all()
            for place_id, keyword in rows:
                out.setdefault(place_id, []).append(keyword)
        return out
